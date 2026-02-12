from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

from openai import OpenAI

from .schemas import DocumentType, ExtractedDocument


class DocumentAIProvider(ABC):
    @abstractmethod
    def enrich(self, doc: ExtractedDocument) -> ExtractedDocument:
        raise NotImplementedError


class NoopProvider(DocumentAIProvider):
    def enrich(self, doc: ExtractedDocument) -> ExtractedDocument:
        return doc


class OpenAIExtractionProvider(DocumentAIProvider):
    def __init__(self, model: str = "gpt-4.1-mini") -> None:
        self.model = model
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def enrich(self, doc: ExtractedDocument) -> ExtractedDocument:
        if not os.getenv("OPENAI_API_KEY"):
            return doc

        schema_hint = {
            "document_type": [t.value for t in DocumentType],
            "supplier": {"name": "", "inn": "", "kpp": "", "address": ""},
            "buyer": {"name": "", "inn": "", "kpp": "", "address": ""},
            "items": [{"name": "", "quantity": 0, "unit_price": 0, "total_price": 0}],
            "signers": [{"role": "", "full_name": ""}],
        }

        prompt = (
            "Извлеки структурированные данные из текста первичного документа РФ. "
            "Верни только JSON строго по схеме. "
            f"Схема: {json.dumps(schema_hint, ensure_ascii=False)}\n\n"
            f"Текст:\n{doc.raw_text_excerpt}"
        )

        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            temperature=0,
        )

        raw = response.output_text.strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return doc

        patch = doc.model_dump()
        patch.update(parsed)
        return ExtractedDocument.model_validate(patch)
