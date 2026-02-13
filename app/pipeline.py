from __future__ import annotations

import io
import re
import uuid
from collections import Counter
from datetime import datetime
from dataclasses import dataclass
from typing import List

from pypdf import PdfReader
from PIL import ImageFilter, ImageOps

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


KEYWORDS = ["счет-фактура", "статус", "продавец", "покупатель", "инн", "кпп", "руб"]


def _normalize_ocr_text(value: str) -> str:
    normalized = value.lower()
    return (
        normalized.replace("o", "0")
        .replace("о", "0")
        .replace("|", "1")
        .replace("l", "1")
        .replace("i", "1")
    )


def _is_meaningful(text: str, min_chars: int = 50) -> bool:
    if not text:
        return False
    useful = re.sub(r"[^0-9A-Za-zА-Яа-я]+", "", text)
    return len(useful) >= min_chars


def _text_quality_score(value: str) -> int:
    # Эвристика качества OCR: учитываем реквизиты/маркеры первички, а не только объём текста.
    normalized = _normalize_ocr_text(re.sub(r"\s+", " ", value))
    base = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]", value))

    inn_count = len(re.findall(r"\b\d{10}\b|\b\d{12}\b", normalized))
    kpp_count = len(re.findall(r"\b\d{9}\b", normalized))
    keyword_hits = sum(1 for marker in KEYWORDS if marker in normalized)

    return base + (10 * inn_count) + (10 * kpp_count) + (3 * keyword_hits)


def _build_preprocessed_variants(image):
    gray = ImageOps.grayscale(image)
    autocontrast = ImageOps.autocontrast(gray)

    # Бинаризация помогает для бледных сканов/печати.
    threshold = autocontrast.point(lambda x: 255 if x > 160 else 0, mode="1").convert("L")

    # Лёгкое повышение резкости для мелкого шрифта.
    sharpened = autocontrast.filter(ImageFilter.SHARPEN)

    return {
        "raw": image,
        "gray": gray,
        "autocontrast": autocontrast,
        "threshold": threshold,
        "sharpened": sharpened,
    }


def _ocr_with_auto_rotate(image, pytesseract_module) -> tuple[str, str, dict[str, int]]:
    candidates: dict[str, object] = {"0": image}

    # 1) Пробуем определить угол автоматически (OSD).
    try:
        osd = pytesseract_module.image_to_osd(image)
        rotate_match = re.search(r"Rotate:\s*(\d+)", osd)
        if rotate_match:
            detected_angle = int(rotate_match.group(1)) % 360
            if detected_angle:
                corrected = image.rotate(360 - detected_angle, expand=True)
                candidates[f"osd_{360 - detected_angle}"] = corrected
    except Exception:
        # OSD может не отработать на шумных/маленьких страницах — это не критично.
        pass

    # 2) Явно проверяем типовые повороты для документов, загруженных "боком".
    for angle in (90, 180, 270):
        candidates[str(angle)] = image.rotate(angle, expand=True)

    best_text = ""
    best_variant = "0/raw"
    best_score = -1
    rotation_scores: dict[str, int] = {"0": 0, "90": 0, "180": 0, "270": 0}

    for variant, candidate in candidates.items():
        local_best = -1
        for prep_name, prepared in _build_preprocessed_variants(candidate).items():
            current_text = pytesseract_module.image_to_string(
                prepared,
                lang="rus+eng",
                config="--oem 1 --psm 6",
            )
            score = _text_quality_score(current_text)
            local_best = max(local_best, score)
            if score > best_score:
                best_text = current_text
                best_variant = f"{variant}/{prep_name}"
                best_score = score

        angle_key = variant.replace("osd_", "")
        if angle_key in rotation_scores and local_best > rotation_scores[angle_key]:
            rotation_scores[angle_key] = local_best

    return best_text.strip(), best_variant, rotation_scores

def _ocr_text_from_images(images: list[object], pytesseract_module) -> tuple[str, list[str], list[dict[str, int]]]:
    ocr_text: list[str] = []
    variants_used: list[str] = []
    rotation_scores: list[dict[str, int]] = []

    for image in images:
        page_text, used_variant, page_scores = _ocr_with_auto_rotate(image, pytesseract_module)
        ocr_text.append(page_text)
        variants_used.append(used_variant)
        rotation_scores.append(page_scores)

    return "\n".join(ocr_text).strip(), variants_used, rotation_scores


