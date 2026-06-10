# 页面类型与 frontmatter 规则（指针文档，按命令最小加载）

- **6 类页面模板（写页格式契约）**：`templates/source.md` / `lesson.md` / `concept.md` /
  `topic.md` / `comparison.md` / `synthesis.md`。frontmatter 全带 Dataview 字段。
- **两阶段发布**：任何命令写出的页一律 `status: proposed`；只有收尾门禁 promote 成 `published`
  并纳入 `index.generated.md`。`managed_by: pipeline` 是覆盖保护的前提（human 页绝不覆盖）。
- **必需小节**：以 `scripts/page_rules.py::REQUIRED_SECTIONS` 为准（concept 六节、topic 三节、
  comparison 四节、synthesis 四节、source 六节）；lesson 无强制小节但须干净散文
  （无裸 E-ID、脚注 ref/def 配对——`find_bare_evidence_ids` / `missing_footnote_defs`）。
- **概念页 frontmatter 是唯一真值**（spec §6）：`canonical_id` / `canonical_name` / `aliases` /
  `scope` / `domain` / `source_refs` / `page_path`。派生文件（`_registry.yaml`/`aliases.md`/
  `index.generated.md`）由收尾 CLI 重建，任何命令不得手写。
