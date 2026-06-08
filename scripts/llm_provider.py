"""OpenAI-compatible LLM provider helpers.

The project keeps providers behind a tiny interface so the pipeline can run
with DeepSeek, OpenAI, or a test fake without changing graph code.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProviderConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    review_model: str
    planner_model: str
    revise_model: str = ""
    temperature: float = 0.2
    timeout_seconds: int = 120
    ssl_verify: bool = True
    max_retries: int = 2


def load_provider_config(
    env_file: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> ProviderConfig:
    """Load provider config from .env plus process env.

    Process env overrides .env. Tests can pass ``environ`` to avoid reading the
    real environment.
    """
    env_path = Path(env_file) if env_file is not None else Path(".env")
    should_read_env_file = env_file is not None or environ is None
    file_values = _read_env_file(env_path) if should_read_env_file and env_path.exists() else {}
    env_values = dict(os.environ if environ is None else environ)
    values = {**file_values, **env_values}

    provider = values.get("LLM_PROVIDER", "openai-compatible").strip()
    api_key = values.get("LLM_API_KEY", "").strip()
    base_url = values.get("LLM_BASE_URL", "").strip().rstrip("/")
    model = values.get("LLM_MODEL", "").strip()
    review_model = values.get("LLM_REVIEW_MODEL", model).strip()
    planner_model = values.get("LLM_PLANNER_MODEL", model).strip()
    revise_model = values.get("LLM_REVISE_MODEL", model).strip()

    if provider != "fake":
        missing = []
        if not api_key:
            missing.append("LLM_API_KEY")
        if not base_url:
            missing.append("LLM_BASE_URL")
        if not model:
            missing.append("LLM_MODEL")
        if missing:
            raise ValueError("缺少 LLM 配置: " + ", ".join(missing))

    return ProviderConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        review_model=review_model or model,
        planner_model=planner_model or model,
        revise_model=revise_model or model,
        temperature=float(values.get("LLM_TEMPERATURE", "0.2")),
        timeout_seconds=int(values.get("LLM_TIMEOUT_SECONDS", "120")),
        ssl_verify=values.get("LLM_SSL_VERIFY", "true").strip().lower() != "false",
        max_retries=int(values.get("LLM_MAX_RETRIES", "2")),
    )


def create_provider(config: ProviderConfig | None = None):
    config = config or load_provider_config()
    if config.provider == "fake":
        raw = os.environ.get("LLM_FAKE_RESPONSES_JSON", "[]")
        try:
            responses = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM_FAKE_RESPONSES_JSON 不是合法 JSON") from exc
        return FakeChatProvider(responses)
    return OpenAICompatibleProvider(config)


class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.calls: list[dict[str, Any]] = []

    def chat_json(
        self,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        model_name = model or self.config.model
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
            "response_format": {"type": "json_object"},
        }
        self.calls.append({"model": model_name, "system": system, "user": user})
        # 对「输出非法/截断 JSON」也重试：模型在 json_object 模式下偶尔会截断（输出过长触顶）
        # 或夹带非 JSON 文本，重新生成通常即可恢复。网络层错误已在 _post 内单独重试。
        import time

        attempts = max(1, self.config.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            response = self._post(payload)
            try:
                content = response["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("LLM 响应缺少 choices[0].message.content") from exc
            try:
                return parse_json_content(content)
            except ValueError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    backoff = 2 ** attempt
                    print(f"[retry] LLM 输出非法 JSON（第 {attempt + 1}/{attempts} 次），{backoff}s 后重试")
                    time.sleep(backoff)
        raise last_error if last_error else ValueError("LLM 输出不是合法 JSON")

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        import ssl
        import time

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )
        if self.config.ssl_verify:
            ctx = None  # 使用默认 SSL 验证
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        # 只对瞬时错误重试：超时、连接失败、SSL 抖动、HTTP 429/5xx。
        # 4xx（鉴权、请求格式等）是确定性错误，重试也没用，直接抛出。
        attempts = max(1, self.config.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.config.timeout_seconds, context=ctx
                ) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code != 429 and exc.code < 500:
                    raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
                last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
                last_error = RuntimeError(f"LLM 连接失败: {exc}")
            if attempt < attempts - 1:
                backoff = 2 ** attempt  # 1s, 2s, 4s ...
                print(f"[retry] LLM 调用失败（第 {attempt + 1}/{attempts} 次），{backoff}s 后重试: {last_error}")
                time.sleep(backoff)
        raise last_error if last_error else RuntimeError("LLM 调用失败")


class FakeChatProvider:
    """Deterministic provider for tests and local dry validation."""

    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat_json(
        self,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append({
            "system": system,
            "user": user,
            "model": model,
            "temperature": temperature,
        })
        if not self.responses:
            raise RuntimeError("FakeChatProvider 没有剩余响应")
        response = self.responses.pop(0)
        if isinstance(response, dict):
            return response
        if isinstance(response, str):
            return parse_json_content(response)
        raise TypeError(f"不支持的 fake response 类型: {type(response)!r}")


def parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 输出不是合法 JSON: {content[:200]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON 输出必须是对象")
    return parsed


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key.strip()] = value
    return values
