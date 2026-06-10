# P3 页面模板 + 正文清理执行报告

- 日期：2026-06-10
- 分支：`feat/p3-page-templates`（基于 `feat/p2-canonical-concepts`）
- 计划：`docs/superpowers/plans/2026-06-10-p3-page-templates.md`（随 Task 2 提交入库）
- 验证：`python -m pytest -q --ignore=tmp` → **163 passed**（150 旧 + 13 新，零回归）

## 提交清单（逐任务 TDD）

| 提交 | 任务 | 内容 |
|---|---|---|
| 9a9cc08 | Task 2 | `page_rules.py`：裸 E-ID 检测（L1 原语）+ 脚注 ref/def 配对（§10 证据进脚注）+ P3 plan 文档 |
| 4d0fb9d | Task 3 | 按页面类型的必需小节规则（REQUIRED_SECTIONS，标题行匹配非子串） |
| a72c8ca | Task 4 | 6 个页面模板（source/lesson/concept/topic/comparison/synthesis），frontmatter 带 Dataview 字段；模板小节由测试自动对照规则表 |
| bf36482 | Task 5 | `create_concept` 骨架改从 `templates/concept.md` 加载（单一真值，str.replace 防花括号，缺失回退内置常量） |

## 验收清单（逐项实测）

- [x] 6 个模板存在、frontmatter 可解析、`type/status: proposed/managed_by: pipeline` 正确
- [x] 每个模板包含本类型全部必需小节（`required_sections_for` 自动核对，concept 含新增 `## 自测`）
- [x] lesson 模板自证干净正文契约：无裸 E-ID、示例脚注配对、KaTeX 与源页截图示例齐全
- [x] 三组规则原语（`find_bare_evidence_ids` / `missing_footnote_defs` / `missing_sections`）纯函数无 I/O，P4 自检与 P6 门禁可直接复用
- [x] `create_concept` 正文与模板字节一致（`{name}` 替换）；模板缺失回退内置骨架；P2 全部测试原样通过（17+3）
- [x] 小节匹配按标题行（正文提到 "## 直觉" 不计）
- [x] 全量 163 passed，工作树干净

## 当前状态与下一步

P3 完成 = 写页格式契约 + 干净正文规则原语就位。分支链：main ← P0 ← P1 ← P2 ← P3（均未合并未 push）。

下一期 **P4：命令层（显式 slash command）+ `/ingest` + source 级 work order 事务协议**——核心重构期。写 P4 计划时须落实：① rolling digest（参考对照评估 C1）；② work order 生成器消费 P2 的 `write_registry` sha256；③ `/ingest` 写页引用 `templates/*` 并调 `page_rules` 自检；④ 建议先单 md 短源走通完整事务协议再开长源多窗（C3）。
