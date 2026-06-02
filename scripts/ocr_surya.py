"""Optional surya-ocr adapter with deterministic fallback behavior."""

from __future__ import annotations

import os
import re
import shutil
import time
import json
from pathlib import Path
from typing import Any, Callable


class OcrUnavailable(RuntimeError):
    pass


_PREDICTOR: Any | None = None


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


def _surya_cache_dir() -> Path:
    return Path.home() / ".cache" / "datalab" / "surya"


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_stale_llamacpp_server_state() -> bool:
    sentinel = _surya_cache_dir() / "llamacpp_server.json"
    if not sentinel.exists():
        return False
    try:
        data = json.loads(sentinel.read_text(encoding="utf-8"))
    except Exception:
        try:
            sentinel.unlink(missing_ok=True)
            return True
        except OSError:
            return False
    pid = data.get("pid")
    if pid and _pid_exists(int(pid)):
        return False
    try:
        sentinel.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _cached_surya_gguf_paths() -> tuple[str, str] | None:
    hub_root = Path.home() / ".cache" / "huggingface" / "hub" / "models--datalab-to--surya-ocr-2-gguf"
    if not hub_root.exists():
        return None
    snapshots = sorted(hub_root.glob("snapshots/*"))
    for snapshot in reversed(snapshots):
        model = snapshot / "surya-2.gguf"
        mmproj = snapshot / "surya-2-mmproj.gguf"
        if model.exists() and mmproj.exists():
            return str(model), str(mmproj)
    return None


def configure_llama_cpp_binary() -> None:
    cleanup_stale_llamacpp_server_state()
    binary = resolve_llama_cpp_binary()
    if not binary:
        return
    os.environ["LLAMA_CPP_BINARY"] = binary
    os.environ.setdefault("SURYA_INFERENCE_BACKEND", "llamacpp")
    os.environ.setdefault("SURYA_INFERENCE_PARALLEL", "1")
    os.environ.setdefault("SURYA_INFERENCE_TIMEOUT_SECONDS", "900")
    os.environ.setdefault("SURYA_INFERENCE_LOGPROBS", "false")
    _surya_cache_dir().mkdir(parents=True, exist_ok=True)
    cached_paths = _cached_surya_gguf_paths()
    if cached_paths:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("SURYA_GGUF_LOCAL_MODEL_PATH", cached_paths[0])
        os.environ.setdefault("SURYA_GGUF_LOCAL_MMPROJ_PATH", cached_paths[1])
    try:
        from surya.settings import settings

        settings.LLAMA_CPP_BINARY = binary
        settings.SURYA_INFERENCE_BACKEND = os.environ["SURYA_INFERENCE_BACKEND"]
        settings.SURYA_INFERENCE_PARALLEL = int(os.environ["SURYA_INFERENCE_PARALLEL"])
        settings.SURYA_INFERENCE_TIMEOUT_SECONDS = float(os.environ["SURYA_INFERENCE_TIMEOUT_SECONDS"])
        settings.SURYA_INFERENCE_LOGPROBS = os.environ["SURYA_INFERENCE_LOGPROBS"].lower() == "true"
        if cached_paths:
            settings.SURYA_GGUF_LOCAL_MODEL_PATH = cached_paths[0]
            settings.SURYA_GGUF_LOCAL_MMPROJ_PATH = cached_paths[1]
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
    predictor = _recognition_predictor()
    from PIL import Image

    with Image.open(image_path) as image:
        result = predictor([image.copy()])
    return normalize_surya_result(result)


def _recognition_predictor():
    global _PREDICTOR
    if _PREDICTOR is not None:
        return _PREDICTOR
    try:
        configure_llama_cpp_binary()
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor
    except ImportError as exc:
        raise OcrUnavailable("surya-ocr is not installed") from exc

    manager = SuryaInferenceManager()
    _PREDICTOR = RecognitionPredictor(manager)
    return _PREDICTOR


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
        cleanup_stale_llamacpp_server_state()
        time.sleep(2)
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
