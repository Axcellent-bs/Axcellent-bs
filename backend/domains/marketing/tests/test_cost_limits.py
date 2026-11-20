"""우리 키 사용량 예산 화면 설정: 우선순위·검증·승인 경로 (가짜 관문·가짜 정책, 과금 없음)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, ExecuteDecision, HoldReason, RequestMeta
from ax_safety import cost_policy
from marketing_agent import cost_limits, handlers
from marketing_agent.media import policy_view
from marketing_agent.media import quota_store as store
from marketing_agent.media.config import MediaSettings

POLICY = {
    "editable": True,
    "items": [
        {
            "key": "video_duration",
            "label": "영상 길이",
            "scope": "video_duration",
            "unit": "s",
            "op": ">",
            "default": 90,
            "value": 90,
            "min": 1,
            "absolute_max": 90,
            "source": "default",
        }
    ],
    "requests": [],
    "history": [],
}

QUOTA_ENVS = (
    "MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY",
    "MARKETING_QUOTA_IMAGE_COUNT_MONTHLY",
    "MARKETING_QUOTA_AUDIO_COUNT_MONTHLY",
    "MARKETING_MEDIA_KEY_OWNER",
)


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
    for name in QUOTA_ENVS:
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
    return {"reason": "월 예산 조정", "updated_by": "민지", **over}


def _gate(monkeypatch: pytest.MonkeyPatch, status: str = "executed") -> Gate:
    gate = Gate(status)
    monkeypatch.setattr(cost_limits, "execute", gate.execute)
    return gate


async def _raise(**body: Any) -> Any:
    return await handlers.raise_media_cost_limits_skill(_req(_body(**body)))


async def _lower(**body: Any) -> Any:
    return await handlers.lower_media_cost_limits_skill(_req(_body(**body)))


def _seed(**values: int) -> None:
    store.set_values(values, "a", "raise", "x")


def test_ui_values_beat_env_and_each_kind_has_its_own_unit(monkeypatch: Any) -> None:
    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "600")
    monkeypatch.setenv("MARKETING_QUOTA_IMAGE_COUNT_MONTHLY", "200")
    s = MediaSettings.from_env()
    assert (s.quota("video"), s.quota("image"), s.quota("audio")) == (600, 200, None)
    store.set_values({"video_seconds_monthly": 300, "audio_count_monthly": 20}, "a", "lower", "x")
    s = MediaSettings.from_env()  # 재시작 없이 반영(mtime 캐시)
    assert (s.quota("video"), s.quota("image"), s.quota("audio")) == (300, 200, 20)


def test_customer_keys_have_no_quota_and_unknown_owner_means_server(monkeypatch: Any) -> None:
    assert MediaSettings.from_env().quota_applies is True  # 기본 server
    monkeypatch.setenv("MARKETING_MEDIA_KEY_OWNER", "customer")
    assert MediaSettings.from_env().quota_applies is False
    monkeypatch.setenv("MARKETING_MEDIA_KEY_OWNER", "oops")
    assert MediaSettings.from_env().quota_applies is True  # 모르는 값은 예산을 건다


def test_store_ignores_broken_out_of_range_and_old_krw_values() -> None:
    path = store.store_path()
    path.write_text(
        '{"video_seconds_monthly": {"value": 0}, "image_count_monthly": {"value": 999999999},'
        ' "video_per_item_krw": {"value": 1500}, "monthly_budget_krw": {"value": 50000}}'
    )
    assert [store.quota(k) for k in ("video", "image", "audio")] == [None, None, None]
    path.write_text("깨짐")
    assert store.quota("video") is None


def test_old_krw_requests_are_not_listed() -> None:
    store.add_request({"id": "old", "payload": {"video_per_item_krw": 1500}})
    store.add_request({"id": "new", "payload": {"video_seconds_monthly": 600}})
    assert [r["id"] for r in store.requests()] == ["new"]


def test_no_money_rules_are_left_in_the_editable_policy() -> None:
    assert list(cost_policy.EDITABLE) == ["video_duration"]
    assert store.MIN_AMOUNT == 1


@pytest.mark.asyncio
async def test_first_value_is_an_increase_needing_human_approval(monkeypatch: Any) -> None:
    gate = _gate(monkeypatch, "held")
    held = await _raise(video_seconds_monthly=600, image_count_monthly=200)
    assert held.outcome == "held" and gate.calls[0][0] == "change_media_cost_limits"
    assert gate.calls[0][1]["requested_by"] == "민지"  # 요청자≠승인자 검사용
    assert store.quota("video") is None
    assert [r["id"] for r in store.requests()] == ["rev_1"]
    gate.status = "executed"
    done = await _raise(video_seconds_monthly=600, image_count_monthly=200)
    assert done.outcome == "raised" and store.quota("video") == 600
    assert store.quota("image") == 200
    assert store.requests() == []
    assert store.history()[0]["action"] == "raise"


@pytest.mark.asyncio
async def test_validation_rules(monkeypatch: Any) -> None:
    gate = _gate(monkeypatch)
    _seed(video_seconds_monthly=600, image_count_monthly=200)
    cases = [
        ({"video_seconds_monthly": 0}, "below_minimum"),
        ({"video_seconds_monthly": 360_001}, "over_absolute_max"),
        ({"image_count_monthly": 100_001}, "over_absolute_max"),
        ({"audio_count_monthly": 20_001}, "over_absolute_max"),
        ({"video_seconds_monthly": 600}, "no_change"),
        ({"video_seconds_monthly": 300, "image_count_monthly": 300}, "mixed_direction"),
        ({"video_per_item_krw": 1500}, "deprecated_krw_limit"),
        ({"monthly_budget_krw": 50000}, "deprecated_krw_limit"),
    ]
    for body, outcome in cases:
        assert (await _raise(**body)).outcome == outcome, body
    assert (await _raise(reason=" ", video_seconds_monthly=700)).outcome == "reason_required"
    assert (await _raise(video_seconds_monthly=500)).outcome == "not_an_increase"
    assert (await _lower(video_seconds_monthly=700)).outcome == "not_a_decrease"
    assert (await _raise()).outcome == "invalid_input"
    assert gate.calls == []


@pytest.mark.asyncio
async def test_raising_has_no_policy_ceiling_and_needs_no_policy_server(monkeypatch: Any) -> None:
    """원화 정책 상한이 없어졌으니 안전 서버를 못 읽어도 상향 요청은 관문까지 간다."""

    async def none() -> None:
        return None

    monkeypatch.setattr(policy_view, "fetch", none)
    gate = _gate(monkeypatch)
    res = await _raise(video_seconds_monthly=100)
    assert res.outcome == "raised" and len(gate.calls) == 1


@pytest.mark.asyncio
async def test_lowering_needs_approval_unless_self_approve_is_on(monkeypatch: Any) -> None:
    _seed(video_seconds_monthly=900, image_count_monthly=200)
    gate = _gate(monkeypatch, "held")
    off = await _lower(video_seconds_monthly=600)
    assert off.outcome == "held" and gate.calls[0][0] == "change_media_cost_limits"
    assert store.quota("video") == 900
    # 허용을 켜면(재무·총괄 설정) 주의사항 확인 뒤 A로 바로 적용된다
    gate.status = "executed"
    opt_body = {"lower_self_approve": True, "reason": "재무 승인", "updated_by": "총괄"}
    opt = await handlers.set_cost_limit_option_skill(_req(opt_body))
    assert opt.outcome == "option_saved" and store.lower_self_approve()
    needs = await _lower(video_seconds_monthly=600)
    assert needs.outcome == "confirmation_required" and store.quota("video") == 900
    done = await _lower(video_seconds_monthly=600, confirmed=True)
    assert done.outcome == "lowered" and store.quota("video") == 600
    assert gate.calls[-1][0] == "lower_media_cost_limits"
    assert [h["action"] for h in store.history()][:2] == ["lower", "option"]
    again = await handlers.set_cost_limit_option_skill(_req(opt_body))
    assert again.outcome == "no_change"


class Ledger:
    def month_usage_all(self, month: str) -> dict[str, int]:
        return {"video": 400, "image": 10, "audio": 1}


@pytest.mark.asyncio
async def test_budget_below_month_usage_is_a_warning(monkeypatch: Any) -> None:
    _seed(video_seconds_monthly=900, image_count_monthly=200)
    store.set_option(True, "총괄", "허용")
    _gate(monkeypatch)

    async def run(**body: Any) -> Any:
        return await cost_limits.change(
            _req(_body(**body)),
            wanted="down",
            ledger=Ledger(),  # type: ignore[arg-type]
            from_decision=handlers._from_decision,
        )

    first = await run(video_seconds_monthly=300)
    assert first.outcome == "confirmation_required" and first.output["warnings"]
    res = await run(video_seconds_monthly=300, confirmed=True)
    assert res.outcome == "lowered"
    warning = res.output["warnings"][0]
    assert warning["code"] == "quota_below_month_usage"
    assert (warning["media_kind"], warning["unit"]) == ("video", "seconds")
    assert warning["month_used"] == 400


@pytest.mark.asyncio
async def test_get_state_shows_sources_remaining_percent_and_policy(monkeypatch: Any) -> None:
    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "800")
    store.set_values({"image_count_monthly": 100}, "a", "raise", "x")
    res = await cost_limits.get_limits(_req({}), ledger=Ledger())  # type: ignore[arg-type]
    out = res.output
    assert out["limits"]["video_source"] == "env" and out["limits"]["video_seconds_monthly"] == 800
    assert out["limits"]["image_source"] == "ui" and out["limits"]["audio_source"] == "none"
    assert out["limits"]["quota_applies"] is True
    assert out["month_total_krw"] is None  # 원화는 더 세지 않는다
    assert out["usage"]["video"] == {
        "unit": "seconds",
        "used": 400,
        "limit": 800,
        "remaining_pct": 50,
    }
    assert out["usage"]["image"]["remaining_pct"] == 90
    assert out["usage"]["audio"]["limit"] is None and out["usage"]["audio"]["remaining_pct"] is None
    assert out["bounds"]["units"]["video_seconds_monthly"] == "seconds"
    items = {i["key"]: i for i in out["policy"]["items"]}
    assert list(items) == ["video_duration"]
    assert out["options"]["lower_self_approve"] is False
