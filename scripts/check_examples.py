from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline import extract_text_from_pdf


def main() -> int:
    examples_dir = Path("examples")
    pdfs = sorted(
        p for p in examples_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
    )

    if not pdfs:
        print("PDF files not found in examples/. Add a .pdf file and run again.")
        return 1

    for pdf_path in pdfs:
        print(f"\n=== {pdf_path.name} ===")
        result = extract_text_from_pdf(pdf_path.read_bytes())
        print(f"Extracted text length: {len(result.text)}")
        print("Diagnostics:")
        for row in result.diagnostics:
            print(f"- {row}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
