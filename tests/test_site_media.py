import importlib.util
import sys
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


site_media = _load("site_media")


def _png(path: Path, width=120, height=60, color=(0.1, 0.4, 0.8)):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(fitz.Rect(0, 0, width, height), color=color, fill=color)
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    path.write_bytes(pix.tobytes("png"))
    doc.close()


def test_load_page_assets_parses_page_level_and_ignores_source_level():
    text = (
        "# 难页原图索引\n\n"
        "## Source A\n\n"
        "**page 级**（写该页时所读窗口的难页原图）\n\n"
        "- [[domains/d/concepts/a.md|A]] → "
        "[p.1](assets/d/p0001.png)、[p.2](assets/d/p0002.png)\n\n"
        "## Source B\n\n"
        "**page 级**（写该页时所读窗口的难页原图）\n\n"
        "- [[domains/d/concepts/a.md|A]] → "
        "[p.2](assets/d/p0002.png)、[p.3](assets/e/p0003.png)\n\n"
        "**source 级**（无法证明具体页归属）\n\n"
        "- [p.3](assets/d/p0003.png)\n"
    )

    mapping = site_media.load_page_assets(text)

    assert mapping == {
        "domains/d/concepts/a.md": [
            "assets/d/p0001.png",
            "assets/d/p0002.png",
            "assets/e/p0003.png",
        ]
    }


def test_stage_image_files_copies_originals_and_creates_deterministic_thumbs(tmp_path):
    vault = tmp_path / "wiki"
    source = vault / "assets" / "d" / "p0001.png"
    source.parent.mkdir(parents=True)
    _png(source)
    first_output = tmp_path / "site-first"
    second_output = tmp_path / "site-second"
    page_assets = {"domains/d/concepts/a.md": [source.relative_to(vault).as_posix()]}

    first = site_media.stage_image_files(vault, first_output, page_assets)
    second = site_media.stage_image_files(vault, second_output, page_assets)

    assert first["domains/d/concepts/a.md"][0]["original"] == "assets/d/p0001.png"
    assert first["domains/d/concepts/a.md"][0]["thumbnail"] == "assets/thumbs/d/p0001.png"
    assert first == second
    assert (first_output / "assets" / "d" / "p0001.png").read_bytes() == source.read_bytes()
    assert (first_output / "assets" / "d" / "p0001.png").read_bytes() == (
        second_output / "assets" / "d" / "p0001.png"
    ).read_bytes()
    assert (first_output / "assets" / "thumbs" / "d" / "p0001.png").read_bytes() == (
        second_output / "assets" / "thumbs" / "d" / "p0001.png"
    ).read_bytes()


def test_source_panel_html_has_p2_semantics_and_relative_image_links():
    entries = [{
        "original": "assets/d/p0001.png",
        "thumbnail": "assets/thumbs/d/p0001.png",
        "label": "p.1",
    }]

    html = site_media.render_source_panel(entries)

    assert "原书难页" in html
    assert "写本页时所读窗口的难页原图" in html
    assert "不等同于" in html
    assert 'href="assets/d/p0001.png"' in html
    assert 'src="assets/thumbs/d/p0001.png"' in html


def test_describe_source_media_filters_to_concept_pages():
    mapping = {
        "domains/d/concepts/a.md": ["assets/d/p0001.png"],
        "topics/T.md": ["assets/d/p0002.png"],
    }

    described = site_media.describe_source_media(
        mapping, only_rels={"domains/d/concepts/a.md"}
    )

    assert list(described) == ["domains/d/concepts/a.md"]


def test_thumbnail_paths_do_not_collide_on_same_source_directory_name(tmp_path):
    vault = tmp_path / "wiki"
    first = vault / "assets" / "a" / "shared" / "p0001.png"
    second = vault / "assets" / "b" / "shared" / "p0001.png"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    _png(first, color=(0.9, 0.2, 0.2))
    _png(second, width=130, color=(0.2, 0.8, 0.2))
    output = tmp_path / "site"
    page_assets = {"a.md": [
        first.relative_to(vault).as_posix(),
        second.relative_to(vault).as_posix(),
    ]}

    staged = site_media.stage_image_files(vault, output, page_assets)

    thumbs = [entry["thumbnail"] for entry in staged["a.md"]]
    assert thumbs == [
        "assets/thumbs/a/shared/p0001.png",
        "assets/thumbs/b/shared/p0001.png",
    ]
    assert (output / thumbs[0]).read_bytes() != (output / thumbs[1]).read_bytes()
