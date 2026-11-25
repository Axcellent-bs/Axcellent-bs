"""게시 일시 중지 스위치(DB): 게시만 멈추고 나머지는 그대로, 재시작 없이 반영, 실패는 fail-closed.

실제 Instagram·Redis는 쓰지 않는다(목 어댑터·가짜 관문).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import events
from ax_contracts.schemas import (
    AgentRequest,
    ExecuteDecision,
    HoldReason,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent import handlers, publish_control
from marketing_agent.brief_auto_handoff import AutoHandoffConfig, auto_handoff_once
from marketing_agent.media import skill as media_skill
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.sns_publish import instagram, pending, publisher
from marketing_agent.sns_publish.config import LIMITS_CONFIG_ENV
from marketing_agent.sns_publish.control import DEFAULT_ENV
from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter

CTX = "ctx_ctrl_0001"
SHA = "a" * 64
NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)


class FakeGate:
    def __init__(self) -> None:
        self.by_key: dict[str, ExecuteDecision] = {}
        self.calls: list[str] = []

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        key = meta.idempotency_key or ""
        self.calls.append(key)
        if key not in self.by_key:
            n = len(self.by_key) + 1
            self.by_key[key] = ExecuteDecision(
                execution_id=f"exe_{n}",
                grade="C",
                status="held",
                hold_reason=HoldReason.OVER_THRESHOLD,
                review_id=f"rev_{n}",
            )
        return self.by_key[key]

    def approve_all(self) -> None:
        for key, d in self.by_key.items():
            self.by_key[key] = d.model_copy(update={"status": "executed"})


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in (
        "MARKETING_SNS_PROVIDER",
        "REDIS_URL",
        "MARKETING_SNS_MAX_RETRIES",
        "MARKETING_SNS_STATUS_POLL_S",
        "MARKETING_SNS_STATUS_MAX_POLLS",
        "MARKETING_IMAGE_PROVIDER",
        "MARKETING_IMAGE_MODEL",
        "MARKETING_VIDEO_PROVIDER",
        "MARKETING_VIDEO_MODEL",
        LIMITS_CONFIG_ENV,
        DEFAULT_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MARKETING_SNS_PENDING_DB", str(tmp_path / "pending.db"))
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path / "media"))
    events.reset_mock_bus()
    handlers.reset_state()
    publisher.reset_runtime()
    instagram.reset_adapter()

    async def no_sleep(seconds: float) -> None:
        return None

    publisher.RT.sleep = no_sleep
    yield
    publisher.reset_runtime()
    instagram.reset_adapter()
    events.reset_mock_bus()


@pytest.fixture
def adapter() -> MockInstagramAdapter:
    fake = MockInstagramAdapter()
    instagram.set_adapter(fake)
    return fake


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> FakeGate:
    fake = FakeGate()
    monkeypatch.setattr(publisher, "execute", fake.execute)
    return fake


@pytest.fixture
def ctl() -> Path:
    """스위치를 사람이 DB에서 직접 바꾸는 것과 같다(같은 DB 파일, 재시작 없음)."""
    return Path(publisher.store().path)


def _pause(path: Path, reason: str = "계정 정지 재고 대기") -> None:
    publisher.store().control_set("instagram", True, reason, "tester")


def _resume(path: Path) -> None:
    publisher.store().control_set("instagram", False, "", "tester")


def _request(draft: str = "spd_abc") -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id=CTX,
            token="mock:orchestrator",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        skill="publish_sns",
        input={
            "post_draft_id": draft,
            "asset_ref": f"assets/{draft}.jpg",
            "asset_sha256": SHA,
            "caption": "캡션 이 글과 이미지는 AI로 만들었습니다.",
            "media_kind": "image",
            "share_to_feed": True,
        },
    )


def _row(draft: str = "spd_abc") -> pending.PendingPost:
    rows = [r for r in publisher.store()._query("") if r.post_draft_id == draft]  # noqa: SLF001
    assert len(rows) == 1
    return rows[0]


def _graph_calls(adapter: MockInstagramAdapter) -> int:
    return sum(adapter.calls.values())


# -- 중지 중 요청 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paused_still_asks_for_approval_and_never_touches_the_adapter(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    _pause(ctl)
    result = await publisher.publish_sns(_request())
    # 거절하지 않고 사람 승인(C)을 받으러 간다. 수동 게시로 이어진다
    assert result.status == TaskStatus.INPUT_REQUIRED and result.outcome == "held"
    assert result.output["pause_reason"] == "계정 정지 재고 대기"
    assert result.output["auto_publish_off"] is True  # 자동 게시만 꺼졌다
    assert result.output["manual_available"] is True
    assert len(gate.calls) == 1
    assert _graph_calls(adapter) == 0
    assert publisher.store().halt_latest() is None  # halt 상태를 새로 세우지 않는다


@pytest.mark.asyncio
async def test_without_a_control_row_publishing_is_unchanged(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    assert publisher.store().control_get("instagram") is None
    held = await publisher.publish_sns(_request())
    assert held.status == TaskStatus.INPUT_REQUIRED
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    assert _row().status == pending.PUBLISHED
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_other_channel_or_paused_false_does_not_pause(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    publisher.store().control_set("facebook", True, "다른 채널", "tester")
    assert (await publisher.publish_sns(_request())).status == TaskStatus.INPUT_REQUIRED
    _resume(ctl)
    assert (await publisher.publish_sns(_request("spd_2"))).status == TaskStatus.INPUT_REQUIRED


@pytest.mark.asyncio
async def test_env_default_pauses_only_while_there_is_no_row(
    gate: FakeGate, adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_ENV, "true")
    result = await publisher.publish_sns(_request())
    assert result.outcome == "held" and "초기 기본값" in result.output["pause_reason"]
    publisher.store().control_set("instagram", False, "", "tester")  # 행이 생기면 행이 우선
    assert (await publisher.publish_sns(_request())).status == TaskStatus.INPUT_REQUIRED


# -- 승인 뒤 중지 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_after_approval_keeps_the_row_approved_for_manual_publish(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    held = await publisher.publish_sns(_request())
    assert held.status == TaskStatus.INPUT_REQUIRED
    _pause(ctl)  # 승인 대기 중에 사람이 중지했다
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    row = _row()
    assert (row.status, row.failure_code) == (pending.APPROVED, None)  # 닫지 않고 수동 대기
    assert _graph_calls(adapter) == 0
    assert row.result["pause_reason"] == "계정 정지 재고 대기"
    assert row.result["auto_publish_off"] is True and row.result["manual_available"] is True
    assert row.result["manual_pending"] is True
    assert publisher.store().halt_latest() is None


@pytest.mark.asyncio
async def test_pause_between_container_and_media_publish_is_rechecked(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = await publisher.publish_sns(_request())
    assert held.status == TaskStatus.INPUT_REQUIRED
    real = publisher._prepare_container  # noqa: SLF001

    async def prepare_then_pause(row: pending.PendingPost, ad: Any) -> str:
        container = await real(row, ad)
        _pause(ctl)  # 컨테이너를 만든 직후 중지
        return container

    monkeypatch.setattr(publisher, "_prepare_container", prepare_then_pause)
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    assert _row().failure_code == "publish_paused"
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_switching_takes_effect_without_restart_and_resumes(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    assert (await publisher.publish_sns(_request("spd_1"))).status == TaskStatus.INPUT_REQUIRED
    _pause(ctl)
    assert (await publisher.publish_sns(_request("spd_2"))).output["auto_publish_off"] is True
    _resume(ctl)
    assert (await publisher.publish_sns(_request("spd_2"))).status == TaskStatus.INPUT_REQUIRED


@pytest.mark.asyncio
async def test_direct_sql_update_by_another_connection_is_picked_up(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    """급할 때 사람이 sqlite3로 직접 고치는 경로. 같은 프로세스는 다시 시작하지 않는다."""
    assert (await publisher.publish_sns(_request("spd_1"))).status == TaskStatus.INPUT_REQUIRED
    other = sqlite3.connect(ctl)
    other.execute(
        "INSERT INTO sns_publish_control(channel,paused,reason,updated_by,updated_at)"
        " VALUES('instagram',1,'긴급','수동',datetime('now'))"
    )
    other.commit()
    assert (await publisher.publish_sns(_request("spd_2"))).output["auto_publish_off"] is True
    other.execute("UPDATE sns_publish_control SET paused = 0 WHERE channel = 'instagram'")
    other.commit()
    other.close()
    assert (await publisher.publish_sns(_request("spd_2"))).status == TaskStatus.INPUT_REQUIRED


@pytest.mark.asyncio
async def test_pause_survives_a_restart(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    _pause(ctl)
    publisher.reset_runtime()
    assert (await publisher.publish_sns(_request())).output["auto_publish_off"] is True


# -- fail-closed ---------------------------------------------------------------------


def _break_control_table(path: Path) -> None:
    other = sqlite3.connect(path)
    other.execute("DROP TABLE sns_publish_control")
    other.commit()
    other.close()


@pytest.mark.asyncio
async def test_unreadable_db_blocks_publishing(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    _break_control_table(ctl)
    result = await publisher.publish_sns(_request())
    assert result.status == TaskStatus.FAILED and result.outcome == "publish_control_error"
    assert gate.calls == [] and _graph_calls(adapter) == 0


@pytest.mark.asyncio
async def test_db_broken_after_approval_blocks_media_publish(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    assert (await publisher.publish_sns(_request())).status == TaskStatus.INPUT_REQUIRED
    _break_control_table(ctl)
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    assert _row().failure_code == "publish_control_error"
    assert adapter.calls["media_publish"] == 0


# -- 스위치 스킬(get/set_publish_control): 안전 관문을 지난다 ---------------------------------


class SetGate:
    """관문 대역: 중지는 A로 바로 executed, 재개(resume=1)는 C로 held → 승인 뒤 executed."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        self.approved: set[str] = set()

    async def __call__(self, action, params, meta, evidence):  # noqa: ANN001
        key = meta.idempotency_key or ""
        self.seen.append({"action": action, "params": params, "key": key})
        if params["resume"] and key not in self.approved:
            return ExecuteDecision(
                execution_id="exe_s1",
                grade="C",
                status="held",
                hold_reason=HoldReason.OVER_THRESHOLD,
                review_id="rev_s1",
            )
        return ExecuteDecision(
            execution_id="exe_s2", grade="C" if params["resume"] else "A", status="executed"
        )


