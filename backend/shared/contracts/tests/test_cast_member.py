"""등장 캐릭터 cast (11/27 A): 추가만, 기본값이 있어 예전 요청 그대로 읽힌다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.marketing_brand import MAX_CAST, MAX_CAST_ROLE, CastMember
from ax_contracts.schemas.planning import ReceiveInsightInput

A, B, C, D = (f"CR-{c * 12}" for c in "abcd")
OLD = {"insight_id": "i1", "title": "t", "summary": "s", "skill": "trend_report_create",
       "facts": [{"label": "l", "value": "v", "source": "s"}],
       "brand_character_record_id": A}  # fmt: skip


def test_old_request_without_cast_reads_as_empty() -> None:
    inp = ReceiveInsightInput.model_validate(OLD)
    assert inp.cast == [] and inp.brand_character_record_id == A


def test_cast_round_trips_and_first_is_the_lead() -> None:
    cast = [{"record_id": A, "role_ko": "주인공"}, {"record_id": B, "role_ko": "동료"},
            {"record_id": C, "role_ko": "마스코트"}]  # fmt: skip
    inp = ReceiveInsightInput.model_validate(OLD | {"cast": cast})
    assert [m.record_id for m in inp.cast] == [A, B, C] and inp.cast[0].role_ko == "주인공"
    again = ReceiveInsightInput.model_validate(inp.model_dump(mode="json"))
    assert again.cast == inp.cast
    assert CastMember(record_id=A).role_ko == ""  # 역할은 비어도 된다


def test_cast_limits() -> None:
    four = [{"record_id": x} for x in (A, B, C, D)]
    assert MAX_CAST == 3
    with pytest.raises(ValidationError):
        ReceiveInsightInput.model_validate(OLD | {"cast": four})
    with pytest.raises(ValidationError):
        CastMember(record_id="CR-xyz")
    with pytest.raises(ValidationError):
        CastMember(record_id=A, role_ko="가" * (MAX_CAST_ROLE + 1))
