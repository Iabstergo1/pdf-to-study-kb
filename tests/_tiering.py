"""测试分层唯一注册表 + fail-closed 归层守卫的纯判定（conftest 消费）。

每个 tests/test_*.py 都必须在 FILE_TIERS 显式登记主 tier（fast/cli/slow/skill/realbook，
pytest.ini 注册）。daily 是正向 `-m fast` 白名单，所以守卫 fail-closed：未登记的新文件、
指向已删文件的死条目、非法 tier 名、fast 与重 tier 的冲突组合，任一都让 collection 失败
（见 registry_violations；接线在 conftest.pytest_collection_modifyitems）。

分层依据 = 实测（pipeline-workspace/reports/test-audit-2026-07-13.md P0）：
- fast   纯函数/直接模块测试，整层 ≈ 十几秒，日常每次编辑后跑；
- cli    subprocess 起真实 pipeline.py 的 wiring/集成测试；
- slow   高成本完整工作流（整链预处理/发布/skill gate），只进 full gate；
- skill  双 skill 树协议与文档契约；
- realbook 预留层（真实书源/MinerU 模型），当前无测试、非现有门禁。
"""

PRIMARY_TIERS = ("fast", "cli", "slow", "skill", "realbook")

# filename -> tier markers applied to every test collected from that file.
FILE_TIERS = {
    # ---- 重型 CLI 工作流（full gate only）----
    "test_conversion_backend_cli.py": ("cli", "slow"),
    "test_lint_republish_cli.py": ("cli", "slow"),
    "test_ingest_orchestration_cli.py": ("cli", "slow"),
    "test_staging_clean_cli.py": ("cli", "slow"),
    "test_doctor_cli.py": ("cli", "slow"),
    "test_skill_evolution.py": ("skill", "slow"),
    # ---- CLI wiring / subprocess 集成 ----
    "test_preprocessing_cli.py": ("cli",),
    "test_ops_metrics_cli.py": ("cli",),
    "test_concept_promotion_cli.py": ("cli",),
    "test_query_session_cli.py": ("cli",),
    "test_vault_init_cli.py": ("cli",),
    "test_pipeline_status.py": ("cli",),
    "test_graph_v2_e2e.py": ("cli",),
    "test_preflight_eval_cli.py": ("cli",),
    "test_resume_packet_cli.py": ("cli",),
    "test_resume_ingest_smoke.py": ("cli",),
    # ---- skill 协议 / 文档契约 ----
    "test_command_docs.py": ("skill",),
    "test_skill_standard.py": ("skill",),
    # ---- fast：纯函数 / 直接模块 ----
    "test_arbitration.py": ("fast",),
    "test_chaptering.py": ("fast",),
    "test_concept_store.py": ("fast",),
    "test_graph_analysis.py": ("fast",),
    "test_graph_data.py": ("fast",),
    "test_graph_html.py": ("fast",),
    "test_graph_lint.py": ("fast",),
    "test_graph_model.py": ("fast",),
    "test_ingest_guards.py": ("fast",),
    "test_ingest_progress.py": ("fast",),
    "test_install_mineru.py": ("fast",),
    "test_legacy_removed.py": ("fast",),
    "test_locks.py": ("fast",),
    "test_mdpage.py": ("fast",),
    "test_mineru_backend.py": ("fast",),
    "test_page_rules.py": ("fast",),
    "test_preflight_eval.py": ("fast",),
    "test_promotion.py": ("fast",),
    "test_query_session.py": ("fast",),
    "test_record_artifact.py": ("fast",),
    "test_resume_packet.py": ("fast",),
    "test_snapshots.py": ("fast",),
    "test_source_artifacts.py": ("fast",),
    "test_source_audit.py": ("fast",),
    "test_source_convert.py": ("fast",),
    "test_state_store.py": ("fast",),
    "test_templates.py": ("fast",),
    "test_thresholds.py": ("fast",),
    "test_tiering_guard.py": ("fast",),
    "test_wiki_gate.py": ("fast",),
    "test_wiki_gate_callout.py": ("fast",),
    "test_windowing.py": ("fast",),
    "test_workorder.py": ("fast",),
}


def registry_violations(existing_files, registry):
    """磁盘上的 test_*.py 文件名集合 vs 注册表 → 违规消息列表（空 = 合规）。

    纯函数（不触磁盘），便于直接单元测试；fail-closed 语义由调用方兑现
    （conftest 在 collection 期对非空结果直接报错退出）。
    """
    problems = []
    existing = set(existing_files)
    registered = set(registry)
    for name in sorted(existing - registered):
        problems.append(
            f"unregistered test file: {name}（在 tests/_tiering.py FILE_TIERS 登记主 tier，"
            "否则它会静默掉出 -m fast 日常层）")
    for name in sorted(registered - existing):
        problems.append(f"stale FILE_TIERS entry: {name}（文件已不存在，删除该条目）")
    for name, marks in sorted(registry.items()):
        unknown = sorted(set(marks) - set(PRIMARY_TIERS))
        if unknown:
            problems.append(f"{name}: unknown tier(s) {unknown}（合法值：{'/'.join(PRIMARY_TIERS)}）")
        if not any(m in PRIMARY_TIERS for m in marks):
            problems.append(f"{name}: no primary tier（fast/cli/slow/skill/realbook 至少其一）")
        if "fast" in marks and len(tuple(marks)) > 1:
            problems.append(f"{name}: fast 不得与其他 tier 组合（fast=纯函数频繁层，混入重测试即失真）")
    return problems
