from __future__ import annotations

import io
import re
import uuid
from datetime import datetime
from typing import List

from pdf2image import convert_from_bytes
from pypdf import PdfReader
import pytesseract

from .schemas import (
    CompanyInfo,
    DocumentType,
    ExtractedDocument,
    ItemInfo,
    SignerInfo,
)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text_chunks: List[str] = []

    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            text_chunks.append(page_text)

    direct_text = "\n".join(text_chunks).strip()
    if len(direct_text) > 200:
        return direct_text

    images = convert_from_bytes(pdf_bytes, dpi=250)
    ocr_text = []
    for image in images:
        ocr_text.append(pytesseract.image_to_string(image, lang="rus+eng"))
    return "\n".join(ocr_text)


def detect_document_type(text: str) -> DocumentType:
    normalized = text.lower()
    if "универсаль" in normalized and "передаточ" in normalized:
        return DocumentType.UPD
    if "товарная накладная" in normalized or "торг-12" in normalized:
        return DocumentType.TORG12
    if "акт" in normalized and "оказан" in normalized and "услуг" in normalized:
        return DocumentType.SERVICE_ACT
    return DocumentType.UNKNOWN


def _first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def extract_fields(filename: str, text: str) -> ExtractedDocument:
    doc_type = detect_document_type(text)

    supplier = CompanyInfo(
        name=_first(r"(?:поставщик|исполнитель)\s*[:\-]\s*(.+)", text),
        inn=_first(r"(?:инн\s*(?:поставщика|исполнителя)?\s*[:\-]?\s*)(\d{10,12})", text),
        kpp=_first(r"(?:кпп\s*(?:поставщика|исполнителя)?\s*[:\-]?\s*)(\d{9})", text),
    )

    buyer = CompanyInfo(
        name=_first(r"(?:покупатель|заказчик)\s*[:\-]\s*(.+)", text),
        inn=_first(r"(?:инн\s*(?:покупателя|заказчика)?\s*[:\-]?\s*)(\d{10,12})", text),
        kpp=_first(r"(?:кпп\s*(?:покупателя|заказчика)?\s*[:\-]?\s*)(\d{9})", text),
    )

    items = []
    for line in text.splitlines():
        if re.search(r"\d+[\.,]?\d*\s*x\s*\d+[\.,]?\d*", line, flags=re.IGNORECASE):
            items.append(ItemInfo(name=line.strip()))

    signers = []
    signer_match = re.findall(
        r"(?:руководитель|директор|главный бухгалтер|подписал[аи]?)\s*[:\-]?\s*([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)",
        text,
    )
    for full_name in signer_match:
        signers.append(SignerInfo(full_name=full_name))

    return ExtractedDocument(
        id=str(uuid.uuid4()),
        filename=filename,
        document_type=doc_type,
        supplier=supplier,
        buyer=buyer,
        items=items[:200],
        signers=signers[:50],
        raw_text_excerpt=text[:3000],
        created_at=datetime.utcnow(),
    )
