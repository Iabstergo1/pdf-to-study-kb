"""Optional surya-ocr adapter with deterministic fallback behavior."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable


class OcrUnavailable(RuntimeError):
    pass


def is_surya_available() -> bool:
    try:
        import surya  # noqa: F401
        return True
    except ImportError:
        return False


def resolve_llama_cpp_binary() -> str | None:
    configured = os.environ.get("LLAMA_CPP_BINARY")
    if configured:
        configured_path = Path(configured)
        if configured_path.exists() or shutil.which(configured):
            return configured

    found = shutil.which("llama-server")
    if found:
        return found

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    winget_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not winget_root.exists():
        return None
    matches = sorted(winget_root.glob("ggml.llamacpp_*/*llama-server.exe"))
    if matches:
        return str(matches[-1])
    return None


def configure_llama_cpp_binary() -> None:
    binary = resolve_llama_cpp_binary()
    if not binary:
        return
    os.environ["LLAMA_CPP_BINARY"] = binary
    (Path.home() / ".cache" / "datalab" / "surya").mkdir(parents=True, exist_ok=True)
    if _surya_gguf_cached():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from surya.settings import settings

        settings.LLAMA_CPP_BINARY = binary
    except ImportError:
        return


def _surya_gguf_cached() -> bool:
    hub_root = Path.home() / ".cache" / "huggingface" / "hub" / "models--datalab-to--surya-ocr-2-gguf"
    if not hub_root.exists():
        return False
    return bool(list(hub_root.glob("snapshots/*/surya-2.gguf"))) and bool(
        list(hub_root.glob("snapshots/*/surya-2-mmproj.gguf"))
    )


def normalize_surya_result(result: Any) -> dict[str, Any]:
    if isinstance(result, list):
        if not result:
            raise RuntimeError("surya returned empty result")
        result = result[0]
    blocks = getattr(result, "blocks", None)
    if blocks is None and isinstance(result, dict):
        blocks = result.get("blocks")
    if not blocks:
        raise RuntimeError("surya returned no OCR blocks")

    normalized = []
    for block in blocks:
        if isinstance(block, dict):
            text = block.get("text") or block.get("html") or ""
            html = block.get("html") or ""
            bbox = block.get("bbox")
        else:
            text = getattr(block, "text", "") or getattr(block, "html", "")
            html = getattr(block, "html", "")
            bbox = getattr(block, "bbox", None)
        if not text and not html:
            continue
        normalized.append({"text": text, "html": html, "bbox": bbox})
    if not normalized:
        raise RuntimeError("surya returned malformed OCR blocks")
    return {"status": "ok", "blocks": normalized}


def recognize_page_image(image_path: Path) -> dict[str, Any]:
    try:
        configure_llama_cpp_binary()
        from PIL import Image
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor
    except ImportError as exc:
        raise OcrUnavailable("surya-ocr is not installed") from exc

    image = Image.open(image_path)
    manager = SuryaInferenceManager()
    predictor = RecognitionPredictor(manager)
    result = predictor([image])
    return normalize_surya_result(result)


def extract_latex_preview(html: str) -> str:
    matches = re.findall(r"<math[^>]*>(.*?)</math>", html or "", flags=re.IGNORECASE | re.DOTALL)
    return " ".join(item.strip() for item in matches if item.strip())[:200]


def recognize_page_image_with_retry(
    image_path: Path,
    recognizer: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    recognizer = recognizer or recognize_page_image
    try:
        return recognizer(image_path)
    except OcrUnavailable:
        return {
            "status": "unavailable",
            "formula_risk": "high",
            "risk_flags": ["ocr_unavailable"],
            "block_publish": True,
            "blocks": [],
        }
    except Exception as first_exc:
        try:
            return recognizer(image_path)
        except OcrUnavailable:
            return {
                "status": "unavailable",
                "formula_risk": "high",
                "risk_flags": ["ocr_unavailable"],
                "block_publish": True,
                "blocks": [],
            }
        except Exception as second_exc:
            return {
                "status": "failed",
                "risk_flags": ["screenshot_ocr_failed"],
                "block_publish": True,
                "error": str(second_exc or first_exc),
                "blocks": [],
            }
