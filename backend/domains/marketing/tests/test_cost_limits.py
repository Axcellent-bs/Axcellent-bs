"""영상 비용 상한 화면 설정: 우선순위·검증·승인 경로 (가짜 관문·가짜 정책, 과금 없음)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, ExecuteDecision, HoldReason, RequestMeta
from ax_safety import cost_policy
from marketing_agent import cost_limits, handlers
from marketing_agent.media import cost_limit_store as store
from marketing_agent.media import policy_view
from marketing_agent.media.config import MediaSettings


def _item(key: str, scope: str, default: int, floor: int, top: int) -> dict:
    return {
        "key": key,
        "label": key,
        "scope": scope,
        "unit": "krw",
        "op": ">=",
        "default": default,
        "value": default,
        "min": floor,
        "absolute_max": top,
        "source": "default",
    }


POLICY = {
    "editable": True,
    "items": [
        _item("video_cost_single", "video_item", 2000, 100, 10000),
        _item("media_cost_daily", "daily_all_media", 10000, 1000, 100000),
    ],
    "requests": [],
    "history": [],
}


class Gate:
    def __init__(self, status: str = "executed") -> None:
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        self.calls.append((action, params))
        extra = {"review_id": "rev_1", "hold_reason": HoldReason.OVER_THRESHOLD}
        return ExecuteDecision(
            execution_id="exe_1",
            grade="C" if self.status == "held" else "A",
            status=self.status,
            **(extra if self.status == "held" else {}),
        )


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(store.FILE_ENV, str(tmp_path / "cost_limits.json"))
    for name in (
        "MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM",
        "MARKETING_VIDEO_MAX_COST_KRW_PER_ITEM",
        "MARKETING_MEDIA_MONTHLY_BUDGET_KRW",
    ):
        monkeypatch.delenv(name, raising=False)

    async def fetch() -> dict | None:
        return POLICY

    monkeypatch.setattr(policy_view, "fetch", fetch)


def _req(inp: dict) -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_cl",
        token="mock:dashboard-api",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    return AgentRequest(meta=meta, skill="x", input=inp)


def _body(**over: Any) -> dict:
    return {"reason": "월 한도 조정", "updated_by": "민지", **over}


def _gate(monkeypatch: pytest.MonkeyPatch, status: str = "executed") -> Gate:
    gate = Gate(status)
    monkeypatch.setattr(cost_limits, "execute", gate.execute)
    return gate


async def _raise(**body: Any) -> Any:
    return await handlers.raise_media_cost_limits_skill(_req(_body(**body)))


async def _lower(**body: Any) -> Any:
    return await handlers.lower_media_cost_limits_skill(_req(_body(**body)))


def test_ui_values_beat_env_and_video_is_separate_from_image(monkeypatch: Any) -> None:
    monkeypatch.setenv("MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM", "500")
    monkeypatch.setenv("MARKETING_MEDIA_MONTHLY_BUDGET_KRW", "50000")
    s = MediaSettings.from_env()
    assert (s.item_limit("video"), s.item_limit("image"), s.item_limit("audio")) == (500,) * 3
    monkeypatch.setenv("MARKETING_VIDEO_MAX_COST_KRW_PER_ITEM", "1500")
    s = MediaSettings.from_env()
    assert (s.item_limit("video"), s.item_limit("image")) == (1500, 500)
    store.set_values({"video_per_item_krw": 1200, "monthly_budget_krw": 30000}, "a", "lower", "x")
    s = MediaSettings.from_env()  # 재시작 없이 반영(mtime 캐시)
    assert s.item_limit("video") == 1200
    assert s.item_limit("image") == 500
    assert s.monthly_budget_krw == 30000


def test_store_ignores_broken_or_out_of_range_values() -> None:
    path = store.store_path()
    path.write_text(
        '{"video_per_item_krw": {"value": 0}, "monthly_budget_krw": {"value": 99999999}}'
    )
    assert (store.video_per_item(), store.monthly_budget()) == (None, None)
    path.write_text("깨짐")
    assert store.video_per_item() is None


def test_absolute_video_cap_matches_safety_code_cap() -> None:
    top = cost_policy.EDITABLE["video_cost_single"].absolute_max
    assert store.ABSOLUTE_MAX_VIDEO_ITEM_KRW == top
    assert store.MIN_KRW == 1


@pytest.mark.asyncio
async def test_first_value_is_an_increase_needing_human_approval(monkeypatch: Any) -> None:
    gate = _gate(monkeypatch, "held")
    held = await _raise(video_per_item_krw=1500, monthly_budget_krw=50000)
    assert held.outcome == "held" and gate.calls[0][0] == "change_media_cost_limits"
    assert gate.calls[0][1]["requested_by"] == "민지"  # 요청자≠승인자 검사용
    assert store.video_per_item() is None
    assert [r["id"] for r in store.requests()] == ["rev_1"]
    gate.status = "executed"
    done = await _raise(video_per_item_krw=1500, monthly_budget_krw=50000)
    assert done.outcome == "raised" and store.video_per_item() == 1500
    assert store.requests() == []
    assert store.history()[0]["action"] == "raise"


@pytest.mark.asyncio
async def test_policy_ceiling_blocks_raise_before_any_gate(monkeypatch: Any) -> None:
    gate = _gate(monkeypatch)
    store.set_values({"video_per_item_krw": 1000, "monthly_budget_krw": 90000}, "a", "raise", "x")
    over = await _raise(video_per_item_krw=2000)
    assert over.outcome == "over_policy_ceiling" and over.output["policy_krw"] == 2000
    assert (await _raise(video_per_item_krw=1999)).outcome == "raised"
    assert len(gate.calls) == 1


@pytest.mark.asyncio
async def test_policy_unavailable_fails_closed_for_raise(monkeypatch: Any) -> None:
    async def none() -> None:
        return None

    monkeypatch.setattr(policy_view, "fetch", none)
    gate = _gate(monkeypatch)
    res = await _raise(video_per_item_krw=100)
    assert res.outcome == "policy_unavailable" and gate.calls == []


@pytest.mark.asyncio
async def test_validation_rules(monkeypatch: Any) -> None:
    gate = _gate(monkeypatch)
    store.set_values({"video_per_item_krw": 1000, "monthly_budget_krw": 50000}, "a", "raise", "x")
    cases = [
        ({"video_per_item_krw": 0}, "below_minimum"),
        ({"video_per_item_krw": 10001}, "over_absolute_max"),
        ({"monthly_budget_krw": 5_000_001}, "over_absolute_max"),
        ({"video_per_item_krw": 1000}, "no_change"),
        ({"video_per_item_krw": 500, "monthly_budget_krw": 60000}, "mixed_direction"),
        ({"monthly_budget_krw": 900}, "monthly_below_item"),
    ]
    for body, outcome in cases:
        assert (await _raise(**body)).outcome == outcome, body
    assert (await _raise(reason=" ", video_per_item_krw=1100)).outcome == "reason_required"
    assert (await _raise(video_per_item_krw=900)).outcome == "not_an_increase"
    assert (await _lower(video_per_item_krw=1100)).outcome == "not_a_decrease"
    assert gate.calls == []


@pytest.mark.asyncio
async def test_lowering_needs_approval_unless_self_approve_is_on(monkeypatch: Any) -> None:
    store.set_values({"video_per_item_krw": 1500, "monthly_budget_krw": 50000}, "a", "raise", "x")
    gate = _gate(monkeypatch, "held")
    off = await _lower(video_per_item_krw=1000)
    assert off.outcome == "held" and gate.calls[0][0] == "change_media_cost_limits"
    assert store.video_per_item() == 1500
    # 허용을 켜면(재무·총괄 설정) 주의사항 확인 뒤 A로 바로 적용된다
    gate.status = "executed"
    opt_body = {"lower_self_approve": True, "reason": "재무 승인", "updated_by": "총괄"}
    opt = await handlers.set_cost_limit_option_skill(_req(opt_body))
    assert opt.outcome == "option_saved" and store.lower_self_approve()
    needs = await _lower(video_per_item_krw=1000)
    assert needs.outcome == "confirmation_required" and store.video_per_item() == 1500
    done = await _lower(video_per_item_krw=1000, confirmed=True)
    assert done.outcome == "lowered" and store.video_per_item() == 1000
    assert gate.calls[-1][0] == "lower_media_cost_limits"
    assert [h["action"] for h in store.history()][:2] == ["lower", "option"]
    again = await handlers.set_cost_limit_option_skill(_req(opt_body))
    assert again.outcome == "no_change"


class Ledger:
    def month_total(self, month: str) -> int:
        return 40000


@pytest.mark.asyncio
async def test_monthly_below_month_total_is_a_warning(monkeypatch: Any) -> None:
    store.set_values({"video_per_item_krw": 500, "monthly_budget_krw": 50000}, "a", "raise", "x")
    store.set_option(True, "총괄", "허용")
    _gate(monkeypatch)

    async def run(**body: Any) -> Any:
        return await cost_limits.change(
            _req(_body(**body)),
            wanted="down",
            ledger=Ledger(),  # type: ignore[arg-type]
            from_decision=handlers._from_decision,
        )

    first = await run(monthly_budget_krw=30000)
    assert first.outcome == "confirmation_required" and first.output["warnings"]
    res = await run(monthly_budget_krw=30000, confirmed=True)
    assert res.outcome == "lowered"
    assert res.output["warnings"][0]["code"] == "monthly_below_month_total"


@pytest.mark.asyncio
async def test_get_state_shows_sources_policy_and_daily_note(monkeypatch: Any) -> None:
    monkeypatch.setenv("MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM", "400")
    res = await handlers.get_media_cost_limits_skill(_req({}))
    out = res.output
    assert out["limits"]["video_source"] == "env_item"
    assert out["limits"]["video_per_item_krw"] == 400
    items = {i["key"]: i for i in out["policy"]["items"]}
    assert items["video_cost_single"]["max_allowed"] == 1999
    assert "합산" in items["media_cost_daily"]["note"]
    assert out["options"]["lower_self_approve"] is False