def _control_request(skill: str, **input_: Any) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_cmp_publish_control",
            token="mock:dashboard-api",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        skill=skill,
        input=input_,
    )


@pytest.fixture
def set_gate(monkeypatch: pytest.MonkeyPatch) -> SetGate:
    fake = SetGate()
    monkeypatch.setattr(publish_control, "execute", fake)
    return fake


@pytest.mark.asyncio
async def test_set_pause_goes_through_the_gate_as_a_and_leaves_an_audit_trail(
    set_gate: SetGate,
) -> None:
    out = await handlers.set_publish_control_skill(
        _control_request("set_publish_control", paused=True, reason="계정 정지", updated_by="재헌")
    )
    assert out.outcome == "updated" and out.output["paused"] is True
    assert set_gate.seen[0]["action"] == "set_publish_control"
    assert set_gate.seen[0]["params"]["resume"] == 0  # 중지는 규칙에 걸리지 않는다
    row = publisher.store().control_get("instagram")
    assert row is not None and row[0] is True and row[1] == "계정 정지" and row[2] == "재헌"
    log = publisher.store().control_log("instagram")
    assert log[0]["updated_by"] == "재헌" and log[0]["execution_id"] == "exe_s2"
    assert log[0]["paused"] is True and log[0]["updated_at"]
    read = await handlers.get_publish_control_skill(_control_request("get_publish_control"))
    assert read.output["paused"] is True and read.output["history"][0]["updated_by"] == "재헌"
    assert read.output["auto_publish_off"] is True and read.output["manual_available"] is False


