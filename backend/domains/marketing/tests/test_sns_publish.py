"""`publish_sns`: 승인 대기 → 승인 이벤트/폴링 → 목 게시. 실제 Instagram·Redis는 쓰지 않는다."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template import events
from ax_contracts.schemas import (
    AgentRequest,
    EventEnvelope,
    EventType,
    ExecuteDecision,
    ExecuteRequest,
    HoldReason,
    RequestMeta,
    TaskStatus,
)
from ax_safety.gate import Gate
from ax_safety.rules import load_registry, load_thresholds
from ax_safety.store import Store
from ax_safety.verify import publish_safety_event
from marketing_agent import handlers
from marketing_agent.main import load_card
from marketing_agent.sns_publish import instagram, pending, publisher
from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter

CTX = "ctx_cmp_0001"
TENANT = "shop-demo"
ASSET_SHA = hashlib.sha256(b"asset").hexdigest()


class FakeGate:
    """safety 관문 대역: 같은 키는 이전 판정을 돌려주고(멱등 재생), 승인하면 executed가 된다."""

    def __init__(self) -> None:
        self.by_key: dict[str, ExecuteDecision] = {}
        self.calls: list[str] = []
        self.n = 0

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        assert action == "publish_sns"
        key = meta.idempotency_key or ""
        self.calls.append(key)
        if key not in self.by_key:
            self.n += 1
            self.by_key[key] = ExecuteDecision(
                execution_id=f"exe_{self.n}",
                grade="C",
                status="held",
                hold_reason=HoldReason.OVER_THRESHOLD,
                review_id=f"rev_{self.n}",
            )
        return self.by_key[key]

    def resolve(self, review_id: str, status: str) -> None:
        for key, d in self.by_key.items():
            if d.review_id == review_id:
                self.by_key[key] = d.model_copy(update={"status": status})


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in (
        "MARKETING_SNS_PROVIDER",
        "MARKETING_SNS_PENDING_DB",
        "REDIS_URL",
        "MARKETING_SNS_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)
    events.reset_mock_bus()
    handlers.reset_state()
    publisher.reset_runtime()
    instagram.reset_adapter()

    async def no_sleep(_: float) -> None:
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


def _request(**over: Any) -> AgentRequest:
    body = {
        "post_draft_id": "spd_abc",
        "asset_ref": "assets/spd_abc.jpg",
        "asset_sha256": ASSET_SHA,
        "caption": "캡션 이 글과 이미지는 AI로 만들었습니다.",
        "media_kind": "image",
        "share_to_feed": True,
    } | over
    return AgentRequest(
        meta=RequestMeta(
            context_id=CTX,
            token="mock:orchestrator",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        skill="publish_sns",
        input=body,
    )


def _envelope(
    review_id: str,
    decision: str = "approved",
    *,
    event_id: str = "evt_1",
    requester: str = "marketing",
    action: str = "publish_sns",
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=EventType.SAFETY_REVIEW_DECIDED,
        occurred_at=datetime.now(UTC),
        tenant_id=TENANT,
        context_id=CTX,
        source="safety",
        idempotency_key=f"safety.review_decided:{review_id}:{event_id}",
        data={
            "review_id": review_id,
            "execution_id": "exe_x",
            "action": action,
            "requester": requester,
            "decision": decision,
            "decided_by": "jaehun",
        },
    )


def _row() -> pending.PendingPost:
    rows = publisher.store()._query("")  # noqa: SLF001
    assert len(rows) == 1
    return rows[0]


async def _held(gate: FakeGate, **over: Any):
    result = await publisher.publish_sns(_request(**over))
    assert result.status == TaskStatus.INPUT_REQUIRED, result
    return result


# -- 기본 흐름
@pytest.mark.asyncio
async def test_held_then_approval_event_publishes_once(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    assert publisher.setup_subscriber()
    held = await _held(gate)
    assert held.outcome == "held"
    review_id = held.output["review_id"]
    assert sum(adapter.calls.values()) == 0  # executed 전에는 어댑터 호출 0회
    assert _row().status == pending.HELD

    gate.resolve(review_id, "executed")  # 사람이 승인한 것과 같은 효과
    await events.publish(_envelope(review_id))

    row = _row()
    assert row.status == pending.PUBLISHED
    assert adapter.calls["media_publish"] == 1
    assert row.result["permalink"].startswith("https://")
    assert row.result["ai_label_confirmed"] is True
    sent = adapter.create_requests[0]
    assert sent.is_ai_generated is True
    assert sent.share_to_feed is True  # 항상 명시

    again = await publisher.publish_sns(_request())  # 같은 요청 다시: 결과만 돌려준다
    assert again.status == TaskStatus.COMPLETED and again.outcome == "published"
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_duplicate_events_publish_once(gate: FakeGate, adapter: MockInstagramAdapter) -> None:
    publisher.setup_subscriber()
    review_id = (await _held(gate)).output["review_id"]
    gate.resolve(review_id, "executed")
    await events.publish(_envelope(review_id, event_id="evt_1"))
    await events.publish(_envelope(review_id, event_id="evt_2"))  # 재전달
    assert adapter.calls["media_publish"] == 1
    assert adapter.calls["create_container"] == 1


@pytest.mark.asyncio
async def test_concurrent_approvals_publish_once(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    import asyncio

    review_id = (await _held(gate)).output["review_id"]
    gate.resolve(review_id, "executed")
    row = _row()
    await asyncio.gather(
        publisher.apply_decision(row, "approved", "event"),
        publisher.apply_decision(row, "approved", "poll"),
    )
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_content_change_needs_new_approval(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    publisher.setup_subscriber()
    review_id = (await _held(gate)).output["review_id"]
    gate.resolve(review_id, "executed")
    publisher.RT.resolver = lambda row: row.params | {"asset_sha256": "0" * 64}
    calls_before = len(gate.calls)
    await events.publish(_envelope(review_id))
    row = _row()
    assert (row.status, row.failure_code) == (pending.FAILED, "content_changed")
    assert sum(adapter.calls.values()) == 0
    assert len(gate.calls) == calls_before  # 옛 승인을 다시 쓰려고 관문을 부르지도 않는다


@pytest.mark.asyncio
async def test_changed_content_gets_a_different_key_and_new_hold(gate: FakeGate) -> None:
    first = await _held(gate)
    second = await _held(gate, caption="다른 캡션 이 글과 이미지는 AI로 만들었습니다.")
    assert first.output["review_id"] != second.output["review_id"]
    assert len(set(gate.calls)) == 2


@pytest.mark.asyncio
async def test_rejected_decision_ends_without_publishing(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    publisher.setup_subscriber()
    review_id = (await _held(gate)).output["review_id"]
    gate.resolve(review_id, "rejected")
    await events.publish(_envelope(review_id, "rejected"))
    assert _row().status == pending.REJECTED
    assert sum(adapter.calls.values()) == 0
    again = await publisher.publish_sns(_request())
    assert again.outcome == "rejected_by_review"


@pytest.mark.asyncio
async def test_events_for_other_actions_requesters_or_reviews_are_ignored(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    publisher.setup_subscriber()
    review_id = (await _held(gate)).output["review_id"]
    gate.resolve(review_id, "executed")
    await events.publish(_envelope(review_id, requester="support", event_id="e1"))
    await events.publish(_envelope(review_id, action="issue_coupon", event_id="e2"))
    await events.publish(_envelope("rev_unknown", event_id="e3"))
    assert _row().status == pending.HELD
    assert sum(adapter.calls.values()) == 0


@pytest.mark.asyncio
async def test_invalid_input_and_ai_label_cannot_be_turned_off(gate: FakeGate) -> None:
    result = await publisher.publish_sns(_request(is_ai_generated=False))
    assert result.outcome == "invalid_input"
    assert not gate.calls
    assert (await publisher.publish_sns(_request(asset_sha256="xyz"))).outcome == "invalid_input"


def test_idempotency_key_includes_content_hash() -> None:
    base = ("c", ASSET_SHA, "image", True, True)
    sha = publisher.content_sha(*base)
    assert publisher.idempotency_key("ctx_1", "spd_1", sha) == f"ctx_1:publish_sns:spd_1:{sha[:12]}"
    variants = [
        ("c2", ASSET_SHA, "image", True, True),
        ("c", "1" * 64, "image", True, True),
        ("c", ASSET_SHA, "reel", True, True),
        ("c", ASSET_SHA, "image", False, True),
        ("c", ASSET_SHA, "image", True, False),
    ]
    assert len({publisher.content_sha(*v) for v in [base, *variants]}) == 6
    # 필드 경계가 달라지면 문자열을 이어 붙여도 같아지지 않는다
    assert publisher.content_sha("ab", "c" * 63, "image", True, True) != publisher.content_sha(
        "a", "b" + "c" * 63, "image", True, True
    )


# -- 실패 분류
async def _approve(gate: FakeGate) -> pending.PendingPost:
    held = await _held(gate)
    gate.resolve(held.output["review_id"], "executed")
    await publisher.apply_decision(_row(), "approved", "event")
    return _row()


@pytest.mark.parametrize(
    ("code", "subcode", "failure"),
    [
        (9, 2207042, "ig_daily_limit"),
        (4, 2207051, "ig_spam_suspected"),
        (25, 2207050, "ig_account_restricted"),
        (9004, 2207052, "ig_media_url_unreachable"),
        (352, 2207026, "ig_video_format"),
        (190, None, "ig_token_invalid"),
        (999, 1, "ig_unknown_error"),
    ],
)
@pytest.mark.asyncio
async def test_fatal_errors_fail_immediately(
    gate: FakeGate, adapter: MockInstagramAdapter, code: int, subcode: int | None, failure: str
) -> None:
    adapter.fail_on("create_container", code, subcode, times=5)
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, failure)
    assert adapter.calls["create_container"] == 1  # 재시도 없음
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_transient_error_backs_off_then_succeeds(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    sleeps: list[float] = []

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    publisher.RT.sleep = record
    adapter.fail_on("create_container", 24, 2207008, times=2)
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert adapter.calls["create_container"] == 3
    assert sleeps == [2.0, 4.0]  # 백오프는 2배씩
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_transient_error_stops_at_retry_limit(
    gate: FakeGate, adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_SNS_MAX_RETRIES", "2")
    publisher.RT.config = None
    adapter.fail_on("create_container", 24, 2207008, times=99)
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "ig_container_missing")
    assert adapter.calls["create_container"] == 3  # 처음 1 + 재시도 2


@pytest.mark.asyncio
async def test_container_error_status_fails_without_publish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.container_error(9004, 2207052)
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "ig_media_url_unreachable")
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_container_expired_fails(gate: FakeGate, adapter: MockInstagramAdapter) -> None:
    adapter.expire_container()
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "ig_container_expired")


@pytest.mark.asyncio
async def test_in_progress_is_polled_until_finished(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.in_progress_polls = 3
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert adapter.calls["get_container_status"] == 4


@pytest.mark.asyncio
async def test_new_container_class_retries_once_only(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("create_container", 24, 2207006, times=1)  # 새 컨테이너 1회
    assert (await _approve(gate)).status == pending.PUBLISHED
    assert adapter.calls["create_container"] == 2


@pytest.mark.asyncio
async def test_new_container_class_fails_when_it_repeats(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("create_container", 24, 2207006, times=5)
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "ig_media_not_found")
    assert adapter.calls["create_container"] == 2


@pytest.mark.asyncio
async def test_lost_response_is_resolved_by_container_status_not_republish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.lose_response_on("media_publish")  # 게시는 처리됐지만 응답을 잃음
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert adapter.calls["media_publish"] == 1  # 다시 게시하지 않았다
    assert row.media_id == "mock_media_2"
    assert row.result["permalink"]


@pytest.mark.asyncio
async def test_lost_response_without_confirmation_does_not_republish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.lose_response_on("media_publish", processed=False)
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "publish_unconfirmed")
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_media_publish_error_response_checks_status_before_retry(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("media_publish", -1, 2207001, times=1)
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert adapter.calls["media_publish"] == 2  # 에러 응답(미게시) 뒤 한 번 더. 성공은 1회


@pytest.mark.asyncio
async def test_media_publish_fatal_error_fails(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("media_publish", 9, 2207042)
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "ig_daily_limit")
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_ai_flag_missing_in_response_is_recorded_not_assumed(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.report_ai_flag = None
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert row.result["ai_label_confirmed"] is False


# -- 폴링 대비책
def _safety_client(status: str | int) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if isinstance(status, int):
            return httpx.Response(status, json={"error": {"code": "NOT_FOUND"}})
        assert request.url.path.startswith("/api/safety/reviews/rev_")
        return httpx.Response(200, json={"data": {"status": status}, "meta": {}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.asyncio
async def test_polling_continues_when_no_event_arrives(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    publisher.setup_subscriber()  # 구독 여부와 상관없이 폴링은 동작한다
    review_id = (await _held(gate)).output["review_id"]
    async with _safety_client("pending") as client:
        assert await publisher.poll_reviews_once(client) == 0
    assert _row().status == pending.HELD
    gate.resolve(review_id, "executed")
    async with _safety_client("approved") as client:
        assert await publisher.poll_reviews_once(client) == 1
    assert _row().status == pending.PUBLISHED
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_polling_rejected_and_unreachable(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    await _held(gate)
    async with _safety_client(404) as client:
        assert await publisher.poll_reviews_once(client) == 0
    async with _safety_client("expired") as client:
        assert await publisher.poll_reviews_once(client) == 1
    assert _row().status == pending.REJECTED
    assert sum(adapter.calls.values()) == 0


def test_subscriber_is_reported_off_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    events.reset_mock_bus()
    assert publisher.setup_subscriber() is False
    assert publisher.RT.subscriber == "disabled_no_redis"
    assert publisher.runtime_status()["subscriber"] == "disabled_no_redis"


# -- 실제 모드 거부, 재시작 복구
@pytest.mark.asyncio
async def test_real_provider_is_rejected_before_any_call(
    gate: FakeGate, adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_SNS_PROVIDER", "instagram")
    result = await publisher.publish_sns(_request())
    assert result.status == TaskStatus.FAILED and result.outcome == "adapter_unavailable"
    assert not gate.calls
    assert sum(adapter.calls.values()) == 0


@pytest.mark.asyncio
async def test_pending_posts_survive_restart(
    gate: FakeGate,
    adapter: MockInstagramAdapter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "pending.db"
    monkeypatch.setenv("MARKETING_SNS_PENDING_DB", str(db))
    review_id = (await _held(gate)).output["review_id"]
    publisher.reset_runtime()  # 서버 재시작: 메모리 상태는 사라지고 파일만 남는다

    async def no_sleep(_: float) -> None:
        return None

    publisher.RT.sleep = no_sleep
    row = publisher.store().by_review(review_id)
    assert row is not None and row.status == pending.HELD
    gate.resolve(review_id, "executed")
    publisher.setup_subscriber()
    await events.publish(_envelope(review_id))
    assert publisher.store().get(row.idempotency_key).status == pending.PUBLISHED  # type: ignore[union-attr]
    assert adapter.calls["media_publish"] == 1


def _interrupted(sent: bool, container: str | None) -> pending.PendingPost:
    row = pending.PendingPost(
        idempotency_key="k1",
        tenant_id=TENANT,
        context_id=CTX,
        post_draft_id="spd_abc",
        content_sha="s",
        execution_id="exe_1",
        review_id="rev_1",
        status=pending.PUBLISHING,
        params={"post_draft_id": "spd_abc"},
        container_id=container,
        publish_sent=sent,
    )
    assert publisher.store().create(row)
    publisher.store().update("k1", container_id=container, publish_sent=sent)
    return row


@pytest.mark.asyncio
async def test_recover_never_republishes(adapter: MockInstagramAdapter) -> None:
    # 보내지 않은 채 멈춤 → 실패로 닫는다
    _interrupted(False, None)
    assert await publisher.recover_interrupted() == 1
    assert publisher.store().get("k1").failure_code == "interrupted_before_publish"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_recover_sent_publish_is_decided_by_container_status(
    adapter: MockInstagramAdapter,
) -> None:
    from marketing_agent.sns_publish.instagram import ContainerRequest

    container = await adapter.create_container(ContainerRequest("a", "image", "c", True, True))
    media_id = await adapter.media_publish(container)
    _interrupted(True, container)
    await publisher.recover_interrupted()
    row = publisher.store().get("k1")
    assert row is not None and row.status == pending.PUBLISHED and row.media_id == media_id
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_recover_sent_but_unpublished_container_fails(adapter: MockInstagramAdapter) -> None:
    from marketing_agent.sns_publish.instagram import ContainerRequest

    container = await adapter.create_container(ContainerRequest("a", "image", "c", True, True))
    _interrupted(True, container)
    await publisher.recover_interrupted()
    assert publisher.store().get("k1").failure_code == "publish_unconfirmed"  # type: ignore[union-attr]
    assert adapter.calls["media_publish"] == 0


# -- 상태 기계
def test_state_machine_blocks_reentry_and_bad_transitions() -> None:
    store = publisher.store()
    row = pending.PendingPost("k", TENANT, CTX, "d", "s", "e", "r", pending.APPROVED, {})
    assert store.create(row) and not store.create(row)
    assert store.transition("k", pending.APPROVED, pending.PUBLISHING) is True
    assert store.transition("k", pending.APPROVED, pending.PUBLISHING) is False  # 재진입 금지
    with pytest.raises(ValueError):
        store.transition("k", pending.PUBLISHING, pending.HELD)
    with pytest.raises(ValueError):
        store.transition("k", pending.PUBLISHED, pending.APPROVED)
    with pytest.raises(ValueError):
        store.update("k", status="held")


# -- 카드, 비밀값
def test_card_lists_publish_sns_as_c_grade() -> None:
    card = {s.id: s for s in load_card().skills}
    assert card["publish_sns"].grade == "C"
    assert card["publish_sns"].allowed_requesters == ["orchestrator"]


def test_secret_env_names_are_names_only() -> None:
    assert "MARKETING_IG_ACCESS_TOKEN" in instagram.SECRET_ENV_NAMES
    source = "".join(
        p.read_text(encoding="utf-8")
        for p in Path(publisher.__file__).parent.glob("*.py")
        if p.name != "instagram.py"
    )
    for name in instagram.SECRET_ENV_NAMES:
        assert name not in source  # 값을 읽는 코드가 없다


# -- 실제 safety Gate와 통합
@pytest.mark.asyncio
async def test_integration_with_real_gate_approval_triggers_publish(
    adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Gate(load_registry(), load_thresholds(), Store(), None, publish_safety_event)  # type: ignore[arg-type]

    async def through_gate(action: str, params: dict, meta: RequestMeta, evidence: Any):
        return await real.execute(
            ExecuteRequest(
                action=action,
                requester="marketing",
                context_id=meta.context_id,
                tenant_id=meta.tenant_id,
                idempotency_key=meta.idempotency_key or "",
                params=params,
                evidence=evidence,
            )
        )

    monkeypatch.setattr(publisher, "execute", through_gate)
    publisher.setup_subscriber()
    held = await _held(None)  # type: ignore[arg-type]
    assert held.output["grade"] == "C"
    assert sum(adapter.calls.values()) == 0

    await real.decide_review(held.output["review_id"], "approved", "taeyoon", None)  # 사람 승인

    row = _row()
    assert row.status == pending.PUBLISHED  # 승인 이벤트가 곧바로 게시로 이어졌다
    assert adapter.calls["media_publish"] == 1
    # 사람 승인 뒤 같은 요청을 다시 보내도 새 보류도 새 게시도 없다
    again = await publisher.publish_sns(_request())
    assert again.outcome == "published"
    assert adapter.calls["media_publish"] == 1