def _ocr_with_pdfium_renderer(pdf_bytes: bytes, pytesseract_module) -> tuple[str, list[str], list[dict[str, int]]]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    images: list[object] = []

    for page_index in range(len(document)):
        page = document.get_page(page_index)
        try:
            rendered = page.render(scale=250 / 72)
            images.append(rendered.to_pil())
        finally:
            page.close()

    return _ocr_text_from_images(images, pytesseract_module)


def extract_text_from_pdf(pdf_bytes: bytes) -> TextExtractionResult:
    diagnostics: list[str] = []

    # 1) Текстовый слой через pypdf.
    try:
        direct_text = _extract_text_with_pypdf(pdf_bytes)
        diagnostics.append(f"text_extraction.pypdf.len={len(direct_text)}")
        if _is_meaningful(direct_text):
            diagnostics.append("Текст извлечён из PDF-слоя (pypdf).")
            return TextExtractionResult(text=direct_text, diagnostics=diagnostics)
        diagnostics.append("pypdf: текст отсутствует или недостаточно осмысленный -> fallback.")
    except Exception as exc:
        diagnostics.append(f"pypdf: ошибка чтения ({exc}).")

    # 2) Дополнительный fallback для PDF-слоя через pdfminer.
    try:
        alt_text = _extract_text_with_pdfminer(pdf_bytes)
        diagnostics.append(f"text_extraction.pdfminer.len={len(alt_text)}")
        if _is_meaningful(alt_text):
            diagnostics.append("Текст извлечён из PDF-слоя (pdfminer).")
            return TextExtractionResult(text=alt_text, diagnostics=diagnostics)
        diagnostics.append("pdfminer: текст отсутствует или недостаточно осмысленный -> OCR.")
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
        text, variants_used, rotation_scores = _ocr_text_from_images(images, pytesseract)
        diagnostics.append("ocr.used=true")
        diagnostics.append(f"ocr.rotation_chosen={','.join(variants_used)}")
        diagnostics.append(f"ocr.rotation_scores={rotation_scores}")
        diagnostics.append(f"ocr.normalized_excerpt={_normalize_ocr_text(text)[:240]}")
        if _is_meaningful(text, min_chars=30):
            diagnostics.append(
                "Текст извлечён OCR (tesseract, poppler) с автоповоротом/предобработкой и оценкой ориентаций."
            )
        else:
            diagnostics.append(
                "OCR через poppler выполнен (включая автоповорот/предобработку), но осмысленный текст не найден."
            )
        return TextExtractionResult(text=text, diagnostics=diagnostics)
    except PDFInfoNotInstalledError:
        diagnostics.append("OCR/poppler недоступен: не найден poppler (pdfinfo). Пробуем встроенный рендерер.")
        try:
            text, variants_used, rotation_scores = _ocr_with_pdfium_renderer(pdf_bytes, pytesseract)
            diagnostics.append("ocr.used=true")
            diagnostics.append(f"ocr.rotation_chosen={','.join(variants_used)}")
            diagnostics.append(f"ocr.rotation_scores={rotation_scores}")
            diagnostics.append(f"ocr.normalized_excerpt={_normalize_ocr_text(text)[:240]}")
            if _is_meaningful(text, min_chars=30):
                diagnostics.append(
                    "Текст извлечён OCR (tesseract, pypdfium2) с автоповоротом/предобработкой и оценкой ориентаций."
                )
            else:
                diagnostics.append(
                    "OCR через pypdfium2 выполнен (включая автоповорот/предобработку), но осмысленный текст не найден."
                )
            return TextExtractionResult(text=text, diagnostics=diagnostics)
        except ModuleNotFoundError as exc:
            diagnostics.append(f"Встроенный PDF-рендерер не установлен ({exc}).")
        except Exception as exc:
            diagnostics.append(f"OCR/pypdfium2: внутренняя ошибка ({exc}).")
    except TesseractNotFoundError:
        diagnostics.append("OCR недоступен: не найден tesseract.")
    except Exception as exc:
        diagnostics.append(f"OCR: внутренняя ошибка ({exc}).")

    return TextExtractionResult(text="", diagnostics=diagnostics)


