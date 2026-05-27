"""Generate Obsidian-facing index and quality files from the manifest."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def _section_sort_key(section: dict[str, Any]):
    raw = str(section.get("source_order", section.get("id", "")))
    parts = []
    for chunk in raw.replace("-", ".").split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(chunk)
    return parts


def _is_published(section: dict[str, Any]) -> bool:
    return section.get("publish_status") == "published" or section.get("status") == "published"


def _link(section: dict[str, Any]) -> str:
    sid = section["id"]
    return f"[[Section-Lessons/{sid}|{sid}]]"


class ObsidianOutputGenerator:
    def __init__(self, book_root: Path, manifest: dict[str, Any]):
        self.book_root = Path(book_root)
        self.manifest = manifest
        self.study_kb = self.book_root / "study-kb"
        self.sections = sorted(manifest.get("sections", []), key=_section_sort_key)

    def generate_all(self):
        (self.study_kb / "Learning-Maps").mkdir(parents=True, exist_ok=True)
        (self.study_kb / "Source-QA").mkdir(parents=True, exist_ok=True)
        (self.study_kb / "Section-Lessons").mkdir(parents=True, exist_ok=True)

        self.generate_home()
        self.generate_moc_full_map()
        self.generate_moc_beginner_path()
        self.generate_moc_difficult_path()
        self.generate_coverage_report()
        self.generate_risk_list()

    def generate_home(self):
        total = len(self.sections)
        published = [s for s in self.sections if _is_published(s)]
        title = self._book_title()
        updated = self._latest_published_mtime() or datetime.now().date().isoformat()

        lines = [
            f"# {title}",
            "",
            "## 内容状态",
            "",
            f"- 小节讲义：{len(published)}/{total}",
            f"- 最近更新：{updated}",
            "",
            "## 已发布小节",
            "",
        ]
        if published:
            for section in published:
                lines.append(f"- {_link(section)} - {section.get('title', '')}")
        else:
            lines.append("- 暂无已发布小节")

        lines.extend([
            "",
            "## 学习地图",
            "",
            "- [[Learning-Maps/MOC-全书学习地图|全书学习地图]]",
            "- [[Learning-Maps/MOC-入门最短路线|入门最短路线]]",
            "- [[Learning-Maps/MOC-难点与推导重点路线|难点与推导重点路线]]",
            "",
            "## 质量报告",
            "",
            "- [[Source-QA/小节覆盖报告|小节覆盖报告]]",
            "- [[Source-QA/高风险内容清单|高风险内容清单]]",
            "",
        ])
        self._write("Home.md", lines)

    def generate_moc_full_map(self):
        lines = ["# 全书学习地图", ""]
        for chapter, sections in self._sections_by_chapter().items():
            lines.extend([f"## {chapter}", ""])
            for section in sections:
                title = section.get("title", "")
                if _is_published(section):
                    lines.append(f"- {_link(section)} - {title}")
                else:
                    lines.append(f"- {section['id']} - {title}（待发布）")
            lines.append("")
        self._write("Learning-Maps/MOC-全书学习地图.md", lines)

    def generate_moc_beginner_path(self):
        lines = ["# 入门最短路线", ""]
        published_a = [
            s for s in self.sections
            if _is_published(s) and s.get("importance", "A") == "A"
        ]
        if not published_a:
            lines.append("- 暂无已发布的 A 级小节")
        else:
            for section in published_a:
                lines.append(f"- {_link(section)} - {section.get('title', '')}")
        lines.append("")
        self._write("Learning-Maps/MOC-入门最短路线.md", lines)

    def generate_moc_difficult_path(self):
        lines = ["# 难点与推导重点路线", ""]
        groups = [
            ("高公式风险", "high"),
            ("中公式风险", "medium"),
        ]
        for heading, risk in groups:
            lines.extend([f"## {heading}", ""])
            matches = [s for s in self.sections if s.get("formula_risk") == risk]
            if not matches:
                lines.append("- 暂无")
            for section in matches:
                item = _link(section) if _is_published(section) else section["id"]
                suffix = "" if _is_published(section) else "（待发布）"
                lines.append(
                    f"- {item} - {section.get('title', '')} "
                    f"(difficulty: {section.get('difficulty', 'unknown')}, formula_risk: {risk}){suffix}"
                )
            lines.append("")
        self._write("Learning-Maps/MOC-难点与推导重点路线.md", lines)

    def generate_coverage_report(self):
        total = len(self.sections)
        published = [s for s in self.sections if _is_published(s)]
        rate = (len(published) / total * 100) if total else 0.0

        lines = [
            "# 小节覆盖报告",
            "",
            f"- 总小节数：{total}",
            f"- 已发布：{len(published)}",
            f"- 覆盖率：{rate:.1f}%",
            "",
            "## 按章节统计",
            "",
            "| 章节 | 已发布 | 总数 | 状态 |",
            "|------|--------|------|------|",
        ]
        for chapter, sections in self._sections_by_chapter().items():
            count = sum(1 for s in sections if _is_published(s))
            if count == 0:
                status = "未发布"
            elif count == len(sections):
                status = "已发布"
            else:
                status = "部分发布"
            lines.append(f"| {chapter} | {count} | {len(sections)} | {status} |")

        lines.extend(["", "## 已发布小节", "", "| Section ID | 标题 | formula_risk |", "|------------|------|--------------|"])
        for section in published:
            lines.append(f"| {section['id']} | {section.get('title', '')} | {section.get('formula_risk', '')} |")
        lines.append("")
        self._write("Source-QA/小节覆盖报告.md", lines)

    def generate_risk_list(self):
        lines = [
            "# 高风险内容清单",
            "",
            "| Section ID | 标题 | formula_risk | status | difficulty |",
            "|------------|------|--------------|--------|------------|",
        ]
        risky = [
            s for s in self.sections
            if s.get("formula_risk") in {"high", "medium"}
        ]
        if risky:
            for section in risky:
                lines.append(
                    f"| {section['id']} | {section.get('title', '')} | "
                    f"{section.get('formula_risk', '')} | {section.get('status', '')} | "
                    f"{section.get('difficulty', '')} |"
                )
        else:
            lines.append("| - | 暂无 high/medium 公式风险小节 | - | - | - |")
        lines.append("")
        self._write("Source-QA/高风险内容清单.md", lines)

    def _book_title(self) -> str:
        profile_path = self.book_root / "config" / "book-profile.yaml"
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = yaml.safe_load(f) or {}
            if profile.get("title"):
                return profile["title"]
        return self.manifest.get("book_id", self.book_root.name)

    def _latest_published_mtime(self) -> str | None:
        mtimes = []
        for section in self.sections:
            if not _is_published(section):
                continue
            path = self.study_kb / "Section-Lessons" / f"{section['id']}.md"
            if path.exists():
                mtimes.append(path.stat().st_mtime)
        if not mtimes:
            return None
        return datetime.fromtimestamp(max(mtimes)).date().isoformat()

    def _sections_by_chapter(self) -> dict[str, list[dict[str, Any]]]:
        try:
            from pipeline import parse_chapter_groups
            groups = parse_chapter_groups(self.book_root)
        except Exception:
            groups = {}

        result: dict[str, list[dict[str, Any]]] = {}
        for section in self.sections:
            chapter = groups.get(section["id"]) or section.get("part") or "未知章节"
            result.setdefault(chapter, []).append(section)
        return result

    def _write(self, relative_path: str, lines: list[str]):
        path = self.study_kb / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(lines).rstrip() + "\n"
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)
        except PermissionError as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise PermissionError(
                f"无法写入 {path}。请确认该文件未被 Obsidian、编辑器或同步工具锁定。"
            ) from exc
