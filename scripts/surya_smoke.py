"""Single-page Surya OCR smoke check.

This script is intentionally stricter than ad-hoc ``python -c`` snippets:
``status=failed`` is a non-zero process exit so OCR failures are not mistaken
for a passing smoke test.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import fitz

import ocr_surya


def _default_pdf_for_book(book: str) -> Path:
    input_dir = Path("books") / book / "input"
    matches = sorted(input_dir.glob("*.pdf"))
    if not matches:
        raise FileNotFoundError(f"No PDF found under {input_dir}")
    return matches[0]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Surya OCR on one PDF page.")
    parser.add_argument("--book", default="game-model-test", help="Book id under books/")
    parser.add_argument("--pdf", help="PDF path; overrides --book")
    parser.add_argument("--page", type=int, default=1, help="1-based page number")
    parser.add_argument(
        "--keep-alive",
        action="store_true",
        help="Keep the llama.cpp server alive for the next command.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.keep_alive:
        os.environ["SURYA_INFERENCE_KEEP_ALIVE"] = "true"

    pdf_path = Path(args.pdf) if args.pdf else _default_pdf_for_book(args.book)
    doc = fitz.open(str(pdf_path))
    try:
        if args.page < 1 or args.page > doc.page_count:
            raise ValueError(f"--page must be between 1 and {doc.page_count}")
        page = doc[args.page - 1]
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / f"surya-smoke-p{args.page}.png"
            page.get_pixmap(matrix=fitz.Matrix(2, 2)).save(str(image_path))
            result = ocr_surya.recognize_page_image_with_retry(image_path)
    finally:
        doc.close()

    summary = {
        "pdf": str(pdf_path),
        "page": args.page,
        "status": result.get("status"),
        "risk_flags": result.get("risk_flags", []),
        "error": result.get("error", ""),
        "blocks": len(result.get("blocks", [])),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "ok" and summary["blocks"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