def detect_document_type(text: str) -> DocumentType:
    normalized = re.sub(r"\s+", " ", text.lower())

    upd_score = 0
    if re.search(r"универсал\w*\s+передаточ", normalized):
        upd_score += 3
    if re.search(r"передаточн\w*\s+документ", normalized):
        upd_score += 2
    if (
        re.search(r"счет\s*[-–—]?\s*фактур", normalized)
        and re.search(r"статус\s*[:№n]?\s*[12]", normalized)
        and re.search(r"передаточ", normalized)
    ):
        upd_score += 4
    if re.search(r"счет\s*[-–—]?\s*фактур", normalized):
        upd_score += 2
    if re.search(r"статус\s*[:№n]?\s*[12]", normalized):
        upd_score += 1
    if re.search(r"продавец|покупатель", normalized):
        upd_score += 1

    torg_score = 0
    if "торг-12" in normalized or "товарная накладная" in normalized:
        torg_score += 3
    if re.search(r"грузоотправител|грузополучател", normalized):
        torg_score += 1

    act_score = 0
    if re.search(r"акт", normalized):
        act_score += 1
    if re.search(r"оказан\w*\s+услуг", normalized):
        act_score += 2

    best = max(upd_score, torg_score, act_score)
    if best == 0:
        return DocumentType.UNKNOWN
    if best == upd_score:
        return DocumentType.UPD
    if best == torg_score:
        return DocumentType.TORG12
    return DocumentType.SERVICE_ACT


def _first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def _extract_inn_kpp_pair(text: str, role_pattern: str) -> tuple[str | None, str | None]:
    pair = re.search(
        rf"{role_pattern}.{{0,80}}?инн\s*/\s*кпп\s*([0-9]{{10,12}})\s*/\s*([0-9]{{9}})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if pair:
        return pair.group(1), pair.group(2)
    return None, None


def _extract_items(text: str) -> list[ItemInfo]:
    items: list[ItemInfo] = []
    for line in text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip(" |;	")
        if len(clean) < 8:
            continue

        numbers = re.findall(r"\d+[\.,]?\d*", clean)
        # Строка табличной части обычно содержит и текст, и несколько чисел (кол-во/цена/сумма).
        if len(numbers) >= 2 and re.search(r"[А-Яа-яA-Za-z]", clean):
            # Отсеиваем очевидные шапки/служебные фразы.
            if re.search(r"(инн|кпп|счет|договор|дата|страниц|лист)", clean, flags=re.IGNORECASE):
                continue
            items.append(ItemInfo(name=clean))

    return items[:200]


def _extract_signers(text: str) -> list[SignerInfo]:
    signers: list[SignerInfo] = []
    signer_match = re.findall(
        r"(?:руководитель|директор|главный бухгалтер|подписал[аи]?|ответственный)\s*[:\-]?\s*([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)",
        text,
    )
    for full_name in signer_match:
        signers.append(SignerInfo(full_name=full_name))
    return signers[:50]


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

    supplier_name = _first(r"(?:поставщик|исполнитель|продавец)\s*[:\-]?\s*(.+)", text)
    buyer_name = _first(r"(?:покупатель|заказчик)\s*[:\-]?\s*(.+)", text)

    supplier_inn, supplier_kpp = _extract_inn_kpp_pair(text, r"(?:продавец|поставщик|исполнитель)")
    buyer_inn, buyer_kpp = _extract_inn_kpp_pair(text, r"(?:покупатель|заказчик)")

    if not supplier_inn:
        supplier_inn = _first(r"(?:инн\s*(?:продавца|поставщика|исполнителя)?\s*[:\-]?\s*)(\d{10,12})", text)
    if not supplier_kpp:
        supplier_kpp = _first(r"(?:кпп\s*(?:продавца|поставщика|исполнителя)?\s*[:\-]?\s*)(\d{9})", text)
    if not buyer_inn:
        buyer_inn = _first(r"(?:инн\s*(?:покупателя|заказчика)?\s*[:\-]?\s*)(\d{10,12})", text)
    if not buyer_kpp:
        buyer_kpp = _first(r"(?:кпп\s*(?:покупателя|заказчика)?\s*[:\-]?\s*)(\d{9})", text)

    supplier = CompanyInfo(name=supplier_name, inn=supplier_inn, kpp=supplier_kpp)
    buyer = CompanyInfo(name=buyer_name, inn=buyer_inn, kpp=buyer_kpp)

    items = _extract_items(text)
    signers = _extract_signers(text)

    return ExtractedDocument(
        id=str(uuid.uuid4()),
        filename=filename,
        document_type=doc_type,
        supplier=supplier,
        buyer=buyer,
        items=items,
        signers=signers,
        raw_text_excerpt=text[:3000],
        created_at=datetime.utcnow(),
    )
