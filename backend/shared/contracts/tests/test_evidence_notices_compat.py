"""빈 `evidence.notices`는 직렬화에서 뺀다 (계약 사본이 옛 판인 판매서버 호환)."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_contracts.schemas import AgentResult, Evidence, RequestMeta, TaskStatus


class _OldEvidence(BaseModel):
    """판매서버 사본(11/16판)의 Evidence: 필드 4개, extra=forbid."""

    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(default_factory=list)
    data_summary: str = ""
    collected_at: datetime | None = None
    ref_ids: list[str] = Field(default_factory=list)


class _OldMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_id: str
    tenant_id: str
    token: str
    path: list[str]
    deadline: datetime
    idempotency_key: str | None = None
    requester: str | None = None


class _OldResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meta: _OldMeta
    status: str
    outcome: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: _OldEvidence = Field(default_factory=_OldEvidence)
    reason_code: str | None = None
    decision_reason: str | None = None
    alternatives_considered: list[str] = Field(default_factory=list)
    fallback: bool = False
    fallback_reason: str | None = None
    fallback_numbers: list[float] = Field(default_factory=list)


def _result(**evidence: Any) -> AgentResult:
    meta = RequestMeta(context_id="ctx_1", token="t", deadline=datetime(2026, 11, 18, tzinfo=UTC))
    return AgentResult(meta=meta, status=TaskStatus.COMPLETED, evidence=Evidence(**evidence))


def test_empty_notices_key_is_omitted_from_json() -> None:
    assert "notices" not in _result(queries=["q"]).model_dump(mode="json")["evidence"]
    assert "notices" not in Evidence().model_dump()
    assert "notices" not in json.loads(_result().model_dump_json())["evidence"]


def test_old_contract_copy_accepts_result_without_notes() -> None:
    dumped = _result(queries=["q"], data_summary="요약", ref_ids=["a"]).model_dump(mode="json")
    old = _OldResult.model_validate(dumped)
    assert old.evidence.queries == ["q"] and old.evidence.ref_ids == ["a"]


def test_notices_are_kept_when_present() -> None:
    dumped = _result(notices=["지연 진단"]).model_dump(mode="json")
    assert dumped["evidence"]["notices"] == ["지연 진단"]
    # 알려진 한계: 노트가 있으면 옛 계약 사본은 거부한다
    with pytest.raises(ValidationError):
        _OldResult.model_validate(dumped)


def test_round_trip_without_notices_key_defaults_to_empty() -> None:
    dumped = _result(queries=["q"]).model_dump(mode="json")
    again = AgentResult.model_validate(dumped)
    assert again.evidence.notices == []
    assert again == _result(queries=["q"])
    with_notes = _result(notices=["a"])
    assert AgentResult.model_validate(with_notes.model_dump(mode="json")) == with_notes


def test_empty_on_behalf_of_is_omitted_so_old_meta_copies_still_accept() -> None:
    """`on_behalf_of`(11/20)는 비어 있으면 직렬화에서 뺀다(옛 계약 사본의 extra=forbid 호환)."""
    meta = RequestMeta(context_id="ctx_1", token="t", deadline=datetime(2026, 11, 18, tzinfo=UTC))
    assert "on_behalf_of" not in meta.model_dump(mode="json")
    assert "on_behalf_of" not in json.loads(meta.model_dump_json())
    _OldMeta.model_validate(meta.model_dump(mode="json"))
    with_person = meta.model_copy(update={"on_behalf_of": "acc-1"})
    assert with_person.model_dump(mode="json")["on_behalf_of"] == "acc-1"
    assert RequestMeta.model_validate(with_person.model_dump(mode="json")) == with_person
