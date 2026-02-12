from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .pipeline import apply_learning_hints, extract_fields, extract_text_from_pdf
from .providers import NoopProvider, OpenAIExtractionProvider
from .registry import (
    create_training_sample,
    export_training_jsonl,
    get_document,
    init_db,
    list_documents,
    list_training_samples,
    save_document,
)
from .schemas import ExtractedDocument, TrainingSample, TrainingSampleCreate

app = FastAPI(title="Primary Document Extractor", version="0.3.0")

provider = OpenAIExtractionProvider() if os.getenv("OPENAI_API_KEY") else NoopProvider()

static_dir = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/documents/upload", response_model=ExtractedDocument)
async def upload_document(file: UploadFile = File(...)) -> ExtractedDocument:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Поддерживаются только PDF файлы")

    pdf_bytes = await file.read()
    try:
        text = extract_text_from_pdf(pdf_bytes)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Ошибка чтения PDF/OCR: {exc}") from exc

    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "Не удалось извлечь текст из PDF. "
                "Проверьте, что файл не повреждён и в окружении установлены OCR-зависимости "
                "(poppler + tesseract)."
            ),
        )
    base_doc = extract_fields(filename=file.filename, text=text)
    enriched = provider.enrich(base_doc)

    # Лёгкое "онлайн-обучение": применяем накопленные правки пользователей
    enriched = apply_learning_hints(enriched, list_training_samples())

    save_document(enriched)
    return enriched


@app.put("/documents/{doc_id}", response_model=ExtractedDocument)
def update_document(doc_id: str, payload: ExtractedDocument) -> ExtractedDocument:
    if payload.id != doc_id:
        raise HTTPException(status_code=400, detail="ID в URL и body должны совпадать")
    save_document(payload)
    return payload


@app.get("/documents", response_model=list[ExtractedDocument])
def documents() -> list[ExtractedDocument]:
    return list_documents()


@app.get("/documents/{doc_id}", response_model=ExtractedDocument)
def document_by_id(doc_id: str) -> ExtractedDocument:
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc


@app.post("/training/samples", response_model=TrainingSample)
def add_training_sample(sample: TrainingSampleCreate) -> TrainingSample:
    return create_training_sample(sample)


@app.get("/training/samples", response_model=list[TrainingSample])
def training_samples() -> list[TrainingSample]:
    return list_training_samples()


@app.get("/training/export", response_class=PlainTextResponse)
def training_export() -> str:
    return export_training_jsonl()


@app.get("/training/howto")
def training_howto() -> dict:
    return {
        "steps": [
            "1) Загружайте PDF в /documents/upload.",
            "2) Проверяйте распознавание в UI и правьте поля.",
            "3) Нажимайте 'Сохранить правки': данные уйдут и в реестр, и в training-set.",
            "4) Следующие документы будут учитывать накопленные правки контрагентов.",
            "5) Периодически выгружайте датасет через /training/export (JSONL).",
        ],
        "auto_learning_note": (
            "В текущей версии это не full fine-tuning в реальном времени, а автоматическое "
            "применение накопленных исправлений (ИНН/КПП/адреса контрагентов) к новым документам."
        ),
        "target_schema": json.loads(
            '{"document_type":"УПД","supplier":{"name":"","inn":"","kpp":"","address":""},"buyer":{"name":"","inn":"","kpp":"","address":""},"items":[{"name":"","quantity":0,"unit_price":0,"total_price":0}],"signers":[{"role":"","full_name":""}]}'
        ),
    }
