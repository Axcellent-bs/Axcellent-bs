import pytest
from pydantic import ValidationError

from ax_contracts.data_access.llm_usage import LlmCallRecord, LlmUsageQuery, LlmUsageResult


def _record(**over):
    base = {
        "log_id": "log-1",
        "occurred_at": "2026-01-01T00:00:00Z",
        "source": "finance",
        "department": "finance",
        "model": "m",
        "input_tokens": 10,
        "output_tokens": 5,
        "success": True,
    }
    return {**base, **over}


def test_record_roundtrip_and_defaults():
    r = LlmCallRecord.model_validate(_record())
    assert r.tenant_id == "shop-demo"
    assert r.context_id is None
    assert LlmCallRecord.model_validate(r.model_dump(mode="json")) == r


def test_model_may_be_none():
    assert LlmCallRecord.model_validate(_record(model=None)).model is None


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_negative_tokens_rejected(field):
    with pytest.raises(ValidationError):
        LlmCallRecord.model_validate(_record(**{field: -1}))


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        LlmCallRecord.model_validate(_record(prompt="raw text"))


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        LlmCallRecord.model_validate(_record(occurred_at="2026-01-01T00:00:00"))


def test_query_from_alias_and_limit():
    q = LlmUsageQuery.model_validate({"from": "2026-01-01T00:00:00Z", "departments": ["hr"]})
    assert q.from_ is not None
    assert q.departments == ["hr"]
    assert q.limit == 1000
    for limit in (0, 5001):
        with pytest.raises(ValidationError):
            LlmUsageQuery.model_validate({"limit": limit})


def test_result_default_not_truncated():
    r = LlmCallRecord.model_validate(_record())
    assert LlmUsageResult(records=[r]).truncated is False


def test_account_id_is_optional_and_old_records_stay_company_usage():
    """개인 키 호출만 계정 ID가 붙는다(개인 AI 연결, 11/20). 옛 기록은 None."""
    assert LlmCallRecord.model_validate(_record()).account_id is None
    assert LlmCallRecord.model_validate(_record(account_id="acc-1")).account_id == "acc-1"
    assert LlmUsageQuery().account_id is None
    assert LlmUsageQuery(account_id="acc-1").account_id == "acc-1"
