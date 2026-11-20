"""수동 게시: 자동 게시가 꺼진 동안 승인된 건을 사람이 직접 올리고 링크를 기록해야 닫힌다.

실제 Instagram·Redis는 쓰지 않는다(목 어댑터·가짜 관문). 링크 진위는 확인하지 않는다(형식만).
"""

from __future__ import annotations

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
from marketing_agent import brief_publish, handlers, manual_publish
from marketing_agent.sns_publish import instagram, pending, publisher
from marketing_agent.sns_publish.config import LIMITS_CONFIG_ENV
from marketing_agent.sns_publish.control import DEFAULT_ENV
from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter
from marketing_agent.sns_publish.permalink import normalize_permalink

CTX = "ctx_manual_0001"
SHA = "b" * 64
LINK = "https://www.instagram.com/p/CxAbc123_-/"
CAPTION = "마케팅 글 이 글과 이미지는 AI로 만들었습니다.\n#캣북스 #고양이책"
STATE_DATA = {
    "post_draft_id": "spd_m1",
    "caption": CAPTION,
    "asset_sha256": SHA,
    "media_kind": "image",
    "share_to_feed": True,
}


class FakeGate:
    """publish_sns용: 처음엔 held, approve_all 뒤엔 executed."""

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


class DoneGate:
    """complete_manual_publish용 A등급 관문 대역."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = "executed"

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        self.calls.append((action, params))
        if self.status == "held":
            return ExecuteDecision(
                execution_id="exe_done",
                grade="C",
                status="held",
                hold_reason=HoldReason.OVER_THRESHOLD,
                review_id="rev_done",
            )
        return ExecuteDecision(execution_id="exe_done", grade="A", status="executed")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in ("MARKETING_SNS_PROVIDER", "REDIS_URL", LIMITS_CONFIG_ENV, DEFAULT_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MARKETING_SNS_PENDING_DB", str(tmp_path / "pending.db"))
    events.reset_mock_bus()
    handlers.reset_state()
    publisher.reset_runtime()
    instagram.reset_adapter()
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
def done(monkeypatch: pytest.MonkeyPatch) -> DoneGate:
    fake = DoneGate()
    monkeypatch.setattr(manual_publish, "execute", fake.execute)
    return fake


def _meta() -> RequestMeta:
    return RequestMeta(
        context_id=CTX,
        token="mock:dashboard-api",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _publish_request(draft: str = "spd_m1", ref: str | None = None) -> AgentRequest:
    return AgentRequest(
        meta=_meta(),
        skill="publish_sns",
        input={
            "post_draft_id": draft,
            "asset_ref": ref or f"med_{draft}/labeled.jpg",
            "asset_sha256": SHA,
            "caption": CAPTION,
            "media_kind": "image",
            "share_to_feed": True,
        },
    )


def _complete(draft: str = "spd_m1", **over: Any) -> AgentRequest:
    body: dict[str, Any] = {
        "post_draft_id": draft,
        "permalink": LINK,
        "ai_label_confirmed": True,
        "posted_by": "이재헌",
    }
    return AgentRequest(meta=_meta(), skill="complete_manual_publish", input=body | over)


def _package_request(draft: str = "spd_m1") -> AgentRequest:
    return AgentRequest(
        meta=_meta(), skill="get_manual_publish_package", input={"post_draft_id": draft}
    )


def _row(draft: str = "spd_m1") -> pending.PendingPost:
    rows = publisher.store().with_draft(draft)
    assert len(rows) == 1
    return rows[0]


def _pause() -> None:
    publisher.store().control_set("instagram", True, "계정 정지", "tester")


async def _manual_pending(
    gate: FakeGate, draft: str = "spd_m1", ref: str | None = None
) -> pending.PendingPost:
    _pause()
    held = await publisher.publish_sns(_publish_request(draft, ref))
    assert held.outcome == "held"
    gate.approve_all()
    await publisher.apply_decision(_row(draft), "approved", "event")
    row = _row(draft)
    assert row.status == pending.APPROVED and row.result["manual_pending"] is True
    return row


async def _complete_ok(draft: str = "spd_m1", **over: Any):
    return await manual_publish.complete_manual_publish(
        _complete(draft, **over),
        from_decision=handlers._from_decision,  # noqa: SLF001
    )


# -- 링크 형식 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.instagram.com/p/CxAbc123_-/", "https://www.instagram.com/p/CxAbc123_-/"),
        ("http://instagram.com/reel/CxAbc123/", "https://www.instagram.com/reel/CxAbc123/"),
        ("https://www.instagram.com/tv/CxAbc123", "https://www.instagram.com/tv/CxAbc123/"),
        (
            "  https://www.instagram.com/p/CxAbc123/?igsh=abc&utm=1#x ",
            "https://www.instagram.com/p/CxAbc123/",
        ),
        ("HTTPS://WWW.INSTAGRAM.COM/p/CxAbc123/", "https://www.instagram.com/p/CxAbc123/"),
    ],
)
def test_permalink_is_normalized(raw: str, expected: str) -> None:
    assert normalize_permalink(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "CxAbc123",
        "https://example.com/p/CxAbc123/",
        "https://instagram.com.evil.com/p/CxAbc123/",
        "https://evil.com/https://www.instagram.com/p/CxAbc123/",
        "https://m.instagram.com/p/CxAbc123/",
        "https://user:pw@www.instagram.com/p/CxAbc123/",
        "https://www.instagram.com:8443/p/CxAbc123/",
        "ftp://www.instagram.com/p/CxAbc123/",
        "https://www.instagram.com/",
        "https://www.instagram.com/someone/",
        "https://www.instagram.com/stories/someone/123/",
        "https://www.instagram.com/p/",
        "https://www.instagram.com/p/ab/",
        "https://www.instagram.com/p/Cx Abc/",
        "https://www.instagram.com/p/Cx$Abc123/",
        "https://www.instagram.com/p/CxAbc123/extra/",
        "https://www.instagram.com/p/CxAbc123//",
        "javascript:alert(1)",
    ],
)
def test_bad_permalinks_are_rejected(raw: object) -> None:
    assert normalize_permalink(raw) is None


# -- 스위치 꺼짐 + 승인 → 수동 대기 ------------------------------------------------------


@pytest.mark.asyncio
async def test_paused_approval_keeps_approved_and_answers_manual_pending(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    row = await _manual_pending(gate)
    assert row.result["manual_available"] is True and row.result["auto_publish_off"] is True
    assert sum(adapter.calls.values()) == 0  # Graph를 한 번도 부르지 않았다
    again = await publisher.publish_sns(_publish_request())  # 같은 요청 재전송 = 상태 조회
    assert again.outcome == "manual_publish_pending"
    assert again.status == TaskStatus.INPUT_REQUIRED
    assert again.output["manual_pending"] is True and again.output["manual_available"] is True


@pytest.mark.asyncio
async def test_manual_pending_rows_are_not_auto_published_after_the_switch_is_on(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    await _manual_pending(gate)
    publisher.store().control_set("instagram", False, "", "tester")
    await publisher.recover_interrupted()  # 재시작 복구도 자동으로 올리지 않는다
    assert _row().status == pending.APPROVED
    assert sum(adapter.calls.values()) == 0


@pytest.mark.asyncio
async def test_without_the_switch_auto_publish_is_unchanged(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    held = await publisher.publish_sns(_publish_request())
    assert held.outcome == "held" and "manual_available" not in held.output
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    row = _row()
    assert row.status == pending.PUBLISHED and not row.result.get("manual")
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_paused_works_without_an_instagram_adapter(gate: FakeGate) -> None:
    _pause()  # 어댑터를 세우지 않았다(실제 모드 설정 없음)
    held = await publisher.publish_sns(_publish_request())
    assert held.outcome == "held"
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    assert _row().result["manual_pending"] is True


@pytest.mark.asyncio
async def test_daily_limit_still_applies_while_paused(
    gate: FakeGate, adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(publisher, "_local_limit_check", lambda: "daily_limit_reached")
    _pause()
    result = await publisher.publish_sns(_publish_request())
    assert result.outcome == "daily_limit_reached" and gate.calls == []


# -- 패키지 --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_package_carries_the_approved_caption_and_no_secrets(gate: FakeGate) -> None:
    await _manual_pending(gate)
    result = await manual_publish.get_manual_publish_package(_package_request())
    assert result.status == TaskStatus.COMPLETED and result.outcome == "package"
    out = result.output
    assert out["caption"] == CAPTION and out["hashtags"] == ["#캣북스", "#고양이책"]
    assert out["ai_label_in_caption"] is True and out["manual_pending"] is True
    assert out["media_kind"] == "image"
    assert out["media"] == [
        {
            "index": 1,
            "asset_ref": "med_spd_m1/labeled.jpg",
            "file_name": "labeled.jpg",
            "asset_sha256": SHA,
        }
    ]
    assert out["permalink_verified"] is False
    text = str(out).lower()
    assert "token" not in text and "secret" not in text


@pytest.mark.asyncio
async def test_package_hides_absolute_server_paths(gate: FakeGate) -> None:
    await _manual_pending(gate, ref="C:\\srv\\media\\med_x\\labeled.jpg")
    out = (await manual_publish.get_manual_publish_package(_package_request())).output
    assert out["media"][0]["asset_ref"] == "med_x/labeled.jpg"
    assert "srv" not in str(out)


@pytest.mark.asyncio
async def test_package_is_refused_when_nothing_is_waiting() -> None:
    result = await manual_publish.get_manual_publish_package(_package_request("nope"))
    assert result.status == TaskStatus.FAILED and result.outcome == "not_manual_pending"


# -- 완료 기록: 거절 --------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "link",
    ["", "   ", "not a link", "https://example.com/p/CxAbc123/", "https://www.instagram.com/p/ab/"],
)
async def test_missing_or_bad_link_is_refused_and_stays_approved(
    gate: FakeGate, done: DoneGate, link: str
) -> None:
    await _manual_pending(gate)
    result = await _complete_ok(permalink=link)
    assert result.status == TaskStatus.FAILED and result.outcome == "invalid_permalink"
    assert _row().status == pending.APPROVED and done.calls == []


@pytest.mark.asyncio
async def test_a_request_without_the_link_field_is_refused(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate)
    request = _complete()
    del request.input["permalink"]
    result = await manual_publish.complete_manual_publish(
        request,
        from_decision=handlers._from_decision,  # noqa: SLF001
    )
    assert result.outcome == "invalid_permalink" and _row().status == pending.APPROVED


@pytest.mark.asyncio
async def test_ai_label_must_be_confirmed(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate)
    result = await _complete_ok(ai_label_confirmed=False)
    assert result.status == TaskStatus.FAILED and result.outcome == "ai_label_not_confirmed"
    for value in ("true", None):
        assert (await _complete_ok(ai_label_confirmed=value)).outcome == "invalid_input"
    assert _row().status == pending.APPROVED and done.calls == []


@pytest.mark.asyncio
async def test_future_time_is_refused(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate)
    later = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert (await _complete_ok(published_at=later)).outcome == "published_at_in_future"
    naive = datetime.now().replace(tzinfo=None).isoformat()
    assert (await _complete_ok(published_at=naive)).outcome == "invalid_input"
    assert _row().status == pending.APPROVED


@pytest.mark.asyncio
async def test_unknown_or_not_manual_rows_are_refused(gate: FakeGate, done: DoneGate) -> None:
    assert (await _complete_ok("none")).outcome == "not_found"
    held = await publisher.publish_sns(_publish_request())  # held(아직 승인 전)
    assert held.outcome == "held"
    result = await _complete_ok()
    assert result.outcome == "not_manual_pending" and _row().status == pending.HELD


@pytest.mark.asyncio
async def test_failed_and_rejected_rows_are_refused(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate, "spd_f")
    publisher.store().transition(_row("spd_f").idempotency_key, pending.APPROVED, pending.FAILED)
    assert (await _complete_ok("spd_f")).outcome == "not_manual_pending"
    await publisher.publish_sns(_publish_request("spd_r"))
    publisher.store().transition(_row("spd_r").idempotency_key, pending.HELD, pending.REJECTED)
    assert (await _complete_ok("spd_r")).outcome == "not_manual_pending"


@pytest.mark.asyncio
async def test_gate_hold_does_not_publish(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate)
    done.status = "held"
    result = await _complete_ok()
    assert result.outcome == "held" and _row().status == pending.APPROVED


# -- 완료 기록: 성공 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_link_closes_as_published_and_records_the_human(
    gate: FakeGate, done: DoneGate
) -> None:
    await _manual_pending(gate)
    when = "2026-11-19T01:00:00+00:00"
    link = "http://instagram.com/p/CxAbc123_-/?igsh=zzz"
    result = await _complete_ok(permalink=link, published_at=when)
    assert result.status == TaskStatus.COMPLETED and result.outcome == "published"
    row = _row()
    assert (row.status, row.failure_code) == (pending.PUBLISHED, None)
    info = row.result
    assert info["manual"] is True and info["permalink"] == LINK
    assert info["posted_by"] == "이재헌" and info["published_at"] == when
    assert info["manual_pending"] is False and info["provider"] == "manual"
    assert info["ai_label_confirmed"] is True
    review = info["ai_label_review"]
    assert review["status"] == "confirmed" and review["confirmed_by"] == "이재헌"
    assert review["confirmed_at"]
    assert done.calls[0][0] == "complete_manual_publish"
    assert result.output["manual"] is True and result.output["permalink"] == LINK
    assert publisher.ai_label_pending() == []  # 표기 확인이 이미 끝났다


@pytest.mark.asyncio
async def test_time_defaults_to_the_recording_time(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate)
    await _complete_ok()
    assert datetime.fromisoformat(_row().result["published_at"]).tzinfo is not None


@pytest.mark.asyncio
async def test_a_published_row_cannot_be_completed_again(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate)
    assert (await _complete_ok()).outcome == "published"
    again = await _complete_ok(permalink="https://www.instagram.com/p/OtherCode1/")
    assert again.status == TaskStatus.FAILED and again.outcome == "already_published"
    assert _row().result["permalink"] == LINK


@pytest.mark.asyncio
async def test_the_same_link_cannot_close_another_row(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate, "spd_a")
    assert (await _complete_ok("spd_a")).outcome == "published"
    await _manual_pending(gate, "spd_b")
    dup = await _complete_ok("spd_b", permalink=LINK + "?igsh=1")
    assert dup.outcome == "permalink_already_used" and _row("spd_b").status == pending.APPROVED


@pytest.mark.asyncio
async def test_link_of_an_auto_published_row_counts_as_used(gate: FakeGate, done: DoneGate) -> None:
    await _manual_pending(gate, "spd_b")
    store = publisher.store()
    store.create(
        pending.PendingPost(
            idempotency_key="k_auto",
            tenant_id="shop-demo",
            context_id="c",
            post_draft_id="spd_auto",
            content_sha="x" * 64,
            execution_id=None,
            review_id=None,
            status=pending.HELD,
            params={},
        )
    )
    store.transition("k_auto", pending.HELD, pending.APPROVED)
    store.transition("k_auto", pending.APPROVED, pending.PUBLISHING)
    store.transition("k_auto", pending.PUBLISHING, pending.PUBLISHED, result={"permalink": LINK})
    assert (await _complete_ok("spd_b")).outcome == "permalink_already_used"


@pytest.mark.asyncio
async def test_manual_posts_count_toward_the_daily_limit(gate: FakeGate, done: DoneGate) -> None:
    since = datetime.now(UTC) - timedelta(hours=1)
    before = publisher.store().count_posts_since(since)
    await _manual_pending(gate)
    assert publisher.store().count_posts_since(since) == before
    await _complete_ok()
    assert publisher.store().count_posts_since(since) == before + 1


@pytest.mark.asyncio
async def test_interrupted_manual_record_is_finished_on_restart(
    gate: FakeGate, done: DoneGate, adapter: MockInstagramAdapter
) -> None:
    row = await _manual_pending(gate)
    record = {"permalink": LINK, "published_at": "2026-11-19T01:00:00+00:00", "posted_by": "재헌"}
    publisher.store().transition(
        row.idempotency_key,
        pending.APPROVED,
        pending.PUBLISHING,
        result={**row.result, **record, "manual": True},
    )  # 두 번째 전이 전에 멈췄다
    await publisher.recover_interrupted()
    closed = _row()
    assert closed.status == pending.PUBLISHED and closed.result["permalink"] == LINK
    assert sum(adapter.calls.values()) == 0


# -- 상태·화면 ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brief_state_shows_manual_pending_then_the_manual_post(
    gate: FakeGate, done: DoneGate
) -> None:
    await _manual_pending(gate)
    base = {"status": "not_requested", "can_request": False}
    waiting = brief_publish.state_from_chain(base, STATE_DATA, CTX)
    assert waiting["status"] == "manual_pending" and waiting["manual_pending"] is True
    assert waiting["post_draft_id"] == "spd_m1" and waiting["permalink"] is None
    await _complete_ok()
    shown = brief_publish.state_from_chain(base, STATE_DATA, CTX)
    assert shown["status"] == "published" and shown["manual"] is True
    assert shown["permalink"] == LINK and shown["posted_by"] == "이재헌"


@pytest.mark.asyncio
async def test_auto_published_state_keeps_its_old_shape(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    await publisher.publish_sns(_publish_request())
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    shown = brief_publish.state_from_chain({"status": "not_requested"}, STATE_DATA, CTX)
    assert shown["status"] == "published"
    assert "manual" not in shown and "manual_pending" not in shown
