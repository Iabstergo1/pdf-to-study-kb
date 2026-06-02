import json
import sys
from pathlib import Path

import fitz


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _make_context_book(tmp_path):
    book_root = tmp_path / "books" / "phase5-book"
    input_dir = book_root / "input"
    input_dir.mkdir(parents=True)
    (book_root / "pipeline-workspace" / "staging").mkdir(parents=True)

    pdf_path = input_dir / "sample.pdf"
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Plain page\nThis page has normal explanatory text.")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Formula page\n∑ payoff_i = p_i * v_i")
    doc.save(str(pdf_path))
    doc.close()

    pdf_profile = {
        "total_pages": 2,
        "pages": [
            {
                "page": 1,
                "text_length": 45,
                "formula_risk": "low",
                "table_risk": "low",
                "blank_variable_risk": "low",
            },
            {
                "page": 2,
                "text_length": 32,
                "formula_risk": "high",
                "table_risk": "low",
                "blank_variable_risk": "low",
            },
        ],
    }
    return book_root, pdf_profile


def test_hybrid_ocrs_high_formula_pages_and_writes_preview(monkeypatch, tmp_path):
    import ocr_surya
    from unit_context import prepare_unit_context

    book_root, pdf_profile = _make_context_book(tmp_path)
    calls = []

    def fake_recognize(_image_path):
        calls.append(_image_path)
        return {
            "status": "ok",
            "blocks": [
                {
                    "text": "OCR formula",
                    "html": "<math>\\sum_i p_i v_i</math>",
                    "bbox": [0, 0, 10, 10],
                }
            ],
        }

    monkeypatch.setattr(ocr_surya, "recognize_page_image", fake_recognize)
    unit = {
        "unit_id": "U-001-01",
        "source_scope": {"pages": [1, 2]},
        "extraction_method": "hybrid",
    }

    context = prepare_unit_context(book_root, unit, pdf_profile)

    assert calls, "hybrid should OCR the high formula page"
    assert context["block_publish"] is False
    assert context["source_pages"] == [1, 2]
    assert any(block["page"] == 1 for block in context["text_blocks"])
    assert any(block["page"] == 2 for block in context["ocr_blocks"])
    assert "hybrid_conflict" in context["risk_flags"]

    preview_path = book_root / "pipeline-workspace" / "staging" / "U-001-01" / "context-preview.json"
    evidence_path = book_root / "pipeline-workspace" / "staging" / "U-001-01" / "evidence-index.jsonl"
    assert preview_path.exists()
    assert evidence_path.exists()
    assert json.loads(preview_path.read_text(encoding="utf-8"))["unit_id"] == "U-001-01"
    assert evidence_path.read_text(encoding="utf-8").strip()
    assert not list((book_root / "pipeline-workspace").glob("**/source-slice.md"))


def test_hybrid_uses_cached_ocr_result(monkeypatch, tmp_path):
    import ocr_surya
    from unit_context import prepare_unit_context

    book_root, pdf_profile = _make_context_book(tmp_path)
    cache_dir = book_root / "pipeline-workspace" / "ocr-cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "page-0002.json").write_text(
        json.dumps({
            "status": "ok",
            "blocks": [
                {
                    "text": "cached OCR formula",
                    "html": "<math>cached</math>",
                    "bbox": [0, 0, 10, 10],
                }
            ],
        }),
        encoding="utf-8",
    )

    def should_not_call(_image_path):
        raise AssertionError("cached OCR page should not call Surya")

    monkeypatch.setattr(ocr_surya, "recognize_page_image", should_not_call)
    unit = {
        "unit_id": "U-001-01",
        "source_scope": {"pages": [2]},
        "extraction_method": "hybrid",
    }

    context = prepare_unit_context(book_root, unit, pdf_profile)

    assert context["block_publish"] is False
    assert context["ocr_blocks"][0]["text_preview"] == "cached OCR formula"


def test_surya_unavailable_blocks_publish(monkeypatch, tmp_path):
    import ocr_surya
    from unit_context import prepare_unit_context

    book_root, pdf_profile = _make_context_book(tmp_path)

    def unavailable(_image_path):
        raise ocr_surya.OcrUnavailable("surya-ocr is not installed")

    monkeypatch.setattr(ocr_surya, "recognize_page_image", unavailable)
    unit = {
        "unit_id": "U-001-01",
        "source_scope": {"pages": [2]},
        "extraction_method": "screenshot_ocr",
    }

    context = prepare_unit_context(book_root, unit, pdf_profile)

    assert context["block_publish"] is True
    assert "ocr_unavailable" in context["risk_flags"]
    assert context["formula_risk"] == "high"


def test_ocr_failure_retries_once_then_blocks(monkeypatch, tmp_path):
    import ocr_surya
    from unit_context import prepare_unit_context

    book_root, pdf_profile = _make_context_book(tmp_path)
    calls = []

    def failing(_image_path):
        calls.append(_image_path)
        raise RuntimeError("backend failed")

    monkeypatch.setattr(ocr_surya, "recognize_page_image", failing)
    unit = {
        "unit_id": "U-001-01",
        "source_scope": {"pages": [2]},
        "extraction_method": "screenshot_ocr",
    }

    context = prepare_unit_context(book_root, unit, pdf_profile)

    assert len(calls) == 2
    assert context["block_publish"] is True
    assert "screenshot_ocr_failed" in context["risk_flags"]


def test_resolve_llama_cpp_binary_finds_winget_install(monkeypatch, tmp_path):
    import ocr_surya

    winget_root = tmp_path / "Microsoft" / "WinGet" / "Packages" / "ggml.llamacpp_test"
    winget_root.mkdir(parents=True)
    binary = winget_root / "llama-server.exe"
    binary.write_text("", encoding="utf-8")
    monkeypatch.delenv("LLAMA_CPP_BINARY", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ocr_surya.shutil, "which", lambda _name: None)

    assert ocr_surya.resolve_llama_cpp_binary() == str(binary)


def test_surya_gguf_cached_detects_required_files(monkeypatch, tmp_path):
    import ocr_surya

    snapshot = tmp_path / ".cache" / "huggingface" / "hub" / "models--datalab-to--surya-ocr-2-gguf" / "snapshots" / "rev"
    snapshot.mkdir(parents=True)
    (snapshot / "surya-2.gguf").write_text("", encoding="utf-8")
    (snapshot / "surya-2-mmproj.gguf").write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert ocr_surya._surya_gguf_cached() is True


def test_cleanup_stale_llamacpp_server_state_removes_dead_sentinel(monkeypatch, tmp_path):
    import ocr_surya

    cache_dir = tmp_path / ".cache" / "datalab" / "surya"
    cache_dir.mkdir(parents=True)
    sentinel = cache_dir / "llamacpp_server.json"
    sentinel.write_text('{"pid": 999999, "port": 51409}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ocr_surya, "_pid_exists", lambda _pid: False)

    assert ocr_surya.cleanup_stale_llamacpp_server_state() is True
    assert not sentinel.exists()
