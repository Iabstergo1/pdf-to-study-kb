# 概念归一协议（resolve_or_create_concept）

spec §6：所有 concept 创建/更新的**唯一入口**。命中 canonical_id 则 merge 进既有页（**绝不新建**重复页）；
未命中按 `concept.<domain>.<slug>` 新建骨架页并登记。

## 用法（/ingest 与 /kb-save 共用）

```
python scripts/pipeline.py resolve-concept --mention "<正文中的提及>" --domain <domain> \
    [--alias "<别名>" ...] [--ref-source <source_id> --ref-sections "5.2,12.2"]
```

- 输出 `[merged] <canonical_id> -> <页路径>`：去编辑该页填充/补充正文（先 check-write + snapshot-page）。
- 输出 `[created] <canonical_id> -> <页路径>`：骨架页已建好（status: proposed），填充五个小节 + 自测。
- CLI 每次调用从概念页**实时扫描**重建内存 registry——会话内新建的概念立即可被后续 resolve 命中。
- 同名异义（econ 的 utility vs cs 的 utility）天然被 `concept.<domain>.<slug>` 命名空间隔离，不会合并。
- 跨域提升（domain → shared）必须经 Review-Queue 人工确认（P7 流程），命令不得自行提升。
- 别名只写概念页 frontmatter `aliases:`；`aliases.md` 是派生视图，不得手写。
