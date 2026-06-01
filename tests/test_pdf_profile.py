import sys
from pathlib import Path

import fitz
import yaml


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _make_book_with_pdf(tmp_path):
    book_root = tmp_path / "books" / "phase2-book"
    input_dir = book_root / "input"
    input_dir.mkdir(parents=True)
    (book_root / "config").mkdir()
    (book_root / "pipeline-workspace" / "reports").mkdir(parents=True)

    pdf_path = input_dir / "sample.pdf"
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text(
        (72, 72),
        "Intro page with table\nName    Value\nA       1\nB       2\n",
    )
    page2 = doc.new_page()
    page2.insert_text(
        (72, 72),
        "Formula page\nalpha beta gamma\n∑ payoff_i = p_i * v_i\nx y z w v\n",
    )
    doc.set_toc([[1, "Intro", 1], [1, "Formula", 2]])
    doc.save(str(pdf_path))
    doc.close()
    return book_root


def test_profile_pdf_returns_page_metrics_and_risks(tmp_path):
    from pdf_profile import profile_pdf

    book_root = _make_book_with_pdf(tmp_path)

    profile = profile_pdf(book_root)

    assert profile["book_id"] == "phase2-book"
    assert profile["source_pdf"] == "sample.pdf"
    assert profile["total_pages"] == 2
    assert profile["toc"] == [
        {"level": 1, "title": "Intro", "page": 1},
        {"level": 1, "title": "Formula", "page": 2},
    ]
    assert profile["pages"][0]["page"] == 1
    assert profile["pages"][0]["text_length"] > 0
    assert profile["pages"][0]["table_risk"] in {"medium", "high"}
    assert profile["pages"][1]["formula_risk"] == "high"
    assert profile["pages"][1]["recommended_extraction_method"] in {
        "hybrid",
        "screenshot_ocr",
    }
    assert "\n" not in profile["pages"][1]["summary_200"]


def test_profile_pdf_command_writes_yaml_and_report_without_source_slice(tmp_path):
    from pdf_profile import profile_pdf_command

    book_root = _make_book_with_pdf(tmp_path)

    profile_pdf_command(book_root, force=True)

    profile_path = book_root / "config" / "pdf-profile.yaml"
    report_path = book_root / "pipeline-workspace" / "reports" / "pdf-profile.md"
    assert profile_path.exists()
    assert report_path.exists()

    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    assert profile["total_pages"] == 2
    assert profile["pages"][1]["formula_risk"] == "high"

    report = report_path.read_text(encoding="utf-8")
    assert "总页数: 2" in report
    assert "风险页统计" in report
    assert "每页短摘要" in report
    assert "p.2" in report

    source_slices = list((book_root / "pipeline-workspace").glob("**/source-slice.md"))
    assert source_slices == []