@pytest.fixture
def ticking_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """스위치 저장 시각을 부를 때마다 1초씩 가는 고정 시계로 (11/25).

    승인 키에 "현재 상태 시각(updated_at)"이 들어간다. 실제 시계는 Windows에서 약 15.6ms 단위라
    시험 안의 두 번 정지(control_set)가 같은 시각이 되면 옛 승인이 다음 재개에 다시 쓰였다."""
    start = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
    ticks = iter(range(10_000))

    def now() -> str:
        return (start + timedelta(seconds=next(ticks))).isoformat()

    monkeypatch.setattr(pending, "_now", now)


@pytest.mark.asyncio
async def test_resume_is_held_until_a_person_approves_then_applies(
    set_gate: SetGate, ticking_clock: None
) -> None:
    publisher.store().control_set("instagram", True, "계정 정지", "재헌")
    resume = _control_request("set_publish_control", paused=False, updated_by="재현")
    held = await handlers.set_publish_control_skill(resume)
    assert held.outcome == "held" and held.output["review_id"] == "rev_s1"
    row = publisher.store().control_get("instagram")
    assert row is not None and row[0] is True  # 아직 중지
    set_gate.approved.add(set_gate.seen[0]["key"])  # 사람이 승인
    done = await handlers.set_publish_control_skill(resume)
    assert done.outcome == "updated" and done.output["paused"] is False
    log = publisher.store().control_log("instagram")
    assert [e["updated_by"] for e in log] == ["재현", "재헌"]
    # 상태가 바뀌었으니 옛 승인은 다음 재개에 쓰이지 않는다
    publisher.store().control_set("instagram", True, "다시 정지", "재헌")
    again = await handlers.set_publish_control_skill(resume)
    assert again.outcome == "held"


