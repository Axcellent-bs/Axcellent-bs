"""트렌드 분석 보고서 알림을 한곳에 (러너는 여기 함수만 부른다).

1. 데이터분석 대화 카드 (#1406): 접수 직후 진행 카드, 끝에 완료 카드 (코드 문장, LLM 없음)
2. 총괄 긴 작업 (15번, `ax_agent_template.async_task` 규약): 기다리는 쪽(`report_waiters`)마다
   - 진행 `task.progress`: 섹션이 끝날 때·인스타 계정 10곳마다·120초마다 생존(대기 중이면 "대기 중
     (앞에 N개)"). 총괄 기본 끊김 기준 300초보다 자주
   - 완료 `task.completed` 한 번: done·partial이면 output = TrendReportCompletedOutput(번호와 짧은
     사실만), failed·interrupted면 failed + note(이유 200자 이내)
   - 요청(AgentRequest)이 프로세스 안에 있으면 async_task 함수를 그대로 쓰고, 다시 켜진 뒤처럼
     요청이 없으면 waiter 값으로 같은 모양의 envelope를 만든다(`_envelope`, 토큰은 저장 안 함)
   - 완료 멱등 키는 `task.completed:<task_id>`라 두 번 보내도 한 번만 전달된다
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ax_contracts.schemas import (
    AgentRequest,
    EventEnvelope,
    EventType,
    TaskCompletedData,
    TaskProgressData,
)
from ax_contracts.schemas.data import (
    TrendReport,
    TrendReportCompletedOutput,
    TrendReportDoneCard,
    TrendReportInput,
    TrendReportProgressCard,
)
from data_agent.trend_report_store import INTERRUPTED_REASON, TrendReportStore

log = logging.getLogger("data_agent.trend_report")
SOURCE = "data"
FINAL = ("done", "partial", "failed")
ENDED = (*FINAL, "interrupted")
DONE_WORDS = {"done": "보고서 완료", "partial": "일부만 완료"}
MAX_TOP_EVIDENCE = 3
NOTE_MAX = 200
SECTION_NAMES = ("search", "hashtags", "content", "competitors", "recommendation")


def report_label(report_id: str) -> str:
    """보고서 번호 표기 (화면·대화 문장 공통): "TR-" + "tr_" 뒤 앞 4글자 대문자.

    예: tr_b8358e… → TR-B835
    """
    return "TR-" + report_id.removeprefix("tr_")[:4].upper()


def report_path(report_id: str) -> str:
    return f"/data/trend#request?id={report_id}"


# ---------------------------------------------------------------- 대화 카드 문장


def top_evidence(report: TrendReport) -> list[str]:
    """추천 근거 앞쪽 3개 (short가 있으면 그것, 같은 것은 한 번).
    재료(11/23) 먼저, 옛 보고서는 콘티."""
    out: list[str] = []
    rec = report.sections.recommendation
    for beat in [*rec.materials, *rec.beats]:
        items = ([e.short or e.text for e in beat.evidence_items] if beat.evidence_items
                 else list(beat.evidence))  # fmt: skip
        for text in items:
            if text and text not in out:
                out.append(text)
    return out[:MAX_TOP_EVIDENCE]


def failure_reason(report: TrendReport) -> str:
    if report.status == "interrupted":
        return INTERRUPTED_REASON
    return report.summary_reason or (report.limits[-1] if report.limits else "이유 모름")


def done_text(report: TrendReport) -> str:
    head = f"의뢰 {report_label(report.report_id)}"
    if report.status in DONE_WORDS:
        return f"{head} {DONE_WORDS[report.status]}"
    return f"{head} 실패: {failure_reason(report)}"


def done_card(report: TrendReport) -> dict[str, Any]:
    card = TrendReportDoneCard(report_id=report.report_id, status=report.status,
                               summary_line=report.summary_line,
                               top_evidence=top_evidence(report),
                               report_path=report_path(report.report_id))  # fmt: skip
    return card.model_dump(mode="json")


# ---------------------------------------------------------------- 긴 작업 알림 모양


def section_percent(report: TrendReport) -> int:
    """끝난 섹션 비율. 주제 후보(find 의뢰만)도 섹션 하나로 센다."""
    names = (*SECTION_NAMES, "theme") if report.brief.theme_mode == "find" else SECTION_NAMES
    done = sum(1 for name in names
               if getattr(report.sections, name).status not in ("waiting", "running"))  # fmt: skip
    return round(done * 100 / len(names))


SECTION_WORDS = {"search": "검색", "hashtags": "해시태그", "content": "콘텐츠",
                 "competitors": "경쟁사", "recommendation": "AI 추천",
                 "theme": "주제 후보"}  # fmt: skip
EMPTY_WORD = "확인함 · 없음"


def section_note(report: TrendReport, done: int, total: int, fresh: set[str]) -> str:
    """진행 알림 글: "섹션 3/5". 방금 끝난 칸이 empty(봤지만 없음)면 "· 해시태그 확인함 · 없음"."""
    empty = [SECTION_WORDS.get(n, n) for n in sorted(fresh)
             if getattr(report.sections, n).status == "empty"]  # fmt: skip
    head = f"섹션 {done}/{total}"
    return f"{head} · {', '.join(empty)} {EMPTY_WORD}" if empty else head


def completed_output(report: TrendReport) -> dict[str, Any]:
    from data_agent.skills.trend_report_insight import build_insight

    insight, why = build_insight(report)
    out = TrendReportCompletedOutput(
        report_id=report.report_id, status=report.status, summary_line=report.summary_line,
        report_path=report_path(report.report_id), has_insight=insight is not None,
        insight_reason=why,
    )  # fmt: skip
    return out.model_dump(mode="json")


def _envelope(tenant_id: str | None, context_id: str | None, kind: EventType,
              data: dict[str, Any], key: str) -> EventEnvelope:  # fmt: skip
    """async_task._envelope와 같은 모양 (요청 없이 waiter 값으로)."""
    extra: dict[str, Any] = {"tenant_id": tenant_id} if tenant_id else {}
    return EventEnvelope(
        event_id=f"evt_{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:12]}",
        type=kind,
        occurred_at=datetime.now(UTC),
        context_id=context_id,
        source=SOURCE,
        idempotency_key=key,
        data=data,
        **extra,
    )


class TrendReportHooks:
    def __init__(self, store: TrendReportStore, *, chat: Callable[[], Any] | None = None,
                 publish: Callable[[EventEnvelope], Any] | None = None) -> None:  # fmt: skip
        self.store = store
        self._chat = chat
        self._publish = publish
        # 같은 의뢰 저장본을 연 대화도 끝 메시지를 받는다: 보고서 id → [(대화 id, 의뢰문)]
        self._watchers: dict[str, list[tuple[str, str | None]]] = {}
        self._requests: dict[str, AgentRequest] = {}  # task_id → 요청 (이 프로세스 안에서만)

    # ---- 대화 카드 ---------------------------------------------------------------

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

    def _mark_accepted(self, thread: str, request_text: str | None, report_id: str) -> None:
        """그 대화의 같은 의뢰문 의뢰 카드에 accepted_report_id를 적는다 (새로고침해도 "접수됨")."""
        try:
            chat = self._chat() if self._chat is not None else None
            if chat is None:
                return
            outcome = chat.mark_accepted(thread, request_text, report_id)
        except Exception as error:  # noqa: BLE001 - 표시를 못 해도 보고서는 그대로
            log.warning("trend_report 대화 %s 카드 표시 못 함 (%s)", thread, type(error).__name__)
            return
        if outcome != "ok":
            log.info("trend_report 대화 %s 카드 표시 안 함 (%s)", thread, outcome)

    def chat_started(self, report_id: str, request: TrendReportInput, status: str,
                     report: TrendReport | None, *, live: bool) -> None:  # fmt: skip
        """접수 직후 진행 메시지. 저장본이 이미 끝났으면 바로 끝 메시지.

        `live`: 이 프로세스 안에서 아직 도는 보고서라 끝 메시지를 따로 받아야 하는지."""
        thread = request.chat_thread_id
        if not thread:
            return
        self._mark_accepted(thread, request.request_text, report_id)
        if status in FINAL:
            if report is not None:
                self._say(thread, request.request_text, done_text(report), done_card(report))
            return
        if live:
            self._watchers.setdefault(report_id, []).append((thread, request.request_text))
        card = TrendReportProgressCard(report_id=report_id).model_dump(mode="json")
        self._say(thread, request.request_text, f"의뢰 {report_label(report_id)} 만드는 중…", card)

    def chat_done(self, report: TrendReport) -> None:
        targets = list(self._watchers.pop(report.report_id, []))
        thread = self.store.chat_thread(report.report_id)
        if thread:
            row = self.store.get(report.report_id) or {}
            targets.insert(0, (thread, (row.get("input") or {}).get("request_text")))
        for thread_id, text in dict.fromkeys(targets):
            self._say(thread_id, text, done_text(report), done_card(report))

    # ---- 총괄 긴 작업 -------------------------------------------------------------

    def add_waiter(self, report_id: str, request: AgentRequest, task_id: str,
                   now: datetime) -> None:  # fmt: skip
        self.store.add_waiter(report_id, task_id, request.meta.tenant_id, request.meta.context_id,
                              request.skill, now)  # fmt: skip
        self._requests[task_id] = request

    def has_waiters(self, report_id: str) -> bool:
        return bool(self.store.waiters(report_id))

    async def _send(self, envelope: EventEnvelope) -> None:
        publish = self._publish
        if publish is None:
            from ax_agent_template.events import publish as default_publish

            publish = default_publish
        got = publish(envelope)
        if asyncio.iscoroutine(got):
            await got

    async def progress(self, report_id: str, *, percent: int | None = None,
                       note: str | None = None) -> None:  # fmt: skip
        """기다리는 쪽 모두에게 진행 알림. 못 보내도 보고서는 그대로(로그만)."""
        from ax_agent_template import async_task

        for w in self.store.waiters(report_id):
            try:
                request = self._requests.get(w["task_id"])
                if request is not None:
                    await async_task.report_progress(request, SOURCE, w["task_id"],
                                                     percent=percent, note=note)  # fmt: skip
                    continue
                data = TaskProgressData(task_id=w["task_id"], skill=w["skill"], percent=percent,
                                        note=note)  # fmt: skip
                key = f"task.progress:{w['task_id']}:{uuid.uuid4().hex[:8]}"
                await self._send(_envelope(w["tenant_id"], w["context_id"],
                                           EventType.TASK_PROGRESS,
                                           data.model_dump(mode="json"), key))  # fmt: skip
            except Exception as error:  # noqa: BLE001 - EventBusUnavailable 등
                log.warning("trend_report %s 진행 알림 못 보냄 (%s)", report_id,
                            type(error).__name__)  # fmt: skip

    async def completed(self, report: TrendReport) -> None:
        """기다리는 쪽 모두에게 완료 알림 한 번. 보냈으면 표에 적는다."""
        from ax_agent_template import async_task

        if report.status not in ENDED:
            return
        failed = report.status not in ("done", "partial")
        output = None if failed else completed_output(report)
        note = failure_reason(report)[:NOTE_MAX] if failed else None
        for w in self.store.waiters(report.report_id):
            task_id = w["task_id"]
            try:
                request = self._requests.pop(task_id, None)
                if request is not None:
                    await async_task.report_completed(request, SOURCE, task_id, output=output,
                                                      failed=failed, note=note)  # fmt: skip
                else:
                    data = TaskCompletedData(task_id=task_id, skill=w["skill"],
                                             status="failed" if failed else "done",
                                             output=output or {}, note=note)  # fmt: skip
                    await self._send(_envelope(w["tenant_id"], w["context_id"],
                                               EventType.TASK_COMPLETED,
                                               data.model_dump(mode="json"),
                                               f"task.completed:{task_id}"))  # fmt: skip
            except Exception as error:  # noqa: BLE001 - 못 보내면 다음 켜질 때 다시
                log.warning("trend_report %s 완료 알림 못 보냄 (%s)", report.report_id,
                            type(error).__name__)  # fmt: skip
                continue
            self.store.mark_completed_sent(task_id)

    async def resume(self, get: Callable[[str], TrendReport | None]) -> None:
        """다시 켜진 뒤: 끝났는데(중단 포함) 완료를 못 보낸 기다리는 쪽에 완료를 보낸다."""
        for report_id in dict.fromkeys(w["report_id"] for w in self.store.waiters()):
            report = get(report_id)
            if report is not None and report.status in ENDED:
                await self.completed(report)
