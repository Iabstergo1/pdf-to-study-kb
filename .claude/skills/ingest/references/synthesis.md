# ingest / 阶段 E — 综合层职责（一等产物，不是可选项）

**输入**：本源各窗写出的概念/lessons + vault 既有综合页。**输出**：增量更新的 overview/topic/comparison/synthesis（`status: proposed`）。
**持久化**：vault 页（proposed）+ 进对应 window 的 `--writes` 记账。**停止点**：与既有结论矛盾时记入「未解决问题」，不悄悄改写。

- **overview.md 每源必更新**：把本源新概念挂进「核心概念地图」（并在其开头放**主题导航**块：用 wikilink 链到本源的 topic 页，使 overview 成为 topic→concept 的导航入口；**「核心概念地图」与主题导航跟随 `chapters.json` 的章节图按章组织**，形成 overview→章→topic→concept 的稳定**导航脊柱**——章节图由 `source-convert` 据 PDF 书签确定性冻结，不靠 LLM 重排）、调整「推荐学习路线」（顺章节图给）、补「模型家族对比」。overview 是 living synthesis，**禁止退化成章节清单**（L5 会拦——是"按章导航到概念/主题"而非罗列章节标题）。更新前 `check-write` + `snapshot-page`（它是已存在的 published 种子），改为 `status: proposed` 并进 `--writes`。
- **topic（概念多的源必做，否则 lint `topics-missing` 阻断）**：当本源产出较多概念（约 ≥6 个）时，**必须把概念按主题聚成 `topics/<主题>.md`**（如「信息与动态博弈」聚 信号/委托代理/重复博弈），每页含 核心综合 + 各来源贡献表 + 未解决问题，并把相关概念以 wikilink 链入——这是扁平概念之上的**分类导航层**。
- **comparison**：出现 2+ 个可横向对比的模型/方法时建/更新 `comparisons/` 页（结论/对比维度/适用场景/相关概念）。
- **synthesis**：跨来源沉淀出单一来源给不了的洞见时写 `synthesis/` 页。
- **lessons 跟随源 TOC**：每个源章节产出 lesson 是线性辅助层；概念/主题才是主组织。
- 收尾 CLI 只重建派生（index/registry/aliases），**不改写以上综合内容**——它们由你维护。

**验收**：overview 含三节综合（核心概念地图/推荐学习路线/模型家族对比）非纯链接清单；**概念多的源至少一个 topic 页**（否则 `topics-missing` 阻断）含跨来源贡献表；comparison 四节齐；所有综合页都进了 `--writes`。
