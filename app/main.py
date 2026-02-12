from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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
from .schemas import DocumentUrlUpload, ExtractedDocument, TrainingSample, TrainingSampleCreate

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


def _resolve_filename(source_name: str | None, url: str) -> str:
    if source_name and source_name.strip():
        name = source_name.strip()
    else:
        parsed = urlparse(url)
        name = Path(parsed.path).name or "uploaded.pdf"

    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def _extract_yandex_public_pdf(public_url: str) -> tuple[bytes, str]:
    meta_url = (
        "https://cloud-api.yandex.net/v1/disk/public/resources/download"
        f"?public_key={public_url}"
    )
    req = Request(meta_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    href = payload.get("href")
    if not href:
        raise HTTPException(status_code=422, detail="Не удалось получить download-ссылку Yandex.Disk")

    with urlopen(Request(href, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as file_response:
        pdf_bytes = file_response.read()

    parsed_name = _resolve_filename(payload.get("filename"), public_url)
    return pdf_bytes, parsed_name


def _process_pdf(filename: str, pdf_bytes: bytes) -> ExtractedDocument:
    extraction = extract_text_from_pdf(pdf_bytes)
    text = extraction.text

    if not text.strip():
        diagnostics = " ".join(extraction.diagnostics)
        raise HTTPException(
            status_code=422,
            detail=(
                "Не удалось извлечь текст из PDF. "
                "Документ может быть сканом без текстового слоя. "
                "Проверьте OCR-зависимости (tesseract; poppler или pypdfium2). "
                f"Диагностика: {diagnostics}"
            ),
        )

    base_doc = extract_fields(filename=filename, text=text)
    enriched = provider.enrich(base_doc)
    enriched = apply_learning_hints(enriched, list_training_samples())

    save_document(enriched)
    return enriched


@app.post("/documents/upload", response_model=ExtractedDocument)
async def upload_document(file: UploadFile = File(...)) -> ExtractedDocument:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Поддерживаются только PDF файлы")

    pdf_bytes = await file.read()
    return _process_pdf(file.filename, pdf_bytes)


@app.post("/documents/upload-by-url", response_model=ExtractedDocument)
def upload_document_by_url(payload: DocumentUrlUpload) -> ExtractedDocument:
    url = payload.url.strip()

    if "disk.yandex.ru" in url:
        try:
            pdf_bytes, default_name = _extract_yandex_public_pdf(url)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Ошибка скачивания с Yandex.Disk: {exc}") from exc
        filename = _resolve_filename(payload.filename or default_name, url)
        return _process_pdf(filename, pdf_bytes)

    raise HTTPException(
        status_code=400,
        detail="Сейчас поддерживается URL Yandex.Disk (public link).",
    )


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
            "1) Загружайте PDF в /documents/upload или ссылкой через /documents/upload-by-url.",
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
