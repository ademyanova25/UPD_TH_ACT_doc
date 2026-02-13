from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .schemas import ExtractedDocument, TrainingSample, TrainingSampleCreate


DB_PATH = Path("registry.sqlite3")


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS training_samples (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            source_document_id TEXT,
            raw_text_excerpt TEXT NOT NULL,
            predicted_payload TEXT NOT NULL,
            corrected_payload TEXT NOT NULL,
            reviewer_comment TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def save_document(doc: ExtractedDocument) -> None:
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO documents (id, filename, payload) VALUES (?, ?, ?)",
        (doc.id, doc.filename, doc.model_dump_json()),
    )
    conn.commit()
    conn.close()


def list_documents() -> List[ExtractedDocument]:
    conn = _conn()
    rows = conn.execute("SELECT payload FROM documents ORDER BY rowid DESC").fetchall()
    conn.close()
    return [ExtractedDocument.model_validate(json.loads(row[0])) for row in rows]


def get_document(doc_id: str) -> Optional[ExtractedDocument]:
    conn = _conn()
    row = conn.execute("SELECT payload FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return ExtractedDocument.model_validate(json.loads(row[0]))


def create_training_sample(sample: TrainingSampleCreate) -> TrainingSample:
    training_sample = TrainingSample(
        id=str(uuid.uuid4()),
        filename=sample.filename,
        source_document_id=sample.source_document_id,
        raw_text_excerpt=sample.raw_text_excerpt,
        predicted=sample.predicted,
        corrected=sample.corrected,
        reviewer_comment=sample.reviewer_comment,
        created_at=datetime.utcnow(),
    )

    conn = _conn()
    conn.execute(
        """
        INSERT INTO training_samples (
            id,
            filename,
            source_document_id,
            raw_text_excerpt,
            predicted_payload,
            corrected_payload,
            reviewer_comment,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            training_sample.id,
            training_sample.filename,
            training_sample.source_document_id,
            training_sample.raw_text_excerpt,
            training_sample.predicted.model_dump_json(),
            training_sample.corrected.model_dump_json(),
            training_sample.reviewer_comment,
            training_sample.created_at.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return training_sample


def list_training_samples() -> List[TrainingSample]:
    conn = _conn()
    rows = conn.execute(
        """
        SELECT id, filename, source_document_id, raw_text_excerpt, predicted_payload,
               corrected_payload, reviewer_comment, created_at
        FROM training_samples
        ORDER BY rowid DESC
        """
    ).fetchall()
    conn.close()

    out: List[TrainingSample] = []
    for row in rows:
        out.append(
            TrainingSample(
                id=row[0],
                filename=row[1],
                source_document_id=row[2],
                raw_text_excerpt=row[3],
                predicted=ExtractedDocument.model_validate(json.loads(row[4])),
                corrected=ExtractedDocument.model_validate(json.loads(row[5])),
                reviewer_comment=row[6],
                created_at=datetime.fromisoformat(row[7]),
            )
        )
    return out


def export_training_jsonl() -> str:
    samples = list_training_samples()
    lines = []
    for s in samples:
        row = {
            "id": s.id,
            "filename": s.filename,
            "source_document_id": s.source_document_id,
            "input": s.raw_text_excerpt,
            "predicted": s.predicted.model_dump(mode="json"),
            "corrected": s.corrected.model_dump(mode="json"),
            "reviewer_comment": s.reviewer_comment,
            "created_at": s.created_at.isoformat(),
        }
        lines.append(json.dumps(row, ensure_ascii=False))
    return "\n".join(lines)
