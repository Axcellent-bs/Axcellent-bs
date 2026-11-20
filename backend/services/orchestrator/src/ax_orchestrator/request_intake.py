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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ax_orchestrator import request_inputs, request_tasks
from ax_orchestrator.models import now
from ax_orchestrator.request_classifier import PROMPT_VERSION, Decision, classify
from ax_orchestrator.work_requests import OPEN_STATES, WorkRequests

DEDUPE_MINUTES = 10
CLARIFY_MINUTES = 30  # 대화 번호가 없을 때 "이미 되물었다"를 보는 시간
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
    def __init__(
        self,
        requests: WorkRequests,
        *,
        daily_limit: int = DAILY_LIMIT,
        clock: Callable[[], datetime] = now,
    ) -> None:
        self._requests = requests
        self._clock = clock
        self._daily_limit = daily_limit
        # 일을 정해 기록하면 번호를 넘겨 실행을 시작한다(`RequestRunner.start`). None이면 접수만
        self.on_task: Callable[[str], None] | None = None
        # 사람에게 넘길 의뢰를 받은 요청에 올린다: (의뢰 번호, 이유). None이면 기록만 한다
        self.on_escalate: Callable[[str, str], None] | None = None

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

    def _clarified_before(self, account: str, thread_id: str | None, own_id: str) -> bool:
        """같은 대화(없으면 같은 사람의 최근 30분)에서 이미 되물었나. 되묻기는 한 번만 한다."""
        since = now() - timedelta(minutes=CLARIFY_MINUTES)
        for row in self._requests.list(account):
            if row["status"] != "received" or row["request_id"] == own_id:
                continue
            if thread_id:
                same = row.get("thread_id") == thread_id
            else:
                same = datetime.fromisoformat(row["created_at"]) >= since
            if same:
                return True
        return False

    def _close_clarifications(self, account: str, thread_id: str | None, new_id: str) -> None:
        """되물은 뒤 고객이 답해 일이 정해지면, 되묻고 기다리던 이전 기록을 이어졌다고 닫는다."""
        for row in self._requests.list(account):
            if (
                row["status"] == "received"
                and row["request_id"] != new_id
                and (not thread_id or row.get("thread_id") == thread_id)
            ):
                self._requests.set_status(
                    row["request_id"], "done", f"되묻기에 답해 새 의뢰({new_id})로 이어졌다"
                )

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
        thread_id: str | None = None,
        buyer_id: str | None = None,
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
        return self._record(account, text, decision, thread_id, buyer_id)

    def _record(
        self,
        account: str,
        text: str,
        d: Decision,
        thread_id: str | None = None,
        buyer_id: str | None = None,
    ) -> IntakeResult:
        row = self._requests.create(account, text)
        rid = row["request_id"]
        if thread_id:
            self._requests.link_thread(rid, thread_id)
        if buyer_id:
            self._requests.set_buyer(rid, buyer_id)
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
            ctx = {"name": account, "buyer_id": buyer_id}
            ask, notes, handoff = request_inputs.check(d.task, text, self._clock(), ctx)
            if (
                handoff is not None
            ):  # 고객이 고칠 수 없는 이유(구매자 연결 없음): 되묻지 않고 사람에게
                return self._no_link(rid, handoff)
            if d.task.note and ask is None:
                notes = [*notes, d.task.note]
            if (
                ask is not None
            ):  # 부서가 요구하는 입력을 문장에서 못 찾았다: 부서에 보내지 않고 묻는다
                return self._ask_input(rid, account, thread_id, ask)
            self._close_clarifications(account, thread_id, rid)
            self._requests.set_plan(
                rid,
                task=d.task.name,
                confidence=d.confidence,
                reason=d.reason,
                alternatives=d.alternatives,
                plan=request_tasks.plan_of(d.task),
            )
            start = self.on_task
            started = start is not None and not d.task.blocked_by
            if started and start is not None:
                start(rid)
            return IntakeResult(
                True,
                "task",
                self._task_reply(rid, d.task, started, notes),
                f"의뢰 접수: {d.task.label}",
                rid,
            )
        if d.outcome == "clarify":
            if self._clarified_before(
                account, thread_id, rid
            ):  # 두 번째부터는 되묻지 않고 사람에게
                self._requests.set_status(
                    rid, "needs_human", "되물었는데도 일을 정하지 못해 사람에게 넘겼다"
                )
                self._escalate(rid, "unclear")
                return IntakeResult(
                    True,
                    "unclear",
                    f"내용을 정하지 못했어요. 담당자가 확인해서 알려 드릴게요. (번호 {rid})",
                    "의뢰 확인 필요",
                    rid,
                )
            self._requests.set_status(rid, "received", "일을 정하지 못해 되물었다")
            return IntakeResult(True, "clarify", d.clarify or "", "의뢰 확인 중", rid)
        if d.outcome == "rejected":
            self._requests.set_status(
                rid,
                "rejected",
                f"위험하거나 권한 밖이라 거절했다 (분류 이유: {d.reason or '없음'})",
            )
            return IntakeResult(
                True,
                "rejected",
                f"이 요청은 처리할 수 없어요. (번호 {rid})",
                "의뢰 거절",
                rid,
            )
        # unknown: 목록에 맞는 일이 없다 → 담당 불명, 사람에게
        self._requests.set_status(rid, "needs_human", "맡을 부서를 정하지 못했다 (담당 불명)")
        self._escalate(rid, "unknown")
        return IntakeResult(
            True,
            "unknown",
            f"어느 부서가 맡을지 정하지 못했어요. 사람이 확인해서 알려 드릴게요. (번호 {rid})",
            "의뢰 담당 불명",
            rid,
        )

    def _no_link(self, rid: str, message: str) -> IntakeResult:
        """계정에 구매자 연결이 없어 접수할 수 없다: 부서에 보내지 않고 사람 받은 요청에 올린다."""
        self._requests.set_status(rid, "needs_human", "계정에 구매자 연결이 없어 사람에게 넘겼다")
        self._escalate(rid, "no_link")
        return IntakeResult(True, "no_link", f"{message} (번호 {rid})", "의뢰 확인 필요", rid)

    def _ask_input(self, rid: str, account: str, thread_id: str | None, ask: str) -> IntakeResult:
        """일은 정했는데 필요한 입력(기간 등)이 문장에 없다: 한 번만 되묻고, 또 없으면 사람에게."""
        if self._clarified_before(account, thread_id, rid):
            self._requests.set_status(
                rid, "needs_human", "필요한 입력을 되물었는데도 알 수 없어 사람에게 넘겼다"
            )
            self._escalate(rid, "unclear")
            return IntakeResult(
                True,
                "unclear",
                f"내용을 정하지 못했어요. 담당자가 확인해서 알려 드릴게요. (번호 {rid})",
                "의뢰 확인 필요",
                rid,
            )
        self._requests.set_status(rid, "received", "필요한 입력(기간 등)이 없어 되물었다")
        return IntakeResult(True, "clarify", ask, "의뢰 확인 중", rid)

    def _escalate(self, rid: str, why: str) -> None:
        if self.on_escalate is not None:
            self.on_escalate(rid, why)

    @staticmethod
    def _task_reply(
        rid: str, task: request_tasks.TaskDef, started: bool, notes: list[str] | None = None
    ) -> str:
        first = request_tasks.dept_label(task.first_dept)
        head = f"의뢰를 접수했어요. (번호 {rid}) '{task.label}' 일로 분류했고 "
        if started:
            reply = head + f"{first}에 시켰어요."
        else:  # 아직 자동으로 돌릴 수 없는 일이거나 실행기가 없다: 접수만 하고 담당자가 확인한다
            reply = head + f"{first}부터 시작할 일이에요. 지금은 접수까지만 되고 담당자가 확인해요."
        if notes:
            reply += f" ({', '.join(notes)})"
        if task.needs_human_approval:
            reply += " 사람 승인이 필요한 일이라 승인을 거친 뒤에 진행돼요."
        elif task.changes_state:
            reply += " 상태를 바꾸는 일이라 안전 확인을 거쳐 진행돼요."
        return reply

    def open_for(self, account: str) -> list[dict[str, Any]]:
        return [r for r in self._requests.list(account) if r["status"] in OPEN_STATES]
