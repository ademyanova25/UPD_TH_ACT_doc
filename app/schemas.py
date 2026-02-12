from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    UPD = "УПД"
    TORG12 = "ТОРГ-12"
    SERVICE_ACT = "Акт оказания услуг"
    UNKNOWN = "Неизвестно"


class CompanyInfo(BaseModel):
    name: Optional[str] = None
    inn: Optional[str] = None
    kpp: Optional[str] = None
    address: Optional[str] = None


class ItemInfo(BaseModel):
    name: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    total_price: Optional[float] = None


class SignerInfo(BaseModel):
    role: Optional[str] = None
    full_name: Optional[str] = None


class ExtractedDocument(BaseModel):
    id: str
    filename: str
    document_type: DocumentType = DocumentType.UNKNOWN
    supplier: CompanyInfo = Field(default_factory=CompanyInfo)
    buyer: CompanyInfo = Field(default_factory=CompanyInfo)
    items: List[ItemInfo] = Field(default_factory=list)
    signers: List[SignerInfo] = Field(default_factory=list)
    raw_text_excerpt: str = ""
    created_at: datetime
