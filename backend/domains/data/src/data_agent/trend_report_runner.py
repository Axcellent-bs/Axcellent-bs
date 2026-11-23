"""트렌드 분석 의뢰 보고서 실행 (data 에이전트 프로세스 안 asyncio 작업).

- 상태: queued(대기) → running(진행) → done(완료)·partial(부분 완료)·failed(실패).
  프로세스가 다시 켜지면 대기·진행으로 남은 줄은 interrupted(중단됨 + 다시 만들기 필요)
- 동시에 1개만 돈다(asyncio.Lock, 나머지는 대기). 섹션이 끝날 때마다 저장한다
- 같은 의뢰(정규화한 입력 해시, 같은 한국 날짜)는 새로 만들지 않고 저장본을 연다. `rebuild`면 새로
- 끝나면(완료·부분 완료·실패) `data.trend_report_ready`를 monitor.py의 `metric.anomaly`처럼
  `ax_agent_template.events.publish`로 보낸다. 보내지 못해도 보고서는 그대로(로그 한 줄만)
- 보고서 기록은 분석 결과 보관이고 회사 업무 상태를 바꾸지 않아 `execute()`를 부르지 않는다
  (data_chat 대화 기록과 같은 이유)
- 알림(대화 카드·총괄 긴 작업)은 `trend_report_hooks` 한곳에서 보낸다. 러너는 훅만 부른다:
  - 대화 카드(`chat_thread_id`): 접수 직후 진행 카드, 끝에 완료 카드
  - 총괄 긴 작업(caller orchestrator, 15번): 요청마다 task_id를 새로 만들어 `report_waiters`에
    남기고, 섹션이 끝날 때·인스타 계정 10곳마다·`HEARTBEAT_SECONDS`(120초)마다 진행, 끝에 완료
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ax_contracts.schemas import AgentRequest, EventEnvelope, EventType
from ax_contracts.schemas.data import (
    TrendReport,
    TrendReportCreated,
    TrendReportHandoffOutput,
    TrendReportInput,
    TrendReportListItem,
)
from ax_contracts.schemas.event import TrendReportReadyData
from data_agent import company_scope
from data_agent import trend_report_hooks as hooks_mod
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.trend_report_hooks import (  # noqa: F401 - 예전 import 자리 (테스트·대화)
    FINAL,
    TrendReportHooks,
    done_card,
    done_text,
    report_label,
    report_path,
    top_evidence,
)
from data_agent.trend_report_store import TrendReportStore

log = logging.getLogger("data_agent.trend_report")
ETA_SECONDS = 90  # 보고서 하나 대략 (인스타 45곳 + 네이버 6번 + LLM 2번)
MIN_EXPECTED_SECONDS = 120  # 총괄 긴 작업 접수 응답의 예상 소요 하한
HEARTBEAT_SECONDS = 120.0  # 총괄 생존 알림 간격 (총괄 끊김 기준 기본 300초보다 짧게)
ACCOUNT_STEP = 10  # 인스타 계정 이만큼 읽을 때마다 진행 알림
LIVE = ("queued", "running")
LIVE_SECTION = ("waiting", "running")


def brief_hash(request: TrendReportInput, company_id: str | None = None) -> str:
    """같은 의뢰 해시. 회사 ID를 넣는다 (1-5: 같은 의뢰문이라도 회사가 다르면 다른 해시)."""
    body = {"company": company_scope.resolve(company_id), "brief": tb.normalized(request)}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def ready_event(report: TrendReport, now: datetime) -> EventEnvelope:
    key = f"{report.report_id}:{report.status}"
    data = TrendReportReadyData(report_id=report.report_id, status=report.status,  # type: ignore[arg-type]
                                context_id=report.context_id, caller=report.caller,
                                summary_line=report.summary_line)  # fmt: skip
    return EventEnvelope(
        event_id="evt_" + hashlib.sha256(key.encode()).hexdigest()[:16],
        type=EventType.TREND_REPORT_READY,
        occurred_at=now,
        context_id=report.context_id,
        source="data",
        tenant_id=company_scope.meta_tenant(),  # 1-5: 그 요청의 회사 (캣북스는 예전 값 그대로)
        idempotency_key=key,
        data=data.model_dump(mode="json"),
        untrusted={},
    )


Publish = Callable[[EventEnvelope], Any]
DepsFactory = Callable[[], tr.Deps]
ChatFactory = Callable[[], Any]  # DataChatStore (없으면 None)
Sleep = Callable[[float], Awaitable[None]]
Waiter = tuple[AgentRequest, str]  # (총괄 요청, 새 task_id)


class TrendReportService:
    def __init__(self, store: TrendReportStore, deps: DepsFactory, *,
                 publish: Publish | None = None,
                 clock: Callable[[], datetime] | None = None,
                 chat: ChatFactory | None = None,
                 sleep: Sleep | None = None,
                 heartbeat: float = HEARTBEAT_SECONDS) -> None:  # fmt: skip
        self.store = store
        self._deps = deps
        self._publish = publish
        self._clock = clock or (lambda: datetime.now(UTC))
        self.hooks = TrendReportHooks(store, chat=chat, publish=publish)
        self._sleep = sleep or asyncio.sleep
        self._heartbeat = heartbeat
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._beats: dict[str, asyncio.Task[None]] = {}
        self._side: set[asyncio.Task[Any]] = set()
        # 다시 켜졌을 때: 프로세스 안에만 있던 작업은 사라졌다
        self.interrupted = store.interrupt_live(self._clock())
        for report_id in self.interrupted:
            if (report := self.get(report_id)) is not None:
                self.hooks.chat_done(report)
        # 끝났는데 완료를 못 보낸 총괄 기다림(중단 포함)에 완료를 보낸다 (루프가 있으면 바로)
        self._resumed = False
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # 이벤트 루프 밖에서 만들었으면 첫 create에서
            pass
        else:
            self._spawn(self.resume())

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
        self._side.add(task)
        task.add_done_callback(self._side.discard)
        return task

    async def resume(self) -> None:
        """다시 켜진 뒤: 끝났는데(중단 포함) 완료를 못 받은 총괄 기다림에 완료 알림."""
        self._resumed = True
        await self.hooks.resume(self._get_any_company)

    def _get_any_company(self, report_id: str) -> TrendReport | None:
        """재시작 정리용: 회사를 보지 않고 읽는다 (기다리는 쪽은 모든 회사 것)."""
        row = self.store.get(report_id, all_companies=True)
        return TrendReport.model_validate(row["result"]) if row else None

    # ---------------------------------------------------------------- 의뢰 넣기·읽기

    async def create(self, request: TrendReportInput, context_id: str | None = None, *,
                     waiter: Waiter | None = None) -> TrendReportCreated:  # fmt: skip
        """의뢰를 넣는다. `waiter`(총괄 요청, task_id)가 있으면 기다리는 쪽으로 남긴다."""
        if not self._resumed:
            await self.resume()
        now = self._clock()
        digest, day = brief_hash(request), tr.kst_day(now)
        if not request.rebuild and (same := self.store.same(digest, day)) is not None:
            state = self.store.statuses(same)
            status = state[0] if state else "done"
            eta = self._eta(same) if status in LIVE else None
            done = self.get(same) if status in FINAL else None
            if waiter is not None:
                self.hooks.add_waiter(same, waiter[0], waiter[1], now)
                if done is not None:  # 이미 끝난 저장본: 접수 응답 뒤 바로 완료
                    self._spawn(self.hooks.completed(done))
                else:
                    self._ensure_beat(same)
            self.hooks.chat_started(same, request, status, done, live=same in self._tasks)
            return TrendReportCreated(report_id=same, status=status, reused=True,  # type: ignore[arg-type]
                                      eta_seconds=eta)  # fmt: skip
        report_id = "tr_" + uuid.uuid4().hex[:16]
        ctx = request.context_id or context_id
        report = TrendReport(report_id=report_id, status="queued", created_at=now,
                             caller=request.caller, context_id=ctx,
                             brief=tb.input_brief(request))  # fmt: skip
        self.store.create(report_id, digest, day, request.model_dump(mode="json"), request.caller,
                          ctx, report.model_dump(mode="json"), now,
                          chat_thread_id=request.chat_thread_id)  # fmt: skip
        self.hooks.chat_started(report_id, request, "queued", None, live=True)
        if waiter is not None:
            self.hooks.add_waiter(report_id, waiter[0], waiter[1], now)
        self._tasks[report_id] = asyncio.create_task(self._run(report_id, request, ctx))
        if waiter is not None:
            self._ensure_beat(report_id)
        return TrendReportCreated(report_id=report_id, status="queued", reused=False,
                                  eta_seconds=self._eta(report_id))  # fmt: skip

    def _eta(self, report_id: str) -> int:
        return ETA_SECONDS * (1 + self.store.waiting_ahead(report_id))

    def expected_seconds(self, report_id: str) -> int:
        """총괄 접수 응답의 예상 소요 (앞 대기 포함, 최소 120초)."""
        return max(MIN_EXPECTED_SECONDS, self._eta(report_id))

    def get(self, report_id: str) -> TrendReport | None:
        row = self.store.get(report_id)
        return TrendReport.model_validate(row["result"]) if row else None

    def list(self, limit: int) -> list[TrendReportListItem]:
        from data_agent.skills.trend_report_insight import build_insight

        out = []
        for raw, handoff in self.store.recent_with_handoff(limit):
            r = TrendReport.model_validate(raw)
            insight, _ = build_insight(r)
            out.append(TrendReportListItem(
                report_id=r.report_id, status=r.status, created_at=r.created_at, caller=r.caller,
                product=r.brief.product, summary_line=r.summary_line,
                has_insight=None if r.status in LIVE else insight is not None,
                handoff=TrendReportHandoffOutput.model_validate(handoff) if handoff else None,
            ))  # fmt: skip
        return out

    async def wait(self, report_id: str) -> None:
        """테스트·내부용: 그 보고서 작업(과 생존 알림)이 끝날 때까지."""
        task = self._tasks.get(report_id)
        if task is not None:
            await task
        beat = self._beats.get(report_id)
        if beat is not None:
            await asyncio.gather(beat, return_exceptions=True)
        while self._side:
            await asyncio.gather(*list(self._side), return_exceptions=True)

    # ---------------------------------------------------------------- 생존 알림

    def _ensure_beat(self, report_id: str) -> None:
        running = self._beats.get(report_id)
        if running is None or running.done():
            self._beats[report_id] = asyncio.create_task(self._beat(report_id))

    async def _beat(self, report_id: str) -> None:
        """120초마다 생존 알림. 대기열이면 "대기 중 (앞에 N개)". 끝나면 멈춘다."""
        while True:
            await self._sleep(self._heartbeat)
            state = self.store.statuses(report_id)
            if state is None or state[0] not in LIVE:
                return
            if state[0] == "queued":
                note = f"대기 중 (앞에 {self.store.waiting_ahead(report_id)}개)"
                percent = 0
            else:
                report = self.get(report_id)
                note = "만드는 중"
                percent = hooks_mod.section_percent(report) if report else None
            await self.hooks.progress(report_id, percent=percent, note=note)

    # ---------------------------------------------------------------- 만들기

    async def _run(self, report_id: str, request: TrendReportInput, ctx: str | None) -> None:
        async with self._lock:  # 동시에 1개만
            row = self.store.get(report_id)
            if row is None:
                return
            report = TrendReport.model_validate(row["result"])
            seen: set[str] = set()

            async def saved(r: TrendReport) -> None:
                self.store.save(r.report_id, r.model_dump(mode="json"))
                names = tr.counted_sections(r)  # 주제 후보(find 의뢰)도 섹션 하나로
                done = {name for name in names
                        if getattr(r.sections, name).status not in LIVE_SECTION}  # fmt: skip
                if fresh := done - seen:  # 섹션 하나가 끝날 때마다
                    seen.update(done)
                    note = hooks_mod.section_note(r, len(done), len(names), fresh)
                    await self.hooks.progress(report_id, percent=hooks_mod.section_percent(r),
                                              note=note)  # fmt: skip

            async def accounts(read: int, total: int) -> None:
                if read % ACCOUNT_STEP == 0 or read == total:
                    await self.hooks.progress(report_id, percent=0,
                                              note=f"인스타 계정 {read}/{total}")  # fmt: skip

            try:
                deps = self._deps()
                async def note(text: str) -> None:  # 예: "회사 경쟁사 찾는 중"
                    await self.hooks.progress(report_id, percent=0, note=text)

                await tr.build(report, request, deps, self.store, now=self._clock(),
                               progress=saved, context_id=ctx,
                               account_progress=accounts, note=note)  # fmt: skip
            except Exception as error:  # noqa: BLE001 - 보고서 전체 실패도 기록하고 알린다
                log.warning("trend_report %s 실패 (%s)", report_id, type(error).__name__)
                report.status = "failed"
                report.finished_at = self._clock()
                report.limits = [*report.limits, f"보고서를 만들지 못함 ({type(error).__name__})"]
                self.store.save(report_id, report.model_dump(mode="json"))
            beat = self._beats.pop(report_id, None)
            if beat is not None:  # 끝났으니 생존 알림은 멈춘다
                beat.cancel()
            await self._notify(report)
            if report.status in FINAL:
                self.hooks.chat_done(report)
                await self.hooks.completed(report)
        self._tasks.pop(report_id, None)

    async def _notify(self, report: TrendReport) -> None:
        if report.status not in FINAL:
            return
        publish = self._publish
        if publish is None:
            from ax_agent_template.events import publish as default_publish

            publish = default_publish
        try:
            got = publish(ready_event(report, self._clock()))
            if asyncio.iscoroutine(got):
                await got
        except Exception as error:  # noqa: BLE001 - EventBusUnavailable 등: 보고서는 그대로
            log.warning("trend_report %s 알림 못 보냄 (%s)", report.report_id,
                        type(error).__name__)  # fmt: skip
