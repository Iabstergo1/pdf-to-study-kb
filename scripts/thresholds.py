"""集中阈值配置（确定性，零 LLM）：检测 / 路由 / 门禁的边界值单一真值。

这些值原先散落在 source_profile / source_convert / mineru_backend / wiki_gate 的内联字面量里，
是按示例文档实测调出来的。集中到此 + 支持 **环境变量覆盖**：默认行为完全不变，但每个魔数
都能按 run 调（无需改代码），且一处可审计。

用法（按某份文档临时调阈值，不改代码）：
    STUDY_KB_VECTOR_FIGURE_DRAW=20 python scripts/pipeline.py profile --source X

覆盖了哪些值会折进 profile/convert 的缓存键（见 fingerprint）：改阈值即失效缓存、强制对该源
重算，与 PROFILER_VERSION 同规——不会因 [skip] 而用旧阈值的陈旧产物。
"""
from __future__ import annotations

import hashlib
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


# ── L1 检测·公式（source_profile.needs_vision_reasons）──
FORMULA_STRONG = _int("STUDY_KB_FORMULA_STRONG", 12)            # f>= → "formula"
FORMULA_BORDERLINE = _int("STUDY_KB_FORMULA_BORDERLINE", 6)     # f>=（非代码）→ "formula-borderline"
EQ_LINES_MIN = _int("STUDY_KB_EQ_LINES_MIN", 2)                 # 方程行≥ → borderline
CODE_HINT_MIN = _int("STUDY_KB_CODE_HINT_MIN", 3)              # 代码指纹≥ → 代码页（抑制弱信号）

# ── L1 检测·图 / 表 / 标题 ──
VECTOR_FIGURE_DRAW = _int("STUDY_KB_VECTOR_FIGURE_DRAW", 12)        # n_draw>= → "vector-figure"
VECTOR_FIGURE_MUST_DRAW = _int("STUDY_KB_VECTOR_FIGURE_MUST_DRAW", 20)  # n_draw>= → vision_tier=must
MATRIX_STRUCT_DRAW = _int("STUDY_KB_MATRIX_STRUCT_DRAW", 6)         # 矩阵词 + n_draw>= → table
CAPTION_VISUAL_DRAW = _int("STUDY_KB_CAPTION_VISUAL_DRAW", 8)       # caption 须共现视觉：n_draw>=
SCANNED_TEXT_FLOOR = _int("STUDY_KB_SCANNED_TEXT_FLOOR", 50)        # text_len< → 近零文本层
GRID_MIN_NUMS = _int("STUDY_KB_GRID_MIN_NUMS", 2)                   # 数字网格：行内数字 token≥
GRID_DIGIT_DENSITY = _float("STUDY_KB_GRID_DIGIT_DENSITY", 0.30)    # 数字网格：数字字符占比≥
GRID_MIN_ROWS = _int("STUDY_KB_GRID_MIN_ROWS", 2)                  # 数字网格：数据行≥

# ── L1 整本扫描件判定（source_profile.is_scanned_source）──
SCANNED_ZERO_TEXT_RATIO = _float("STUDY_KB_SCANNED_ZERO_TEXT_RATIO", 0.80)
SCANNED_IMAGE_RATIO = _float("STUDY_KB_SCANNED_IMAGE_RATIO", 0.80)

# ── L1 路由（source_convert）──
LOW_TEXT_MEAN = _int("STUDY_KB_LOW_TEXT_MEAN", 100)        # 平均文本< → low_text_pdf / 路由 mineru
DENSE_RATIO = _float("STUDY_KB_DENSE_RATIO", 0.30)         # 密集/部分扫描比例阈值

# ── L1 OCR 识别置信（mineru per_page_signals）──
OCR_LOW_CONF_MIN = _float("STUDY_KB_OCR_LOW_CONF_MIN", 0.60)
OCR_LOW_CONF_MEAN = _float("STUDY_KB_OCR_LOW_CONF_MEAN", 0.85)

# ── L4/lint 生成·审核门禁（wiki_gate）──
TOPIC_THRESHOLD = _int("STUDY_KB_TOPIC_THRESHOLD", 6)      # 本批≥N concept 却无 topic → 阻断
LESSON_MIN_BODY = _int("STUDY_KB_LESSON_MIN_BODY", 80)     # lesson 正文最小字符

# ── 观测：preflight-eval 检测分布告警 ──
DETECT_RATIO_HIGH = _float("STUDY_KB_DETECT_RATIO_HIGH", 0.90)  # needs_vision 比例> → 疑过召回（warn）

# ── source-audit 双审互检（验收期；不折进 profile/convert 缓存键——只影响 reconciliation，不改抽取产物）──
RECONCILE_PAGECOUNT_TOL = _int("STUDY_KB_RECONCILE_PAGECOUNT_TOL", 1)  # |primary−review| 页数差> → page_count_mismatch 分歧

# ── source-audit 证据风险层（evidence-risk；验收期，不折进 profile/convert 缓存键——只影响 evidence/候选）──
FRAGMENT_MIN_LINES = _int("STUDY_KB_FRAGMENT_MIN_LINES", 3)           # 页文本行数≥ 才评碎片化
FRAGMENT_SHORTLINE_LEN = _int("STUDY_KB_FRAGMENT_SHORTLINE_LEN", 4)   # strip 后 len≤ 算短行
FRAGMENT_SHORTLINE_RATIO = _float("STUDY_KB_FRAGMENT_SHORTLINE_RATIO", 0.5)  # 短行占比≥ → 碎片化

# 折进缓存键的检测/路由阈值（不含纯观测/门禁项；改这些才需对 profile/convert 重算）。
_CACHE_KEYED = (
    "FORMULA_STRONG", "FORMULA_BORDERLINE", "EQ_LINES_MIN", "CODE_HINT_MIN",
    "VECTOR_FIGURE_DRAW", "VECTOR_FIGURE_MUST_DRAW", "MATRIX_STRUCT_DRAW", "CAPTION_VISUAL_DRAW",
    "SCANNED_TEXT_FLOOR", "GRID_MIN_NUMS", "GRID_DIGIT_DENSITY", "GRID_MIN_ROWS",
    "SCANNED_ZERO_TEXT_RATIO", "SCANNED_IMAGE_RATIO", "LOW_TEXT_MEAN", "DENSE_RATIO",
    "OCR_LOW_CONF_MIN", "OCR_LOW_CONF_MEAN")


def fingerprint() -> str:
    """当前生效的检测/路由阈值短指纹（折进 profile/convert input_hash）。默认值下恒定；
    任一被 env 覆盖即变化 → 失效缓存、强制重算，不会用旧阈值的陈旧产物。"""
    g = globals()
    payload = ",".join(f"{k}={g[k]}" for k in _CACHE_KEYED)
    return "th-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