@pytest.mark.asyncio
async def test_set_same_state_is_unchanged_and_bad_input_is_rejected(set_gate: SetGate) -> None:
    request = _control_request("set_publish_control", paused=True, reason="x", updated_by="재헌")
    assert (await handlers.set_publish_control_skill(request)).outcome == "updated"
    assert (await handlers.set_publish_control_skill(request)).outcome == "unchanged"
    assert len(set_gate.seen) == 1
    for bad in (
        {"paused": "yes", "updated_by": "재헌"},
        {"paused": True},  # 이름 없음
        {"paused": True, "updated_by": "재헌", "channel": "tiktok"},
        {"paused": True, "updated_by": "재헌", "reason": "x" * 201},
    ):
        out = await handlers.set_publish_control_skill(
            _control_request("set_publish_control", **bad)
        )
        assert out.outcome == "invalid_input"
    assert len(set_gate.seen) == 1


class ReviewGate:
    """safety 대역: 같은 키면 이전 판정을 돌려준다. 재개는 키마다 보류 하나를 만들고,
    보류가 닫히면 safety처럼 판정을 `executed`(승인) 또는
    `rejected`(만료·거절, review_id 남김)로 바꾼다."""

    def __init__(self) -> None:
        self.by_key: dict[str, ExecuteDecision] = {}
        self.keys: list[str] = []

    async def __call__(self, action, params, meta, evidence):  # noqa: ANN001
        key = meta.idempotency_key or ""
        self.keys.append(key)
        if key not in self.by_key:
            n = len(self.by_key) + 1
            self.by_key[key] = (
                ExecuteDecision(
                    execution_id=f"exe_r{n}",
                    grade="C",
                    status="held",
                    hold_reason=HoldReason.OVER_THRESHOLD,
                    review_id=f"rev_r{n}",
                )
                if params["resume"]
                else ExecuteDecision(execution_id=f"exe_r{n}", grade="A", status="executed")
            )
        return self.by_key[key]

    def close(self, review_id: str, status: str) -> None:
        key, old = next((k, d) for k, d in self.by_key.items() if d.review_id == review_id)
        if status == "approved":
            self.by_key[key] = old.model_copy(update={"status": "executed"})
        else:
            reason = HoldReason.REVIEW_TIMEOUT if status == "expired" else old.hold_reason
            self.by_key[key] = old.model_copy(update={"status": "rejected", "hold_reason": reason})

    def reviews(self) -> list[str]:
        return [d.review_id for d in self.by_key.values() if d.review_id]


