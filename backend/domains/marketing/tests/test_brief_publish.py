"""`request_brief_publish`: 준비된 미디어 초안의 게시 요청(C등급 승인 대기 건) 만들기.

외부 호출은 없다: 안전 관문은 가짜(`FakeGate`: 항상 보류, 사람이 결정해야 executed),
Instagram은 목 어댑터. 요청만으로 어댑터가 불리지 않는지(승인 전 게시 0회)를 본다.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import (
    AgentResult,
    EventEnvelope,
    EventType,
    ExecuteDecision,
    HoldReason,
    RequestMeta,
    TaskStatus,
)
from marketing_agent import brief_publish, handlers, sns_media
from marketing_agent.brief_handoff import request_sha
from marketing_agent.main import app
from marketing_agent.sns import AI_LABEL_TEXT_REEL, SnsPostDraft
from marketing_agent.sns_publish import instagram, pending, publisher
from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter

client = TestClient(app)
CTX = "ctx_cmp_brfm_PROP-5"
CAPTION = f"10대 그림책 릴스 {AI_LABEL_TEXT_REEL}"
ASSET_SHA = hashlib.sha256(b"labeled").hexdigest()
ASSET_REF = "t/ctx_brief_PROP-5/med_1/labeled.mp4"


class FakeGate:
    """같은 키는 이전 판정을 돌려준다. 새 키는 항상 보류. 사람이 `resolve`해야 executed."""

    def __init__(self) -> None:
        self.by_key: dict[str, ExecuteDecision] = {}
        self.calls: list[str] = []
        self.params: list[dict] = []

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        assert action == "publish_sns"
        key = meta.idempotency_key or ""
        self.calls.append(key)
        self.params.append(params)
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

    def resolve(self, status: str) -> str:
        key, decision = next(iter(self.by_key.items()))
        self.by_key[key] = decision.model_copy(update={"status": status})
        return decision.review_id or ""


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeGate:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in ("MARKETING_SNS_PROVIDER", "MARKETING_SNS_PENDING_DB", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(tmp_path))
    events.reset_mock_bus()
    handlers.reset_state()
    publisher.reset_runtime()
    instagram.reset_adapter()
    fake = MockInstagramAdapter()
    instagram.set_adapter(fake)
    gate = FakeGate()
    gate.adapter = fake  # type: ignore[attr-defined]
    monkeypatch.setattr(publisher, "execute", gate.execute)

    async def no_sleep(_: float) -> None:
        return None

    publisher.RT.sleep = no_sleep

    async def passed(request, store):  # noqa: ANN001
        # 실제 점검은 미디어 파일(ffprobe)이 있어야 통과한다. 점검 규칙 자체는 test_sns*가 본다.
        # 여기서는 "요청 직전에 점검을 다시 돌려 통과한 값만 보낸다"는 연결만 본다
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            output={"verdict": "approved", "publishable": True, "problems": []},
        )

    monkeypatch.setattr(brief_publish.sns, "check_sns_post", passed)
    yield gate
    publisher.reset_runtime()
    instagram.reset_adapter()
    events.reset_mock_bus()


def _call(skill_id: str, input: dict, token: str = "mock:dashboard-api") -> dict:
    body = {
        "meta": {
            "context_id": "ctx_cmp_any",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill_id,
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def _prepared_brief(
    *, stage: str = "draft_ready", kind: str = "reel", publishable: bool = True
) -> SnsPostDraft:
    """기획안을 받고 넘기고, 미디어 초안이 준비된 상태(보관본)를 만든다."""
    brief = {
        "schema_version": "1",
        "proposal_id": "PROP-5",
        "title": "10대 그림책 릴스",
        "idea": "10대에게 인스타그램 릴스로 그림책 신간을 알린다",
        "why": "12월 그림책 검색이 늘었다",
        "facts": [],
        "target": None,
        "products": [],
        "format": "숏폼 15초",
        "approved_by": "예진",
        "note": None,
        "body_md": None,
    }
    assert _call("receive_campaign_brief", brief, token="mock:planning")["output"]["outcome"]
    out = _call(
        "handoff_brief_to_sns",
        {"proposal_id": "PROP-5", "media_kind": "video", "fields": {"category": "그림책"}},
    )
    assert out["outcome"] == "handed", out
    draft = SnsPostDraft(
        post_draft_id="spd_fake0001",
        source_kind="brief",
        source_ref="PROP-5",
        platform="instagram",
        media_kind=kind,  # type: ignore[arg-type]
        caption=CAPTION,
        hashtags=[],
        caption_sha256=hashlib.sha256(CAPTION.encode()).hexdigest(),
        required_text=[],
        context_id="ctx_brief_PROP-5",
        asset_ref=ASSET_REF,
        asset_sha256=ASSET_SHA,
    )
    handlers.STATE.sns.save(draft)
    record = handlers.STATE.briefs.handoff("PROP-5")
    prepared = {
        "request_sha": request_sha(record.media_request),
        "post_draft_id": draft.post_draft_id,
        "stage": stage,
        "media_kind": kind,
        "asset_ref": ASSET_REF,
        "asset_sha256": ASSET_SHA,
        "check": {
            "verdict": "approved" if publishable else "rejected",
            "publishable": publishable,
            "problems": [],
        },
    }
    handlers.STATE.briefs.save_handoff(record.model_copy(update={"prepared": prepared}))
    return draft


def _published(env: FakeGate) -> int:
    """게시 시도 횟수: 컨테이너 생성·게시 호출(읽기 전용 한도 조회는 세지 않는다)."""
    calls = env.adapter.calls  # type: ignore[attr-defined]
    return calls.get("create_container", 0) + calls.get("media_publish", 0)


def _request() -> dict:
    return _call("request_brief_publish", {"proposal_id": "PROP-5"})


def _view() -> dict:
    out = _call("get_campaign_brief", {"proposal_id": "PROP-5"})
    return out["output"]["item"]["sns_handoff"]["publish"]


def _envelope(review_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_id="evt_1",
        type=EventType.SAFETY_REVIEW_DECIDED,
        occurred_at=datetime.now(UTC),
        tenant_id="shop-demo",
        context_id=CTX,
        source="safety",
        idempotency_key=f"safety.review_decided:{review_id}:evt_1",
        data={
            "review_id": review_id,
            "execution_id": "exe_x",
            "action": "publish_sns",
            "requester": "marketing",
            "decision": "approved",
            "decided_by": "jaehun",
        },
    )


def _row() -> pending.PendingPost:
    rows = publisher.store()._query("")  # noqa: SLF001
    assert len(rows) == 1
    return rows[0]


# --- 요청: 승인 대기 건만 만든다 -----------------------------------------------------------


def test_request_creates_a_held_item_and_never_publishes(env: FakeGate) -> None:
    _prepared_brief()
    assert _view() == {
        "status": "not_requested",
        "can_request": True,
        "reason_code": None,
        "review_id": None,
        "failure_code": None,
        "permalink": None,
        "caption": CAPTION,
    }
    out = _request()
    assert out["status"] == "completed" and out["outcome"] == "requested", out
    assert out["output"]["publish"]["status"] == "held"
    assert out["output"]["publish"]["review_id"] == out["output"]["review_id"] == "rev_1"
    assert out["output"]["grade"] == "C"
    row = _row()
    assert row.status == pending.HELD and row.review_id == "rev_1"
    assert _published(env) == 0  # 승인 전 게시 0회
    # 보관본 그대로: 캡션·파일·해시·AI 표시·종류
    assert row.params["caption"] == CAPTION and row.params["asset_ref"] == ASSET_REF
    assert row.params["asset_sha256"] == ASSET_SHA and row.params["media_kind"] == "reel"
    assert row.params["is_ai_generated"] is True and "PROP-5" in row.params["summary"]
    assert row.context_id == "ctx_brief_PROP-5"  # 호출자와 무관하게 같은 키
    sent = env.params[0]
    assert sent["ai_label_missing_count"] == 0 and sent["is_ai_generated"] is True
    assert _view()["status"] == "held" and _view()["can_request"] is False


def test_second_request_returns_state_without_a_second_hold(env: FakeGate) -> None:
    _prepared_brief()
    _request()
    again = _request()
    assert again["outcome"] == "already_requested"
    assert again["output"]["publish"]["status"] == "held"
    assert len(env.calls) == 1 and len(env.by_key) == 1


def test_caller_cannot_choose_content() -> None:
    _prepared_brief()
    for extra in ({"caption": "바꾼 글"}, {"asset_ref": "x"}, {"post_draft_id": "spd_other"}):
        out = _call("request_brief_publish", {"proposal_id": "PROP-5"} | extra)
        assert out["outcome"] == "invalid_input"
    assert publisher.store()._query("") == []  # noqa: SLF001


def test_only_a_human_approval_publishes(env: FakeGate) -> None:
    publisher.setup_subscriber()
    _prepared_brief()
    review_id = _request()["output"]["review_id"]
    adapter = env.adapter  # type: ignore[attr-defined]
    again = _request()  # 승인 전에 다시 눌러도 게시되지 않는다
    assert again["outcome"] == "already_requested" and _published(env) == 0

    env.resolve("executed")  # 사람이 승인 화면에서 진행을 누른 것과 같은 효과
    import asyncio

    asyncio.run(events.publish(_envelope(review_id)))
    assert _row().status == pending.PUBLISHED
    assert adapter.calls["media_publish"] == 1
    assert _view()["status"] == "published" and _view()["can_request"] is False
    assert adapter.create_requests[0].is_ai_generated is True


def test_rejected_or_expired_stays_closed_and_is_not_requested_again(env: FakeGate) -> None:
    _prepared_brief()
    _request()
    env.resolve("rejected")
    row = _row()
    import asyncio

    asyncio.run(publisher.apply_decision(row, "rejected", "poll"))
    state = _view()
    assert state["status"] == "rejected" and state["can_request"] is False
    again = _request()
    assert (
        again["outcome"] == "already_requested"
        and again["output"]["publish"]["status"] == "rejected"
    )
    assert len(env.calls) == 1  # 새 승인 건을 만들지 않는다(자동 재요청·연장 없음)
    assert _published(env) == 0


# --- 막는 것 -------------------------------------------------------------------------------


def test_refusals_never_reach_the_gate(env: FakeGate) -> None:
    assert _request()["outcome"] == "brief_not_found"
    assert _call("request_brief_publish", {"proposal_id": "a b"})["outcome"] == "invalid_input"
    _prepared_brief(stage="check_rejected", publishable=False)
    assert _request()["outcome"] == "not_prepared"
    assert _view()["can_request"] is False and _view()["reason_code"] == "not_prepared"
    assert env.calls == []


def test_not_publishable_check_is_refused(env: FakeGate) -> None:
    _prepared_brief(publishable=False)
    assert _request()["outcome"] == "check_not_passed"
    assert env.calls == []


def test_carousel_is_out_of_scope(env: FakeGate) -> None:
    _prepared_brief(kind="carousel")
    out = _request()
    assert out["outcome"] == "media_kind_unsupported" and env.calls == []
    assert _view()["can_request"] is False


def test_changed_or_missing_draft_is_refused(env: FakeGate) -> None:
    draft = _prepared_brief()
    handlers.STATE.sns.save(draft.model_copy(update={"asset_sha256": "0" * 64}))
    assert _request()["outcome"] == "draft_changed"
    handlers.STATE.sns.save(draft.model_copy(update={"caption": "다른 글"}))
    assert _request()["outcome"] == "draft_changed"
    assert env.calls == []


def test_fresh_safety_check_runs_and_is_not_bypassed(
    env: FakeGate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared_brief()

    async def rejecting(request, store):  # noqa: ANN001
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            output={"verdict": "rejected", "publishable": False, "problems": ["forbidden_phrase"]},
        )

    monkeypatch.setattr(brief_publish.sns, "check_sns_post", rejecting)
    out = _request()
    assert out["outcome"] == "check_not_passed" and out["output"]["problems"] == [
        "forbidden_phrase"
    ]
    assert env.calls == []


def test_daily_limit_and_halt_are_passed_through(
    env: FakeGate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared_brief()

    async def refused(adapter):  # noqa: ANN001
        return "daily_limit_reached", {}

    monkeypatch.setattr(publisher, "_pre_gate", refused)
    out = _request()
    assert out["status"] == "failed" and out["outcome"] == "daily_limit_reached"
    assert env.calls == [] and publisher.store()._query("") == []  # noqa: SLF001
    assert _view()["status"] == "not_requested" and _view()["can_request"] is True


def test_no_adapter_is_refused_before_approval(
    env: FakeGate, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing():  # noqa: ANN202
        raise instagram.AdapterUnavailable("none")

    monkeypatch.setattr(publisher, "get_adapter", missing)
    _prepared_brief()
    assert _request()["outcome"] == "adapter_unavailable"
    assert env.calls == []


def test_only_dashboard_api_may_call() -> None:
    _prepared_brief()
    for token in ("mock:planning", "mock:orchestrator"):
        assert (
            _call("request_brief_publish", {"proposal_id": "PROP-5"}, token=token)["status"]
            == "rejected"
        )
    assert publisher.store()._query("") == []  # noqa: SLF001
