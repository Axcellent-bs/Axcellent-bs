"""받은 기획안 자동 넘김: 초안까지만, 멱등, 실패 재시도 한도, 생성·게시는 부르지 않는다."""

from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import AgentRequest
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent import brief_auto_handoff, handlers
from marketing_agent.brief_auto_handoff import AutoHandoffConfig, auto_handoff_once

MEDIA_ENV = (
    "MARKETING_IMAGE_PROVIDER",
    "MARKETING_IMAGE_MODEL",
    "MARKETING_VIDEO_PROVIDER",
    "MARKETING_VIDEO_MODEL",
)
NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)
ON = AutoHandoffConfig(enabled=True, poll_s=60, retry_s=300, max_attempts=3)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in MEDIA_ENV:
        monkeypatch.delenv(name, raising=False)
    handlers.reset_state()

    async def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("자동 넘김이 생성·게시를 불렀다")

    monkeypatch.setattr(handlers, "generate_media_flow", forbidden)
    monkeypatch.setattr(handlers, "publish_sns", forbidden)
    monkeypatch.setattr(handlers, "execute", forbidden)  # 초안 보관만, 상태 변경 관문도 안 쓴다


def _add(pid: str) -> None:
    brief = CampaignBriefInput(
        schema_version="1",
        proposal_id=pid,
        title="10대 그림책 릴스",
        idea="10대에게 릴스로 그림책 신간을 알린다",
        why="12월 그림책 검색이 늘었다",
        facts=[],
        target=None,
        products=[],
        approved_by="tester",
    )
    handlers.STATE.briefs.add(brief, NOW)


def _status(pid: str) -> str | None:
    record = handlers.STATE.briefs.handoff(pid)
    return record.status if record else None


@pytest.mark.asyncio
async def test_off_by_default_does_nothing() -> None:
    _add("P1")
    off = AutoHandoffConfig(enabled=False, poll_s=60, retry_s=300, max_attempts=3)
    counts = await auto_handoff_once(handlers.STATE.briefs, handlers.handoff_brief_to_sns, off, NOW)
    assert counts == {"handed": 0, "failed": 0, "skipped": 0}
    assert _status("P1") is None


def test_env_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(brief_auto_handoff.ENABLE_ENV, raising=False)
    assert AutoHandoffConfig.from_env().enabled is False
    monkeypatch.setenv(brief_auto_handoff.ENABLE_ENV, "on")
    assert AutoHandoffConfig.from_env().enabled is True


@pytest.mark.asyncio
async def test_hands_off_pending_briefs_to_draft_only_and_is_idempotent() -> None:
    _add("P1")
    _add("P2")
    run = handlers.handoff_brief_to_sns
    counts = await auto_handoff_once(handlers.STATE.briefs, run, ON, NOW)
    assert counts["handed"] == 2
    for pid in ("P1", "P2"):
        record = handlers.STATE.briefs.handoff(pid)
        assert record is not None and record.status == "handed"
        assert record.media_request["auto_generate"] is False
        assert record.prepared is None  # 생성은 사람이 시작한다
        assert handlers.STATE.sns.get(record.draft_id) is not None
    assert handlers.STATE.briefs.pending_handoff_ids(10) == []
    again = await auto_handoff_once(handlers.STATE.briefs, run, ON, NOW)
    assert again == {"handed": 0, "failed": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_failed_is_retried_after_delay_and_gives_up_at_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add("P1")
    calls: list[str] = []

    async def failing(request: AgentRequest):  # noqa: ANN202
        calls.append(request.input["proposal_id"])
        return await handlers.handoff_brief_to_sns(request)

    for kind in ("IMAGE", "VIDEO"):  # 모델 없음 -> 가격표 fail-closed
        monkeypatch.setenv(f"MARKETING_{kind}_PROVIDER", "gemini")
    store = handlers.STATE.briefs
    assert (await auto_handoff_once(store, failing, ON, NOW))["failed"] == 1
    assert _status("P1") == "failed"
    # 재시도 간격 전에는 건너뛴다
    soon = NOW + timedelta(seconds=10)
    assert (await auto_handoff_once(store, failing, ON, soon))["skipped"] == 1
    t2 = NOW + timedelta(seconds=301)
    assert (await auto_handoff_once(store, failing, ON, t2))["failed"] == 1
    t3 = t2 + timedelta(seconds=301)
    assert (await auto_handoff_once(store, failing, ON, t3))["failed"] == 1
    assert len(calls) == 3
    # 한도(3번)에 닿으면 더 하지 않는다
    t4 = t3 + timedelta(hours=1)
    assert (await auto_handoff_once(store, failing, ON, t4))["skipped"] == 1
    assert len(calls) == 3
    assert store.auto_attempts("P1")[0] == 3
    # 원인을 고치면(단가표 없는 공급자 해제) 사람이 버튼으로 넘길 수 있고 자동 대상에서 빠진다
    for kind in ("IMAGE", "VIDEO"):
        monkeypatch.delenv(f"MARKETING_{kind}_PROVIDER")
    out = await handlers.handoff_brief_to_sns(
        AgentRequest(
            meta=brief_auto_handoff._request("P1", t4).meta,  # noqa: SLF001
            skill="handoff_brief_to_sns",
            input={"proposal_id": "P1"},
        )
    )
    assert out.output["outcome"] == "handed"
    assert store.pending_handoff_ids(10) == []


@pytest.mark.asyncio
async def test_exception_in_one_brief_does_not_stop_others() -> None:
    _add("P1")
    _add("P2")

    async def flaky(request: AgentRequest):  # noqa: ANN202
        if request.input["proposal_id"] == "P1":
            raise RuntimeError("boom")
        return await handlers.handoff_brief_to_sns(request)

    counts = await auto_handoff_once(handlers.STATE.briefs, flaky, ON, NOW)
    assert counts == {"handed": 1, "failed": 1, "skipped": 0}
    assert handlers.STATE.briefs.auto_attempts("P1")[0] == 1
