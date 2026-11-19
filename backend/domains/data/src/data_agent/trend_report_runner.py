"""트렌드 분석 의뢰 보고서 실행 (data 에이전트 프로세스 안 asyncio 작업).

- 상태: queued(대기) → running(진행) → done(완료)·partial(부분 완료)·failed(실패).
  프로세스가 다시 켜지면 대기·진행으로 남은 줄은 interrupted(중단됨 + 다시 만들기 필요)
- 동시에 1개만 돈다(asyncio.Lock, 나머지는 대기). 섹션이 끝날 때마다 저장한다
- 같은 의뢰(정규화한 입력 해시, 같은 한국 날짜)는 새로 만들지 않고 저장본을 연다. `rebuild`면 새로
- 끝나면(완료·부분 완료·실패) `data.trend_report_ready`를 monitor.py의 `metric.anomaly`처럼
  `ax_agent_template.events.publish`로 보낸다. 보내지 못해도 보고서는 그대로(로그 한 줄만)
- 보고서 기록은 분석 결과 보관이고 회사 업무 상태를 바꾸지 않아 `execute()`를 부르지 않는다
  (data_chat 대화 기록과 같은 이유)
- `chat_thread_id`(데이터분석 대화 카드로 넣은 의뢰)면 그 대화에 코드 문장 메시지를 붙인다(LLM 없음,
  DataChatStore 직접): 접수 직후 "의뢰 TR-… 만드는 중…" + 진행 카드, 끝(완료·부분·실패·중단)에
  "보고서 완료"·"일부만 완료"·"실패: 이유" + 완료 카드. 대화가 닫혔거나 그 대화의 의뢰 카드
  (같은 의뢰문)가 없으면(남의 대화·엉뚱한 id) 붙이지 않고 로그만
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ax_contracts.schemas import EventEnvelope, EventType
from ax_contracts.schemas.data import (
    TrendReport,
    TrendReportCreated,
    TrendReportDoneCard,
    TrendReportInput,
    TrendReportListItem,
    TrendReportProgressCard,
)
from ax_contracts.schemas.event import TrendReportReadyData
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.trend_report_store import INTERRUPTED_REASON, TrendReportStore

log = logging.getLogger("data_agent.trend_report")
ETA_SECONDS = 90  # 보고서 하나 대략 (인스타 45곳 + 네이버 6번 + LLM 2번)
FINAL = ("done", "partial", "failed")


def brief_hash(request: TrendReportInput) -> str:
    raw = json.dumps(tb.normalized(request), ensure_ascii=False, sort_keys=True)
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
        idempotency_key=key,
        data=data.model_dump(mode="json"),
        untrusted={},
    )


Publish = Callable[[EventEnvelope], Any]
DepsFactory = Callable[[], tr.Deps]
ChatFactory = Callable[[], Any]  # DataChatStore (없으면 None)

# ---------------------------------------------------------------- 대화 카드 메시지 (LLM 없음)

DONE_WORDS = {"done": "보고서 완료", "partial": "일부만 완료"}
MAX_TOP_EVIDENCE = 3


def short_id(report_id: str) -> str:
    return "TR-" + report_id.removeprefix("tr_")[:6]


def report_path(report_id: str) -> str:
    return f"/data/trend#request?id={report_id}"


def top_evidence(report: TrendReport) -> list[str]:
    """콘티 근거 앞쪽 3개 (short가 있으면 그것, 같은 것은 한 번)."""
    out: list[str] = []
    for beat in report.sections.recommendation.beats:
        items = ([e.short or e.text for e in beat.evidence_items] if beat.evidence_items
                 else list(beat.evidence))  # fmt: skip
        for text in items:
            if text and text not in out:
                out.append(text)
    return out[:MAX_TOP_EVIDENCE]


def done_text(report: TrendReport) -> str:
    head = f"의뢰 {short_id(report.report_id)}"
    if report.status in DONE_WORDS:
        return f"{head} {DONE_WORDS[report.status]}"
    if report.status == "interrupted":
        why = INTERRUPTED_REASON
    else:
        why = report.summary_reason or (report.limits[-1] if report.limits else "이유 모름")
    return f"{head} 실패: {why}"


def done_card(report: TrendReport) -> dict[str, Any]:
    card = TrendReportDoneCard(report_id=report.report_id, status=report.status,
                               summary_line=report.summary_line,
                               top_evidence=top_evidence(report),
                               report_path=report_path(report.report_id))  # fmt: skip
    return card.model_dump(mode="json")


class TrendReportService:
    def __init__(self, store: TrendReportStore, deps: DepsFactory, *,
                 publish: Publish | None = None,
                 clock: Callable[[], datetime] | None = None,
                 chat: ChatFactory | None = None) -> None:  # fmt: skip
        self.store = store
        self._deps = deps
        self._publish = publish
        self._clock = clock or (lambda: datetime.now(UTC))
        self._chat = chat
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # 같은 의뢰 저장본을 연 대화도 끝 메시지를 받는다: 보고서 id → [(대화 id, 의뢰문)]
        self._watchers: dict[str, list[tuple[str, str | None]]] = {}
        # 다시 켜졌을 때: 프로세스 안에만 있던 작업은 사라졌다
        self.interrupted = store.interrupt_live(self._clock())
        for report_id in self.interrupted:
            self._chat_done(report_id)

    # ---------------------------------------------------------------- 대화에 붙이기

    def _say(self, thread_id: str, request_text: str | None, text: str,
             card: dict[str, Any]) -> None:  # fmt: skip
        """대화에 코드 문장 + 카드를 붙인다. 닫혔거나 이 대화 카드가 아니면 붙이지 않고 로그만."""
        try:
            chat = self._chat() if self._chat is not None else None
            if chat is None:
                return
            outcome = chat.add_report_message(thread_id, text, card, request_text)
        except Exception as error:  # noqa: BLE001 - 대화에 못 붙여도 보고서는 그대로
            log.warning("trend_report 대화 %s에 못 붙임 (%s)", thread_id, type(error).__name__)
            return
        if outcome != "ok":
            log.info("trend_report 대화 %s에 붙이지 않음 (%s)", thread_id, outcome)

    def _chat_targets(self, report_id: str) -> list[tuple[str, str | None]]:
        targets = list(self._watchers.pop(report_id, []))
        thread = self.store.chat_thread(report_id)
        if thread:
            row = self.store.get(report_id) or {}
            targets.insert(0, (thread, (row.get("input") or {}).get("request_text")))
        return list(dict.fromkeys(targets))

    def _chat_done(self, report_id: str) -> None:
        targets = self._chat_targets(report_id)
        if not targets:
            return
        report = self.get(report_id)
        if report is None:
            return
        for thread, text in targets:
            self._say(thread, text, done_text(report), done_card(report))

    def _chat_started(self, report_id: str, request: TrendReportInput, status: str) -> None:
        """접수 직후 진행 메시지. 저장본이 이미 끝났으면 바로 끝 메시지."""
        thread = request.chat_thread_id
        if not thread:
            return
        if status in FINAL:
            report = self.get(report_id)
            if report is not None:
                self._say(thread, request.request_text, done_text(report), done_card(report))
            return
        card = TrendReportProgressCard(report_id=report_id).model_dump(mode="json")
        self._say(thread, request.request_text, f"의뢰 {short_id(report_id)} 만드는 중…", card)

    # ---------------------------------------------------------------- 의뢰 넣기·읽기

    async def create(self, request: TrendReportInput, context_id: str | None = None
                     ) -> TrendReportCreated:  # fmt: skip
        now = self._clock()
        digest, day = brief_hash(request), tr.kst_day(now)
        if not request.rebuild and (same := self.store.same(digest, day)) is not None:
            state = self.store.statuses(same)
            status = state[0] if state else "done"
            eta = self._eta(same) if status in ("queued", "running") else None
            if request.chat_thread_id and status not in FINAL and same in self._tasks:
                self._watchers.setdefault(same, []).append(
                    (request.chat_thread_id, request.request_text))
            self._chat_started(same, request, status)
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
        self._chat_started(report_id, request, "queued")
        self._tasks[report_id] = asyncio.create_task(self._run(report_id, request, ctx))
        return TrendReportCreated(report_id=report_id, status="queued", reused=False,
                                  eta_seconds=self._eta(report_id))  # fmt: skip

    def _eta(self, report_id: str) -> int:
        return ETA_SECONDS * (1 + self.store.waiting_ahead(report_id))

    def get(self, report_id: str) -> TrendReport | None:
        row = self.store.get(report_id)
        return TrendReport.model_validate(row["result"]) if row else None

    def list(self, limit: int) -> list[TrendReportListItem]:
        out = []
        for raw in self.store.recent(limit):
            r = TrendReport.model_validate(raw)
            out.append(TrendReportListItem(report_id=r.report_id, status=r.status,
                                           created_at=r.created_at, caller=r.caller,
                                           product=r.brief.product,
                                           summary_line=r.summary_line))  # fmt: skip
        return out

    async def wait(self, report_id: str) -> None:
        """테스트·내부용: 그 보고서 작업이 끝날 때까지."""
        task = self._tasks.get(report_id)
        if task is not None:
            await task

    # ---------------------------------------------------------------- 만들기

    async def _run(self, report_id: str, request: TrendReportInput, ctx: str | None) -> None:
        async with self._lock:  # 동시에 1개만
            row = self.store.get(report_id)
            if row is None:
                return
            report = TrendReport.model_validate(row["result"])
            try:
                deps = self._deps()
                await tr.build(report, request, deps, self.store, now=self._clock(),
                               progress=lambda r: self.store.save(r.report_id,
                                                                  r.model_dump(mode="json")),
                               context_id=ctx)  # fmt: skip
            except Exception as error:  # noqa: BLE001 - 보고서 전체 실패도 기록하고 알린다
                log.warning("trend_report %s 실패 (%s)", report_id, type(error).__name__)
                report.status = "failed"
                report.finished_at = self._clock()
                report.limits = [*report.limits, f"보고서를 만들지 못함 ({type(error).__name__})"]
                self.store.save(report_id, report.model_dump(mode="json"))
            await self._notify(report)
            if report.status in FINAL:
                self._chat_done(report_id)
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
