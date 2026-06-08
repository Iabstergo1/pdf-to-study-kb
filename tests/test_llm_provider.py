import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _config(max_retries=2):
    from llm_provider import ProviderConfig

    return ProviderConfig(
        provider="openai-compatible",
        api_key="k",
        base_url="https://example.test/v1",
        model="m",
        review_model="m",
        planner_model="m",
        max_retries=max_retries,
    )


def _message(content):
    return {"choices": [{"message": {"content": content}}]}


def test_chat_json_retries_on_invalid_json_then_succeeds():
    """reviewer 偶尔返回截断/非法 JSON：应重新生成而不是直接抛错炸掉整本书。"""
    from llm_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(_config(max_retries=2))
    posts = [
        _message('{"decision": {"decision": "accept"  <截断'),  # 非法 JSON
        _message('{"decision": {"decision": "accept", "confidence": "high"}}'),  # 合法
    ]

    def fake_post(payload):
        return posts.pop(0)

    provider._post = fake_post  # type: ignore[assignment]

    result = provider.chat_json(system="s", user="u")
    assert result["decision"]["decision"] == "accept"
    assert posts == []  # 两次都用上了：先失败后成功


def test_chat_json_raises_after_exhausting_json_retries():
    from llm_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(_config(max_retries=1))
    calls = {"n": 0}

    def fake_post(payload):
        calls["n"] += 1
        return _message("not json at all")

    provider._post = fake_post  # type: ignore[assignment]

    raised = False
    try:
        provider.chat_json(system="s", user="u")
    except ValueError:
        raised = True
    assert raised
    assert calls["n"] == 2  # max_retries=1 → 共 2 次尝试