@pytest.fixture
def review_gate(monkeypatch: pytest.MonkeyPatch) -> ReviewGate:
    fake = ReviewGate()
    monkeypatch.setattr(publish_control, "execute", fake)
    return fake


def _resume_request() -> AgentRequest:
    return _control_request("set_publish_control", paused=False, updated_by="재현")


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", ["expired", "rejected"])
async def test_resume_after_an_expired_or_rejected_review_asks_for_a_new_approval(
    review_gate: ReviewGate, closed: str
) -> None:
    publisher.store().control_set("instagram", True, "계정 정지", "재헌")
    first = await handlers.set_publish_control_skill(_resume_request())
    assert first.outcome == "held" and first.output["review_id"] == "rev_r1"
    review_gate.close("rev_r1", closed)  # 승인 없이 10분 지남 / 사람이 거절
    again = await handlers.set_publish_control_skill(_resume_request())
    assert again.outcome == "held" and again.output["review_id"] == "rev_r2"  # 새 사람 승인
    assert review_gate.keys[1] == review_gate.keys[0] and review_gate.keys[2] != review_gate.keys[0]
    row = publisher.store().control_get("instagram")
    assert row is not None and row[0] is True  # 거절·만료를 몰래 통과시키지 않는다
    review_gate.close("rev_r2", "approved")
    done = await handlers.set_publish_control_skill(_resume_request())
    assert done.outcome == "updated" and done.output["paused"] is False
    assert review_gate.reviews() == ["rev_r1", "rev_r2"]


@pytest.mark.asyncio
async def test_resume_while_waiting_keeps_the_same_key_and_one_review(
    review_gate: ReviewGate,
) -> None:
    publisher.store().control_set("instagram", True, "계정 정지", "재헌")
    for _ in range(3):
        out = await handlers.set_publish_control_skill(_resume_request())
        assert out.outcome == "held" and out.output["review_id"] == "rev_r1"
    assert len(set(review_gate.keys)) == 1 and review_gate.reviews() == ["rev_r1"]
    review_gate.close("rev_r1", "approved")  # 승인 뒤 같은 요청이 같은 키로 이어진다
    done = await handlers.set_publish_control_skill(_resume_request())
    assert done.outcome == "updated" and done.output["paused"] is False
    assert len(set(review_gate.keys)) == 1


