from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile

from .pipeline import extract_fields, extract_text_from_pdf
from .providers import NoopProvider, OpenAIExtractionProvider
from .registry import get_document, init_db, list_documents, save_document
from .schemas import ExtractedDocument

app = FastAPI(title="Primary Document Extractor", version="0.1.0")

provider = OpenAIExtractionProvider() if __import__("os").getenv("OPENAI_API_KEY") else NoopProvider()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/documents/upload", response_model=ExtractedDocument)
async def upload_document(file: UploadFile = File(...)) -> ExtractedDocument:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Поддерживаются только PDF файлы")

    pdf_bytes = await file.read()
    text = extract_text_from_pdf(pdf_bytes)
    base_doc = extract_fields(filename=file.filename, text=text)
    enriched = provider.enrich(base_doc)
    save_document(enriched)
    return enriched


@app.get("/documents", response_model=list[ExtractedDocument])
def documents() -> list[ExtractedDocument]:
    return list_documents()


@app.get("/documents/{doc_id}", response_model=ExtractedDocument)
def document_by_id(doc_id: str) -> ExtractedDocument:
    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc
