"""의뢰 영상 게시 → 데이터 성과 측정 등록 (11/23). 실제 Instagram·데이터는 부르지 않는다 (가짜).

- 자동 게시(media_publish)·수동 게시(permalink 기록)가 닫히면 등록 호출 한 번
- 넘기는 값: campaign_id = 의뢰 번호(wr_), video_url = permalink, proposal_id = source_ref,
  test=false
- 등록이 실패해도 게시 결과는 그대로 (경고만)
- 의뢰 영상이 아니면(다른 context·brief 아닌 초안) 부르지 않는다. 캠페인 경로는 그대로
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.data import VideoPerformanceOutput
from marketing_agent import handlers, request_video_publish, sns
from marketing_agent.sns_publish import pending, publisher

_SPEC = importlib.util.spec_from_file_location(
    "manual_publish_fakes_rv", Path(__file__).with_name("test_manual_publish.py"))
assert _SPEC is not None and _SPEC.loader is not None
fakes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fakes)

_env = fakes._env  # noqa: SLF001 - 같은 환경 (임시 대기 저장소·목 어댑터)
adapter, gate, done = fakes.adapter, fakes.gate, fakes.done
WR = "wr_abc0000001"


class FakeData:
    def __init__(self, fail: bool = False, status: TaskStatus = TaskStatus.COMPLETED) -> None:
        self.got: list[AgentRequest] = []
        self.fail, self.status = fail, status

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        assert target == "data" and request.skill == "video_performance"
        self.got.append(request)
        if self.fail:
            raise RuntimeError("데이터에 닿지 못함")
        out = VideoPerformanceOutput(platform="instagram", video_id="mock1", tracking=True,
                                     campaign_id=request.input["campaign_id"])  # fmt: skip
        return AgentResult(meta=request.meta, status=self.status, outcome=out.status,
                           output=out.model_dump(mode="json"))  # fmt: skip


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch) -> FakeData:
    fake = FakeData()
    monkeypatch.setattr("ax_agent_template.client.call_agent", fake)
    return fake


def _draft(context_id: str = f"ctx_{WR}", source_kind: str = "brief") -> None:
    caption = fakes.CAPTION
    handlers.STATE.sns.save(sns.SnsPostDraft(
        post_draft_id="spd_m1", source_kind=source_kind, source_ref="PROP-7",
        platform="instagram", media_kind="reel", caption=caption, hashtags=[],
        caption_sha256=sns._sha(caption), required_text=[], context_id=context_id,  # noqa: SLF001
    ))  # fmt: skip


async def _auto(gate: Any) -> pending.PendingPost:
    held = await publisher.publish_sns(fakes._publish_request())  # noqa: SLF001
    assert held.outcome == "held"
    gate.approve_all()
    await publisher.apply_decision(fakes._row(), "approved", "event")  # noqa: SLF001
    row = fakes._row()  # noqa: SLF001
    assert row.status == pending.PUBLISHED
    return row


def test_request_id_comes_from_the_request_context() -> None:
    assert request_video_publish.request_id_of(f"ctx_{WR}") == WR
    for other in ("ctx_req_0001", "ctx_manual_0001", None, "ctx_wr_", "ctx_wr_a b"):
        assert request_video_publish.request_id_of(other) is None


@pytest.mark.asyncio
async def test_auto_publish_registers_the_request_video_once(
    gate: Any, adapter: Any, data: FakeData
) -> None:
    _draft()
    row = await _auto(gate)
    assert len(data.got) == 1
    body = data.got[0].input
    assert body == {"campaign_id": WR, "video_url": row.result["permalink"],
                    "proposal_id": "PROP-7", "test": False}  # fmt: skip
    assert data.got[0].meta.context_id == f"ctx_{WR}"
    assert row.result["performance"] == {"registered": True, "campaign_id": WR,
                                         "video_id": "mock1", "tracking": True}  # fmt: skip
    await publisher.after_published(row.idempotency_key)  # 다시 닫혀도 한 번만
    assert len(data.got) == 1


@pytest.mark.asyncio
async def test_manual_publish_registers_with_the_human_permalink(
    gate: Any, done: Any, data: FakeData
) -> None:
    _draft()
    await fakes._manual_pending(gate)  # noqa: SLF001
    result = await fakes._complete_ok()  # noqa: SLF001
    assert result.outcome == "published"
    assert [r.input["video_url"] for r in data.got] == [fakes.LINK]
    assert data.got[0].input["campaign_id"] == WR
    assert fakes._row().result["performance"]["registered"] is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_register_failure_keeps_the_publish(
    gate: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = FakeData(fail=True)
    monkeypatch.setattr("ax_agent_template.client.call_agent", failing)
    _draft()
    row = await _auto(gate)
    assert row.status == pending.PUBLISHED and row.failure_code is None
    assert row.result["performance"]["registered"] is False
    assert "영상 등록 실패" in row.result["performance"]["warning"]
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_a_crashing_hook_does_not_change_the_publish(gate: Any, adapter: Any) -> None:
    async def boom(_row: Any) -> None:
        raise RuntimeError("훅 실패")

    publisher.RT.on_published = boom
    row = await _auto(gate)
    assert row.status == pending.PUBLISHED and "performance" not in row.result


@pytest.mark.asyncio
@pytest.mark.parametrize(("context_id", "kind"), [
    ("ctx_manual_0001", "brief"),  # 의뢰가 아닌 context
    (f"ctx_{WR}", "notice"),  # 의뢰 context여도 brief 초안이 아니면
])  # fmt: skip
async def test_other_posts_are_not_registered(
    gate: Any, adapter: Any, data: FakeData, context_id: str, kind: str
) -> None:
    _draft(context_id, kind)
    row = await _auto(gate)
    assert data.got == [] and "performance" not in row.result


@pytest.mark.asyncio
async def test_without_a_draft_nothing_is_called(gate: Any, adapter: Any, data: FakeData) -> None:
    row = await _auto(gate)
    assert data.got == [] and "performance" not in row.result
    # 캠페인 경로(campaign._register_video)는 이 훅을 거치지 않는다: test_campaign*.py 그대로 통과
