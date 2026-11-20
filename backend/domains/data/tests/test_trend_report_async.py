"""총괄 의뢰 15번: trend_report_create 긴 작업 + trend_report_insight (실제 API·LLM 없음).

총괄 plan ① trend_report_create(caller orchestrator → WORKING + task_id, 진행·완료 알림)
→ ② trend_report_insight(report_id) → ③ planning receive_insight(②의 insight 그대로).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import async_task
from ax_agent_template.services import BY_ID
from ax_contracts.schemas import ACCEPTED_ASYNC, AgentRequest, EventType, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    TrendReport,
    TrendReportCompletedOutput,
    TrendReportInput,
    TrendReportInsightOutput,
)
from ax_contracts.schemas.planning import ReceiveInsightInput
from data_agent import handlers
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_insight as ti
from data_agent.trend_report_runner import TrendReportService
from data_agent.trend_report_store import INTERRUPTED_REASON, TrendReportStore

# 보고서 테스트의 가짜(LLM·인스타·네이버)를 그대로 쓴다 (tests는 패키지가 아니라 파일로 읽는다)
_SPEC = importlib.util.spec_from_file_location(
    "trend_report_fakes", Path(__file__).with_name("test_trend_report.py"))
assert _SPEC is not None and _SPEC.loader is not None
fakes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fakes)
NOW, _service, _filled, _report = fakes.NOW, fakes._service, fakes._filled, fakes._report  # noqa: SLF001

REQUEST = {"preset": "eco_beauty", "keywords": ["리필 화장품"], "caller": "orchestrator"}


def _meta(ctx: str = "ctx_orch_1") -> RequestMeta:
    return RequestMeta(context_id=ctx, token="x", tenant_id="catbooks",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip


def _call(skill: str, data: dict[str, Any], ctx: str = "ctx_orch_1") -> Any:
    return handlers.HANDLERS[skill](AgentRequest(meta=_meta(ctx), skill=skill, input=data))


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """async_task가 보내는 알림 (총괄 이벤트 버스 자리)."""
    got: list[Any] = []

    async def publish(envelope: Any) -> None:
        got.append(envelope)

    monkeypatch.setattr(async_task, "publish", publish)
    return got


def _of(events: list[Any], kind: EventType, task_id: str | None = None) -> list[Any]:
    return [e for e in events if e.type == kind
            and (task_id is None or e.data["task_id"] == task_id)]  # fmt: skip


# ---------------------------------------------------------------- 1·3. 접수 · 완료


def test_orchestrator_call_is_async_and_completes_with_numbers_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[Any]
) -> None:
    service = _service(tmp_path, [])
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)

    async def run() -> Any:
        got = await _call("trend_report_create", REQUEST)
        report_id = service.store.waiters()[0]["report_id"] if service.store.waiters() else None
        await service.wait(report_id or "")
        for rid in list(service._tasks):  # noqa: SLF001
            await service.wait(rid)
        return got

    got = asyncio.run(run())
    assert got.status == TaskStatus.WORKING and got.outcome == ACCEPTED_ASYNC
    task_id = got.output["task_id"]
    assert task_id.startswith("tsk_") and got.output["expected_seconds"] >= 120
    done = _of(events, EventType.TASK_COMPLETED, task_id)
    assert len(done) == 1 and done[0].idempotency_key == f"task.completed:{task_id}"
    assert done[0].context_id == "ctx_orch_1" and done[0].tenant_id == "catbooks"
    data = done[0].data
    assert data["status"] == "done" and data["skill"] == "trend_report_create"
    out = TrendReportCompletedOutput.model_validate(data["output"])
    assert set(data["output"]) == {"report_id", "status", "summary_line", "report_path",
                                   "has_insight", "insight_reason"}  # 번호와 짧은 사실만
    assert out.report_path == f"/data/trend#request?id={out.report_id}" and out.has_insight
    # 섹션이 끝날 때마다 진행 알림 (총괄 기본 300초보다 자주)
    notes = [e.data["note"] for e in _of(events, EventType.TASK_PROGRESS, task_id)]
    assert any(n.startswith("섹션 ") for n in notes)
    assert {"섹션 5/5"} <= set(notes)


def test_human_call_keeps_the_old_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         events: list[Any]) -> None:  # fmt: skip
    service = _service(tmp_path, [])
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)

    async def run() -> Any:
        got = await _call("trend_report_create", {**REQUEST, "caller": "human"})
        await service.wait(got.output["report_id"])
        return got

    got = asyncio.run(run())
    assert got.status == TaskStatus.COMPLETED and got.output["status"] == "queued"
    assert set(got.output) == {"report_id", "status", "reused", "eta_seconds"}
    assert events == [] and service.store.waiters() == []


def test_two_orchestrator_events_get_their_own_task_and_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[Any]
) -> None:
    service = _service(tmp_path, [])
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)

    async def run() -> tuple[Any, Any, Any]:
        a = await _call("trend_report_create", REQUEST, "ctx_a")
        b = await _call("trend_report_create", REQUEST, "ctx_b")  # 만드는 중인 같은 의뢰 (reused)
        rid = next(iter(service._tasks))  # noqa: SLF001
        await service.wait(rid)
        c = await _call("trend_report_create", REQUEST, "ctx_c")  # 이미 끝난 저장본
        await service.wait(rid)
        return a, b, c

    a, b, c = asyncio.run(run())
    ids = [x.output["task_id"] for x in (a, b, c)]
    assert len(set(ids)) == 3  # 요청마다 새 task_id (완료 멱등 키가 겹치지 않게)
    for task_id, ctx in zip(ids, ("ctx_a", "ctx_b", "ctx_c"), strict=True):
        done = _of(events, EventType.TASK_COMPLETED, task_id)
        assert len(done) == 1 and done[0].context_id == ctx
    reports = {e.data["output"]["report_id"] for e in _of(events, EventType.TASK_COMPLETED)}
    assert len(reports) == 1  # 같은 보고서
    assert service.store.waiters() == []  # 모두 보냄


def test_failed_report_completes_failed_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[Any]
) -> None:
    service = _service(tmp_path, [])
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(tr, "build", boom)

    async def run() -> Any:
        got = await _call("trend_report_create", REQUEST)
        for rid in list(service._tasks):  # noqa: SLF001
            await service.wait(rid)
        return got

    got = asyncio.run(run())
    done = _of(events, EventType.TASK_COMPLETED, got.output["task_id"])[0].data
    assert done["status"] == "failed" and done["output"] == {}
    assert done["note"] == "보고서를 만들지 못함 (RuntimeError)" and len(done["note"]) <= 200


# ---------------------------------------------------------------- 2. 진행 알림 간격 · 대기열


def test_heartbeat_and_queue_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   events: list[Any]) -> None:  # fmt: skip
    gate = asyncio.Event
    beats: list[float] = []

    async def run() -> tuple[str, str]:
        hold = gate()
        original = tr.build

        async def slow(report: Any, *args: Any, **kwargs: Any) -> Any:
            if report.brief.keywords == ["첫째"]:  # 진행 중으로 저장한 채 멈춰 있다
                report.status = "running"
                await kwargs["progress"](report)
                await hold.wait()
            return await original(report, *args, **kwargs)

        monkeypatch.setattr(tr, "build", slow)

        async def tick(seconds: float) -> None:  # 가짜 시계: 120초를 기다린 셈
            beats.append(seconds)
            await asyncio.sleep(0)

        service = _service(tmp_path, [])
        service._sleep, service._heartbeat = tick, 120.0  # noqa: SLF001
        monkeypatch.setattr(handlers, "_TREND_REPORTS", service)
        first = await _call("trend_report_create", {**REQUEST, "keywords": ["첫째"]})
        second = await _call("trend_report_create", {**REQUEST, "keywords": ["둘째"]})
        for _ in range(20):  # 생존 알림 몇 번
            await asyncio.sleep(0)
        hold.set()
        for rid in list(service._tasks):  # noqa: SLF001
            await service.wait(rid)
        return first.output["task_id"], second.output["task_id"]

    first, second = asyncio.run(run())
    assert beats and set(beats) == {120.0}
    waiting = [e.data["note"] for e in _of(events, EventType.TASK_PROGRESS, second)]
    assert "대기 중 (앞에 1개)" in waiting
    running = [e.data["note"] for e in _of(events, EventType.TASK_PROGRESS, first)]
    assert "만드는 중" in running
    for task_id in (first, second):
        assert len(_of(events, EventType.TASK_COMPLETED, task_id)) == 1


def test_account_progress_every_ten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    events: list[Any]) -> None:  # fmt: skip
    from data_agent.skills import trend_report_brief as tb

    FakeGraph, FakeLLM, MEMES = fakes.FakeGraph, fakes.FakeLLM, fakes.MEMES
    _account, _deps = fakes._account, fakes._deps  # noqa: SLF001

    preset = tb.load_preset("eco_beauty")
    assert preset is not None
    media = {a: _account(a) for a in preset.accounts}
    service = TrendReportService(
        TrendReportStore(), lambda: _deps(tmp_path, FakeLLM(), FakeGraph(media), memes=MEMES),
        publish=lambda e: None, clock=lambda: NOW)  # fmt: skip
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)

    async def run() -> str:
        got = await _call("trend_report_create", REQUEST)
        for rid in list(service._tasks):  # noqa: SLF001
            await service.wait(rid)
        return got.output["task_id"]

    task_id = asyncio.run(run())
    notes = [e.data["note"] for e in _of(events, EventType.TASK_PROGRESS, task_id)]
    total = len(preset.accounts)
    assert [n for n in notes if n.startswith("인스타 계정")] == [
        f"인스타 계정 {k}/{total}" for k in (10, 20, 30, total)]  # fmt: skip


# ---------------------------------------------------------------- 3. 재시작 뒤 waiter 표로


def test_restart_sends_failed_completion_from_the_waiter_table(tmp_path: Path) -> None:
    path = tmp_path / "reports.db"
    store = TrendReportStore(path)
    brief = _report(TrendReportInput()).brief
    report = TrendReport(report_id="tr_restart01", status="running", created_at=NOW,
                         caller="orchestrator", brief=brief)  # fmt: skip
    store.create(report.report_id, "h", NOW.date(), {}, "orchestrator", "ctx_r",
                 report.model_dump(mode="json"), NOW)  # fmt: skip
    store.add_waiter(report.report_id, "tsk_old", "catbooks", "ctx_r", "trend_report_create", NOW)
    store.close()
    sent: list[Any] = []

    async def run() -> None:
        service = TrendReportService(TrendReportStore(path), lambda: None,  # type: ignore[arg-type, return-value]
                                     publish=sent.append, clock=lambda: NOW)  # fmt: skip
        await service.resume()
        await service.resume()  # 두 번째는 보내지 않는다 (보낸 표시)
        await service.wait("")

    asyncio.run(run())
    done = [e for e in sent if e.type == EventType.TASK_COMPLETED]
    assert len(done) == 1
    e = done[0]
    assert e.idempotency_key == "task.completed:tsk_old" and e.source == "data"
    assert e.context_id == "ctx_r" and e.tenant_id == "catbooks"
    assert e.data["status"] == "failed" and e.data["note"] == INTERRUPTED_REASON
    assert TrendReportStore(path).waiters() == []


# ---------------------------------------------------------------- 4·5. trend_report_insight


def test_insight_of_a_finished_report_is_the_planning_input(tmp_path: Path) -> None:
    report = _filled(tmp_path)
    insight, why = ti.build_insight(report)
    assert why is None and insight is not None
    again = ReceiveInsightInput.model_validate(insight.model_dump(mode="json"))  # 계약 그대로
    assert again.insight_id == f"trend_report:{report.report_id}"
    assert again.title.startswith("트렌드 분석: ") and again.skill == "trend_report_create"
    assert 1 <= len(again.facts) <= 12
    assert all(f.source == "trend_report_create" for f in again.facts)
    assert again.audience == "20~30대 여성" and again.shorts_format == "릴스"
    assert again.products == [] and again.product_ids == [] and again.content_format is None
    assert again.generated_at == report.finished_at
    assert again.body_md and "## 결론" in again.body_md and len(again.body_md) <= 8000
    # 콘티 근거가 가리키는 행이 먼저
    beat_ids = [e.id for b in report.sections.recommendation.beats for e in b.evidence_items]
    first = ti.row_facts(report)[beat_ids[0]]
    assert again.facts[0].label == ti.clip(first[0], 120)
    # 두 번 불러도 같은 결과
    assert ti.build_insight(report)[0] == insight


def test_insight_caps_clips_and_drops_small_base(tmp_path: Path) -> None:
    report = _filled(tmp_path)
    for r in report.sections.search.rows:
        r.keyword = "아주 긴 키워드 " * 20
    report.sections.search.rows[0].small_base = True
    report.sections.search.rows[0].search_change_pct = 999.0
    insight, _ = ti.build_insight(report)
    assert insight is not None and len(insight.facts) <= 12
    assert all(len(f.label) <= 120 and len(f.value) <= 80 for f in insight.facts)
    assert "search:1" not in ti.row_facts(report)  # small_base 제외
    assert all("+999" not in f.value for f in insight.facts)


def test_insight_null_with_reasons(tmp_path: Path) -> None:
    assert ti.build_insight(None) == (None, ti.NO_REPORT)
    running = _report(TrendReportInput(preset="eco_beauty"))
    running.status = "running"
    assert ti.build_insight(running) == (None, ti.NOT_DONE)
    empty = _report(TrendReportInput(preset="eco_beauty"))
    empty.status = "failed"
    assert ti.build_insight(empty) == (None, ti.NO_FACTS)


def test_insight_handler_and_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path, [])
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)
    report = _filled(tmp_path)
    service.store.create(report.report_id, "h", NOW.date(), {}, "orchestrator", None,
                         report.model_dump(mode="json"), NOW)  # fmt: skip
    got = asyncio.run(_call("trend_report_insight", {"report_id": report.report_id}))
    again = asyncio.run(_call("trend_report_insight", {"report_id": report.report_id}))
    out = TrendReportInsightOutput.model_validate(got.output)
    assert got.outcome == "ready" and out.insight is not None and out.reason is None
    assert again.output == got.output  # 같은 결과 (다시 돌리기·재시작 뒤에도 안전)
    missing = asyncio.run(_call("trend_report_insight", {"report_id": "tr_none"}))
    assert missing.outcome == "not_ready" and missing.output["reason"] == ti.NO_REPORT
    assert "trend_report_insight" in BY_ID["data"].skills
    assert "trend_report_insight" in handlers.TIMED_SKILLS
    for card in ("domains/data/agent-card.json", "services/data/agent-card.json"):
        raw = json.loads((Path(__file__).parents[3] / card).read_text(encoding="utf-8"))
        skill = next(s for s in raw["skills"] if s["id"] == "trend_report_insight")
        assert skill["grade"] == "read" and "orchestrator" in skill["allowed_requesters"]