@pytest.mark.asyncio
async def test_retry_after_a_closed_review_happens_at_most_once_per_request(
    review_gate: ReviewGate, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher.store().control_set("instagram", True, "계정 정지", "재헌")
    closed = ExecuteDecision(
        execution_id="exe_x",
        grade="C",
        status="rejected",
        hold_reason=HoldReason.REVIEW_TIMEOUT,
        review_id="rev_x",
    )
    calls: list[str] = []

    async def always_closed(action, params, meta, evidence):  # noqa: ANN001
        calls.append(meta.idempotency_key or "")
        return closed

    monkeypatch.setattr(publish_control, "execute", always_closed)
    out = await handlers.set_publish_control_skill(_resume_request())
    assert out.outcome == "execute_rejected" and len(calls) == 2 and calls[0] != calls[1]


@pytest.mark.asyncio
async def test_rejection_without_a_review_does_not_retry_and_pause_stays_immediate(
    review_gate: ReviewGate, monkeypatch: pytest.MonkeyPatch
) -> None:
    pause = _control_request("set_publish_control", paused=True, reason="정지", updated_by="재헌")
    out = await handlers.set_publish_control_skill(pause)
    assert out.outcome == "updated" and out.output["paused"] is True and len(review_gate.keys) == 1
    calls: list[str] = []

    async def unavailable(action, params, meta, evidence):  # noqa: ANN001
        calls.append(meta.idempotency_key or "")
        return ExecuteDecision(execution_id="exe_unavailable", grade="C", status="rejected")

    monkeypatch.setattr(publish_control, "execute", unavailable)
    out = await handlers.set_publish_control_skill(_resume_request())
    assert out.outcome == "execute_rejected" and len(calls) == 1  # safety 무응답은 새 키로 안 감
    assert publisher.store().control_attempt("instagram") == 0


@pytest.mark.asyncio
async def test_set_and_get_fail_closed_when_the_db_is_unreadable(
    set_gate: SetGate, ctl: Path
) -> None:
    _break_control_table(ctl)
    out = await handlers.set_publish_control_skill(
        _control_request("set_publish_control", paused=True, updated_by="재헌")
    )
    assert out.outcome == "publish_control_error" and set_gate.seen == []
    read = await handlers.get_publish_control_skill(_control_request("get_publish_control"))
    assert read.outcome == "publish_control_error"


# -- 다른 기능은 중지 중에도 돈다 ------------------------------------------------------


def _brief(pid: str) -> None:
    handlers.STATE.briefs.add(
        CampaignBriefInput(
            schema_version="1",
            proposal_id=pid,
            title="10대 그림책 릴스",
            idea="10대에게 릴스로 그림책 신간을 알린다",
            why="12월 그림책 검색이 늘었다",
            facts=[],
            target=None,
            products=[],
            approved_by="tester",
        ),
        NOW,
    )


@pytest.mark.asyncio
async def test_drafts_and_auto_handoff_keep_working_while_paused(
    ctl: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pause(ctl)

    async def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("자동 넘김이 생성·게시를 불렀다")

    monkeypatch.setattr(handlers, "generate_media_flow", forbidden)
    monkeypatch.setattr(handlers, "publish_sns", forbidden)
    monkeypatch.setattr(handlers, "execute", forbidden)
    _brief("P1")
    on = AutoHandoffConfig(enabled=True, poll_s=60, retry_s=300, max_attempts=3)
    counts = await auto_handoff_once(handlers.STATE.briefs, handlers.handoff_brief_to_sns, on, NOW)
    assert counts["handed"] == 1
    record = handlers.STATE.briefs.handoff("P1")
    assert record is not None and record.status == "handed"
    assert handlers.STATE.sns.get(record.draft_id) is not None  # SNS 초안이 만들어졌다


@pytest.mark.asyncio
async def test_media_generation_keeps_working_while_paused(
    ctl: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pause(ctl)

    async def gate(action, params, context, evidence):  # noqa: ANN001
        return ExecuteDecision(execution_id="exe_m1", grade="B", status="executed")

    monkeypatch.setattr(media_skill, "execute", gate)
    request = AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "generate_media",
            "input": {
                "post_draft_id": "DRAFT-1",
                "media_kind": "image",
                "scene_template_id": "cat_bookstore_image",
                "fields": {"season": "spring", "color_theme": "soft pastel", "category": "그림책"},
            },
        }
    )
    out = await media_skill.generate_media(
        request, ledger=MediaLedger(), from_decision=handlers._from_decision
    )
    assert out.outcome == "generated"


# -- Graph 오류 368 ---------------------------------------------------------------------


@pytest.mark.parametrize("subcode", [None, 0, 2207050])
def test_graph_error_368_maps_to_the_restriction_family(subcode: int | None) -> None:
    failure, action = instagram.classify(368, subcode)
    assert (failure, action) == ("ig_account_restricted", "fail")
    assert failure in instagram.HALT_FAILURES


@pytest.mark.asyncio
async def test_error_368_on_media_publish_halts_later_publishing(
    gate: FakeGate, adapter: MockInstagramAdapter, ctl: Path
) -> None:
    adapter.fail_on("media_publish", 368, None, times=1)
    assert (await publisher.publish_sns(_request())).status == TaskStatus.INPUT_REQUIRED
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    assert _row().failure_code == "ig_account_restricted"
    assert (await publisher.publish_sns(_request("spd_other"))).outcome == "publish_halted"
