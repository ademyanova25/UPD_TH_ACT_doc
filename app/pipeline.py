from __future__ import annotations

import io
import re
import uuid
from collections import Counter
from datetime import datetime
from dataclasses import dataclass
from typing import List

from pypdf import PdfReader

from .schemas import (
    CompanyInfo,
    DocumentType,
    ExtractedDocument,
    ItemInfo,
    SignerInfo,
    TrainingSample,
)


@dataclass
class TextExtractionResult:
    text: str
    diagnostics: list[str]


def _extract_text_with_pypdf(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text_chunks: List[str] = []

    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            text_chunks.append(page_text)

    return "\n".join(text_chunks).strip()


def _extract_text_with_pdfminer(pdf_bytes: bytes) -> str:
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract_text
    except ModuleNotFoundError as exc:
        raise RuntimeError("pdfminer.six не установлен") from exc

    return (pdfminer_extract_text(io.BytesIO(pdf_bytes)) or "").strip()


def extract_text_from_pdf(pdf_bytes: bytes) -> TextExtractionResult:
    diagnostics: list[str] = []

    # 1) Текстовый слой через pypdf.
    try:
        direct_text = _extract_text_with_pypdf(pdf_bytes)
        if direct_text:
            diagnostics.append("Текст извлечён из PDF-слоя (pypdf).")
            return TextExtractionResult(text=direct_text, diagnostics=diagnostics)
        diagnostics.append("pypdf: текстовый слой не найден.")
    except Exception as exc:
        diagnostics.append(f"pypdf: ошибка чтения ({exc}).")

    # 2) Дополнительный fallback для PDF-слоя через pdfminer.
    try:
        alt_text = _extract_text_with_pdfminer(pdf_bytes)
        if alt_text:
            diagnostics.append("Текст извлечён из PDF-слоя (pdfminer).")
            return TextExtractionResult(text=alt_text, diagnostics=diagnostics)
        diagnostics.append("pdfminer: текстовый слой не найден.")
    except Exception as exc:
        diagnostics.append(f"pdfminer: ошибка чтения ({exc}).")

    # 3) OCR fallback для сканов.
    try:
        from pdf2image import convert_from_bytes
        from pdf2image.exceptions import PDFInfoNotInstalledError
        import pytesseract
        from pytesseract import TesseractNotFoundError
    except ModuleNotFoundError as exc:
        diagnostics.append(f"OCR-библиотеки Python не установлены ({exc}).")
        return TextExtractionResult(text="", diagnostics=diagnostics)

    try:
        images = convert_from_bytes(pdf_bytes, dpi=250)
        ocr_text = []
        for image in images:
            ocr_text.append(pytesseract.image_to_string(image, lang="rus+eng"))
        text = "\n".join(ocr_text).strip()
        if text:
            diagnostics.append("Текст извлечён OCR (tesseract).")
        else:
            diagnostics.append("OCR выполнен, но текст не найден.")
        return TextExtractionResult(text=text, diagnostics=diagnostics)
    except PDFInfoNotInstalledError:
        diagnostics.append("OCR недоступен: не найден poppler (pdfinfo).")
    except TesseractNotFoundError:
        diagnostics.append("OCR недоступен: не найден tesseract.")
    except Exception as exc:
        diagnostics.append(f"OCR: внутренняя ошибка ({exc}).")

    return TextExtractionResult(text="", diagnostics=diagnostics)


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


def _normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    return re.sub(r"\s+", " ", name).strip().lower()


def _best_value(values: list[str | None]) -> str | None:
    cleaned = [v.strip() for v in values if v and v.strip()]
    if not cleaned:
        return None
    return Counter(cleaned).most_common(1)[0][0]


def apply_learning_hints(doc: ExtractedDocument, samples: list[TrainingSample]) -> ExtractedDocument:
    supplier_hints: dict[str, CompanyInfo] = {}
    buyer_hints: dict[str, CompanyInfo] = {}

    grouped_supplier: dict[str, list[CompanyInfo]] = {}
    grouped_buyer: dict[str, list[CompanyInfo]] = {}

    for sample in samples:
        s_name = _normalize_name(sample.corrected.supplier.name)
        b_name = _normalize_name(sample.corrected.buyer.name)
        if s_name:
            grouped_supplier.setdefault(s_name, []).append(sample.corrected.supplier)
        if b_name:
            grouped_buyer.setdefault(b_name, []).append(sample.corrected.buyer)

    for name, rows in grouped_supplier.items():
        supplier_hints[name] = CompanyInfo(
            name=_best_value([x.name for x in rows]),
            inn=_best_value([x.inn for x in rows]),
            kpp=_best_value([x.kpp for x in rows]),
            address=_best_value([x.address for x in rows]),
        )
    for name, rows in grouped_buyer.items():
        buyer_hints[name] = CompanyInfo(
            name=_best_value([x.name for x in rows]),
            inn=_best_value([x.inn for x in rows]),
            kpp=_best_value([x.kpp for x in rows]),
            address=_best_value([x.address for x in rows]),
        )

    supplier_key = _normalize_name(doc.supplier.name)
    buyer_key = _normalize_name(doc.buyer.name)

    if supplier_key and supplier_key in supplier_hints:
        hint = supplier_hints[supplier_key]
        if not doc.supplier.inn:
            doc.supplier.inn = hint.inn
        if not doc.supplier.kpp:
            doc.supplier.kpp = hint.kpp
        if not doc.supplier.address:
            doc.supplier.address = hint.address
    if buyer_key and buyer_key in buyer_hints:
        hint = buyer_hints[buyer_key]
        if not doc.buyer.inn:
            doc.buyer.inn = hint.inn
        if not doc.buyer.kpp:
            doc.buyer.kpp = hint.kpp
        if not doc.buyer.address:
            doc.buyer.address = hint.address

    return doc


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
