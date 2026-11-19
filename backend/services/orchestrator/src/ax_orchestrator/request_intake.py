"""의뢰 접수: 총괄 대화로 온 글을 분류해 의뢰면 기록하고 고객에게 접수 안내를 돌려준다.

대화 글 하나가 오면(`receive`):
1. 분류기(`request_classifier`)가 질문인지 의뢰인지 가른다(LLM 한 번).
2. 질문이거나 분류하지 못했으면(`handled=False`) 아무것도 기록하지 않고 기존 설명 대화로 보낸다.
3. 의뢰면 중복(같은 사람이 같은 글을 최근에 보냄)과 하루 한도를 확인하고 의뢰 기록을 만든다.
   - 일을 정했으면 일 목록의 계획을 기록에 붙인다(`classified`). 실제로 부서에 시키는 것은 이후
     단계(2단계)이고, 지금은 접수까지만 한다. 안내 문장이 그렇게 말한다.
   - 일을 못 정했으면 되묻는다(`received`로 둠). 목록에 없으면 담당 불명으로 사람에게
     (`needs_human`), 위험하면 거절한다(`rejected`).
고객에게 가는 안내는 기록한 값(번호·일 이름표·부서 이름)과 정해진 문장 틀로만 만든다. 부서가 돌려준
자유 문장은 쓰지 않는다. 의뢰자는 대화 계정(`account`)이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ax_orchestrator import request_tasks
from ax_orchestrator.models import now
from ax_orchestrator.request_classifier import PROMPT_VERSION, Decision, classify
from ax_orchestrator.work_requests import OPEN_STATES, WorkRequests

DEDUPE_MINUTES = 10
DAILY_LIMIT = 20  # 의뢰자 한 명이 하루(서울 시간)에 접수할 수 있는 의뢰 수
KST = timedelta(hours=9)
UNKNOWN_ACCOUNT = "unknown"


@dataclass
class IntakeResult:
    """`handled`가 False면 의뢰가 아니라서(또는 분류하지 못해서) 기존 대화로 보낸다."""

    handled: bool
    outcome: str
    reply: str = ""
    summary: str | None = None
    request_id: str | None = None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _day(value: str) -> str:
    return (datetime.fromisoformat(value) + KST).date().isoformat()


class RequestIntake:
    def __init__(self, requests: WorkRequests, *, daily_limit: int = DAILY_LIMIT) -> None:
        self._requests = requests
        self._daily_limit = daily_limit

    def _duplicate(self, account: str, text: str) -> dict[str, Any] | None:
        """같은 사람이 같은 글을 최근에 이미 접수했나(거절된 의뢰는 다시 보낼 수 있다)."""
        since = now() - timedelta(minutes=DEDUPE_MINUTES)
        key = _norm(text)
        for row in self._requests.list(account):
            if (
                row["status"] != "rejected"
                and _norm(row["text"]) == key
                and datetime.fromisoformat(row["created_at"]) >= since
            ):
                return row
        return None

    def _today(self, account: str) -> int:
        today = _day(now().isoformat())
        return sum(1 for r in self._requests.list(account) if _day(r["created_at"]) == today)

    async def receive(
        self,
        who: str | None,
        text: str,
        llm: Any,
        *,
        history: list[str] | None = None,
    ) -> IntakeResult:
        account = who or UNKNOWN_ACCOUNT
        dup = self._duplicate(account, text)
        if dup is not None:  # 같은 글이면 LLM을 다시 부르지 않는다
            return IntakeResult(
                True,
                "duplicate",
                f"같은 의뢰가 이미 접수돼 있어요. (번호 {dup['request_id']})",
                request_id=dup["request_id"],
            )
        decision = await classify(llm, text, history=history)
        if decision.outcome in ("question", "unavailable"):
            return IntakeResult(False, decision.outcome)
        if self._today(account) >= self._daily_limit:
            return IntakeResult(
                True, "limited", "오늘 접수할 수 있는 의뢰 수를 넘었어요. 내일 다시 보내 주세요."
            )
        return self._record(account, text, decision)

    def _record(self, account: str, text: str, d: Decision) -> IntakeResult:
        row = self._requests.create(account, text)
        rid = row["request_id"]
        self._requests.set_classification(
            rid,
            prompt_version=PROMPT_VERSION,
            kind=d.kind,
            outcome=d.outcome,
            task=d.task.name if d.task else None,
            confidence=d.confidence,
            reason=d.reason,
            alternatives=d.alternatives,
        )
        if d.outcome == "task" and d.task is not None:
            self._requests.set_plan(
                rid,
                task=d.task.name,
                confidence=d.confidence,
                reason=d.reason,
                alternatives=d.alternatives,
                plan=request_tasks.plan_of(d.task),
            )
            return IntakeResult(
                True, "task", self._task_reply(rid, d.task), f"의뢰 접수: {d.task.label}", rid
            )
        if d.outcome == "clarify":
            self._requests.set_status(rid, "received", "일을 정하지 못해 되물었다")
            return IntakeResult(True, "clarify", d.clarify or "", "의뢰 확인 중", rid)
        if d.outcome == "rejected":
            self._requests.set_status(rid, "rejected", "위험하거나 권한 밖이라 거절했다")
            return IntakeResult(
                True,
                "rejected",
                f"이 요청은 처리할 수 없어요. (번호 {rid})",
                "의뢰 거절",
                rid,
            )
        # unknown: 목록에 맞는 일이 없다 → 담당 불명, 사람에게
        self._requests.set_status(rid, "needs_human", "맡을 부서를 정하지 못했다 (담당 불명)")
        return IntakeResult(
            True,
            "unknown",
            f"어느 부서가 맡을지 정하지 못했어요. 사람이 확인해서 알려 드릴게요. (번호 {rid})",
            "의뢰 담당 불명",
            rid,
        )

    @staticmethod
    def _task_reply(rid: str, task: request_tasks.TaskDef) -> str:
        first = request_tasks.dept_label(task.first_dept)
        reply = (
            f"의뢰를 접수했어요. (번호 {rid}) '{task.label}' 일로 분류했고 "
            f"{first}부터 시작해요. 부서로 넘기는 기능은 아직 준비 중이라 지금은 접수까지만 돼요."
        )
        if task.changes_state:
            reply += " 상태를 바꾸는 일이라 승인을 거친 뒤에 진행돼요."
        return reply

    def open_for(self, account: str) -> list[dict[str, Any]]:
        return [r for r in self._requests.list(account) if r["status"] in OPEN_STATES]
