"""마케팅 Higgsfield 연결 상태 계약. 회사 계정 연결(MCP) 칸은 선택이라 옛 응답도 맞아야 한다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.marketing_higgsfield import HiggsfieldStatusOutput

OLD = {
    "key_configured": True,
    "connection": "ok",
    "http_status": 404,
    "image_model": None,
    "checked_at": "2026-11-23T01:02:03Z",
    "message_ko": "Higgsfield에 연결돼 있어요",
}


def test_old_answer_without_mcp_fields_still_reads() -> None:
    out = HiggsfieldStatusOutput.model_validate(OLD)
    assert out.source is None and out.credits is None and out.plan is None


def test_mcp_connection_fields_round_trip() -> None:
    raw = {**OLD, "source": "mcp", "credits": 1244.25, "plan": "ultra", "http_status": None}
    out = HiggsfieldStatusOutput.model_validate(raw)
    assert out.source == "mcp" and out.credits == 1244.25 and out.plan == "ultra"
    assert HiggsfieldStatusOutput.model_validate(out.model_dump(mode="json")) == out


@pytest.mark.parametrize("bad", [{"source": "shared"}, {"credits": -1}, {"plan": "p" * 41}])
def test_rejects_bad_mcp_values(bad: dict) -> None:
    with pytest.raises(ValidationError):
        HiggsfieldStatusOutput.model_validate({**OLD, **bad})
