"""fail-closed 归层守卫（tests/_tiering.py）：纯判定函数矩阵 + 真实注册表健康断言。

守卫动机（test-audit-2026-07-13 P0）：daily 是正向 `-m fast` 白名单——若新增测试文件
可以不登记 tier，它会静默掉出频繁反馈层、只在 full gate 被跑到。所以任何未登记文件、
注册表死条目、非法/缺失 tier 都必须让 collection 直接失败，而不是悄悄降级。
"""
from pathlib import Path

import _tiering

TESTS_DIR = Path(__file__).resolve().parent


def test_real_registry_matches_disk_and_is_well_formed():
    # 真实注册表 vs 真实磁盘：任何漂移（新文件未登记/条目指向已删文件）都在 fast 层现形。
    files = [p.name for p in TESTS_DIR.glob("test_*.py")]
    assert _tiering.registry_violations(files, _tiering.FILE_TIERS) == []


def test_unregistered_file_is_flagged():
    v = _tiering.registry_violations(["test_a.py", "test_new.py"], {"test_a.py": ("fast",)})
    assert any("test_new.py" in p and "unregistered" in p for p in v)


def test_stale_entry_is_flagged():
    v = _tiering.registry_violations(["test_a.py"],
                                     {"test_a.py": ("fast",), "test_gone.py": ("cli",)})
    assert any("test_gone.py" in p and "stale" in p for p in v)


def test_missing_primary_tier_is_flagged():
    v = _tiering.registry_violations(["test_a.py"], {"test_a.py": ()})
    assert any("test_a.py" in p and "no primary tier" in p for p in v)


def test_unknown_tier_is_flagged():
    v = _tiering.registry_violations(["test_a.py"], {"test_a.py": ("banana",)})
    assert any("test_a.py" in p and "unknown tier" in p for p in v)


def test_fast_must_not_combine_with_heavier_tiers():
    # fast = 纯函数频繁层；与 cli/slow 组合意味着重测试混进 daily，判定为冲突归层。
    v = _tiering.registry_violations(["test_a.py"], {"test_a.py": ("fast", "cli")})
    assert any("test_a.py" in p and "fast" in p for p in v)
