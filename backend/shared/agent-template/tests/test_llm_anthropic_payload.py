"""Claude(Anthropic) 호환 주소는 엄격 스키마·temperature 없음, 다른 주소는 예전 그대로."""

from ax_agent_template.llm import response_payload

SCHEMA = {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}}}


def test_anthropic_payload_is_strict_without_temperature() -> None:
    p = response_payload("https://api.anthropic.com/v1", "claude-sonnet-5-5", [], "Out", dict(SCHEMA))
    js = p["response_format"]["json_schema"]
    assert js["strict"] is True and "temperature" not in p
    assert js["schema"]["additionalProperties"] is False
    assert js["schema"]["properties"]["a"]["additionalProperties"] is False


def test_other_payload_unchanged() -> None:
    p = response_payload("https://generativelanguage.googleapis.com/v1beta/openai", "g", [], "Out", dict(SCHEMA))
    assert p["temperature"] == 0 and "strict" not in p["response_format"]["json_schema"]
