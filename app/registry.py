from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from .schemas import ExtractedDocument


DB_PATH = Path("registry.sqlite3")


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def save_document(doc: ExtractedDocument) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO documents (id, filename, payload) VALUES (?, ?, ?)",
        (doc.id, doc.filename, doc.model_dump_json()),
    )
    conn.commit()
    conn.close()


def list_documents() -> List[ExtractedDocument]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT payload FROM documents ORDER BY rowid DESC").fetchall()
    conn.close()
    return [ExtractedDocument.model_validate(json.loads(row[0])) for row in rows]


def get_document(doc_id: str) -> Optional[ExtractedDocument]:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT payload FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return ExtractedDocument.model_validate(json.loads(row[0]))
