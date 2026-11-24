"""기획·개발 서비스가 같이 쓰는 서비스 뼈대 (분리 5단계: `planning_agent.domain.service`에서 옮김).

- `PlanningCore`: 기획(`PlanningService`)·개발(`DevService`) 서비스의 공용 부분. 두 서비스 사이
  넘기기(송신·재시도·다시 맞추기), 다음 단계 반복 틀, 공용 조회, 액션 dispatch(서비스별 `ACTIONS`).
- 액션 표(`ACTIONS`)는 화면이 보내는 프로토타입 Action 이름과 입력 모델이다. 서비스마다
  `PLANNING_ACTIONS`·`DEV_ACTIONS`만 받는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_agent_template import config as template_config
from ax_agent_template import routing
from ax_agent_template.services import route_target
from ax_contracts.schemas import TENANT_ID_DEFAULT, ExecuteDecision
from ax_contracts.schemas.planning import (
    HANDOFF_KNOWN_MAX_KEYS,
    ChangeResultInput,
    DevHandoffInput,
    DevResultInput,
)
from ax_workflow import cause_inbox, related
from ax_workflow.autocheck import (
    Check,
)
from ax_workflow.clock import Clock
from ax_workflow.core import (
    ActionError,
    ActionResult,
    Gate,
    GateRefused,
    StateService,
)
from ax_workflow.dev_handoff import (
    HANDED,
    PERMANENT,
    RECEIVE_DEV_RESULT,
    SENT,
    START_DEV,
    DevSender,
    KnownAsker,
    ask_known,
    classify,
    fingerprint,
    permanent_hint,
    send_to_service,
)
from ax_workflow.models import (
    PR,
    AutoReview,
    HandoffFailure,
    OutMsg,
    Request,
    Spec,
    State,
)
from ax_workflow.result_notify import ResultSender, send_change_result
from ax_workflow.rules import effective_level
from ax_workflow.seed import initial_state, real_state

AGENT_NAME = "planning"


TEAM_LEAD = "노예진"  # 확정·머지·승급 승인 버튼을 누르는 팀장 (목)


INBOUND_KINDS = (
    "support_ticket",
    "marketing_ticket",
    "hr_draft",
    "data_draft",
    "data_query",
    "infra_query",
    "orch_incident",
    "safety_verify",
    "support_simple",
    "marketing_simple",
)


ADVANCE_LIMIT = 40  # 다음 단계 반복 상한 (무한 반복 방지)


_TICKET_KINDS = ("support_ticket", "marketing_ticket", "support_simple", "marketing_simple")


# ---------------------------------------------------------------- 관문·결과 타입


# 관문이 보류해도 본 작업을 되돌리지 않는 부수 기록. 인사 기록은 인사가 실행하는 일이라
# 기획·개발 쪽 관문에서는 보류될 수 있다 (등록표 owner hr)
SOFT_ACTIONS = frozenset({"record_case"})


# ---- 액션 입력 (프로토타입 Action과 같은 모양)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InboundParams(_Params):
    kind: Literal[INBOUND_KINDS]  # type: ignore[valid-type]


class SendParams(_Params):
    to: str
    point: str
    text: str
    ctx: str = "ctx_req_0012"


class ToParams(_Params):
    to: str


class SpecParams(_Params):
    spec: str


class AnswerParams(_Params):
    spec: str
    text: str


class PrParams(_Params):
    pr: str | int


class RepoParams(_Params):
    """올릴 레포 승인. connection을 안 주면 제안된 연결로."""

    pr: str | int
    connection: str | None = None


class RequestParams(_Params):
    request: str


class RejectDraftParams(_Params):
    """개선 초안 기각. 이유·검토한 대안이 비면 hr에 보내지 않고 `MISSING_DECISION_REASON`."""

    request: str
    decision_reason: str = ""
    alternatives_considered: list[str] = Field(default_factory=list)


class RejectDecisionParams(_Params):
    """기각한 안을 결정으로 남긴다 (예: 기획서 검토 중 버린 안)."""

    spec: str
    text: str
    decision_reason: str = ""
    alternatives_considered: list[str] = Field(default_factory=list)


class EmptyParams(_Params):
    pass


class PostMessageParams(_Params):
    thread: str
    text: str


class DeptChatParams(_Params):
    from_: str = Field(alias="from")


class ExtractDecisionParams(_Params):
    thread: str
    index: int


class NewDocParams(_Params):
    title: str = ""


class GatherDocParams(_Params):
    about: str
    by: Literal["ai", "human"] | None = None


class RenameDocParams(_Params):
    doc: str
    title: str


class AddBlockParams(_Params):
    doc: str
    kind: Literal["h2", "p", "todo", "ref"]
    text: str | None = None


class EditBlockParams(_Params):
    doc: str
    block: str
    text: str


class BlockParams(_Params):
    doc: str
    block: str


class ResendParams(_Params):
    """받는 쪽이 거절해 멈춘 넘기기를 다시 보낸다. key를 안 주면 멈춘 것 모두.

    `all`이면 받는 쪽이 받았다고 한 기록(acked)도 무시하고 넘긴 것을 모두 다시 보낸다
    (받는 쪽이 상태를 잃었을 때. 같은 멱등 키라 받는 쪽이 가진 건 그대로)."""

    key: str | None = None
    everything: bool = Field(False, alias="all")


# snake_case 액션 이름 → (메서드, 입력 모델). 화면이 보내는 camelCase(`confirmSpec`)도 받는다.
ACTIONS: dict[str, tuple[str, type[_Params]]] = {
    "inbound": ("_inbound", InboundParams),
    "send": ("_send", SendParams),
    "mark_read": ("_mark_read", ToParams),
    "confirm_spec": ("_confirm_spec", SpecParams),
    "check_spec": ("_check_spec", SpecParams),
    "answer_question": ("_answer_question", AnswerParams),
    "generate_prs": ("_generate_prs", SpecParams),
    "review_pr": ("_review_pr", PrParams),
    "merge_pr": ("_merge_pr", PrParams),
    "approve_repo": ("_approve_repo", RepoParams),
    "reject_repo": ("_reject_repo", PrParams),
    "accept_draft": ("_accept_draft", RequestParams),
    "write_spec": ("_write_spec", RequestParams),
    "reject_draft": ("_reject_draft", RejectDraftParams),
    "reject_decision": ("_reject_decision", RejectDecisionParams),
    "approve_promotion": ("_approve_promotion", EmptyParams),
    "reject_promotion": ("_reject_promotion", EmptyParams),
    "post_message": ("_post_message", PostMessageParams),
    "dept_chat": ("_dept_chat", DeptChatParams),
    "extract_decision": ("_extract_decision", ExtractDecisionParams),
    "new_doc": ("_new_doc", NewDocParams),
    "gather_doc": ("_gather_doc", GatherDocParams),
    "rename_doc": ("_rename_doc", RenameDocParams),
    "add_block": ("_add_block", AddBlockParams),
    "edit_block": ("_edit_block", EditBlockParams),
    "toggle_todo": ("_toggle_todo", BlockParams),
    "remove_block": ("_remove_block", BlockParams),
    "resend_handoff": ("_resend_handoff", ResendParams),
}


# 서비스마다 받는 액션 (분리 4단계). 시뮬레이터·소통 창구(inbound·send·mark_read)는 둘 다 받는다
BOTH_ACTIONS = ("inbound", "send", "mark_read", "resend_handoff")


DEV_ONLY_ACTIONS = (
    "generate_prs",
    "review_pr",
    "merge_pr",
    "approve_repo",
    "reject_repo",
    "approve_promotion",
    "reject_promotion",
)


DEV_ACTIONS = {k: ACTIONS[k] for k in (*BOTH_ACTIONS, *DEV_ONLY_ACTIONS)}


PLANNING_ACTIONS = {k: v for k, v in ACTIONS.items() if k not in DEV_ONLY_ACTIONS}


SIMULATOR_ACTIONS = {"inbound", "dept_chat"}


def action_name(raw: str) -> str:
    """`confirmSpec` → `confirm_spec`. 이미 snake_case면 그대로."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", raw).lower()


# ---------------------------------------------------------------- 서비스


# 다른 부서(인사)가 자기 관문으로 실행하는 기록. 실제 모드에서는 기획 관문에 묻지 않는다
HR_EXECUTED_ACTIONS = frozenset({"record_case"})


class PlanningCore(StateService):
    """기획·개발 서비스가 같이 쓰는 뼈대 (분리 4단계). 역할마다 `PlanningService`·`DevService`.

    상태·구독·적용 틀·실행 관문은 `ax_workflow.core.StateService` (분리 1단계). 여기에는 두
    서비스 사이 넘기기(송신·재시도·다시 맞추기)·다음 단계 반복 틀·공용 조회가 있다."""

    agent_name = AGENT_NAME
    soft_actions = SOFT_ACTIONS
    ROLE = ""  # 서비스 역할 (상태 파일 이름·요청 색인 owner·넘기기 방향)
    ACTIONS: dict[str, tuple[str, type[_Params]]] = {}

    def __init__(
        self,
        clock: Clock | None = None,
        gate: Gate | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        result_sender: ResultSender | None = None,
        dev_sender: DevSender | None = None,
        real_example: bool = False,
        relater: related.Relater | None | Literal["default"] = "default",
    ):
        super().__init__(clock, gate, tenant_id, self.ROLE, real_example)
        self._send_dev: DevSender = dev_sender or send_to_service
        # 다시 켰을 때 맞추기가 받는 쪽에 받았는지 묻는 곳 (시험에서 바꿔 끼운다)
        self._ask_known: KnownAsker = ask_known
        self._sending: set[asyncio.Task] = set()
        self._retrying = False  # 못 보낸 넘기기를 다시 보내는 중
        self._retry_task: asyncio.Task | None = None
        # 못 보낸 넘기기마다 다음에 다시 보낼 시각 (time.monotonic). 다시 켜면 비어 맞추기가 보낸다
        self._due: dict[str, float] = {}
        # 사람이 "다시 보내기"를 고른 넘기기 (skill, ctx, 입력, 멱등 키). _settle이 꺼내 보낸다
        self._resend_pending: list[tuple[str, str, BaseModel, str]] = []
        self._send_result: ResultSender = result_sender or send_change_result
        # 원인 확인 요청을 받은 요청에 올리는 곳 (시험에서 바꿔 끼운다, None이면 실제 할 일함)
        self._cause_poster: cause_inbox.Poster | None = None
        # 새 요청이 오면 비슷한·충돌하는 요청을 찾는 곳 (None이면 찾지 않는다)
        self._relater: related.Relater | None = (
            related.default_relater(self.role) if relater == "default" else relater
        )
        # 인사 record_case를 실제로 부르는 곳 (기획 서비스가 채운다. 관문 덮어쓰기가 본다)
        self._hr: Any = None  # HrClient (planning_agent.hr_link)

    # ---- 서비스마다 채우는 자리 (기본은 아무것도 안 함)

    async def _relate_new(self, s: State, known: set[str]) -> None:
        return None

    def _relate_paths(self, s: State, known_prs: set[str]) -> None:
        return None

    def _spawn_hold(self, task_id: str) -> None:
        return None

    def _kick_waiting(self) -> None:
        return None

    def _dev_outgoing(self, s: State, full: bool = False) -> list[tuple[str, str, BaseModel, str]]:
        return []

    async def tick(self) -> None:
        """화면이 열려 있는 동안 주기적으로 부른다 (SSE keepalive 때). 서비스마다 채운다."""
        return None

    async def _advance_step(self, s: State) -> bool:
        return False

    def _inbound_start(self, s: State) -> tuple[int, datetime, str, PR | None]:
        """시뮬레이터 한 건의 순번·시각·요청 ctx·마지막 머지 PR (순번은 올린다)."""
        seq = s.seq + 1
        merged = [p for p in s.prs if p.merged]
        s.seq = seq
        return seq, self.clock(), f"ctx_req_{20 + seq:04d}", merged[-1] if merged else None

    @staticmethod
    def _wrong_service(kind: str, other: str) -> ActionError:
        return ActionError("WRONG_SERVICE", f"{kind} 시뮬레이터는 {other} 서비스가 받는다")

    # ---- 상태·구독

    def _seed(self) -> State:
        if self.company:  # 다른 회사는 빈 상태로 시작한다 (캣북스 예시를 섞지 않는다)
            return State(auto_policy=initial_state().auto_policy, tenant_id=self.company)
        return real_state() if self.real_example else initial_state()

    # ---- 적용 틀

    def _mark(self, s: State) -> dict[str, Any]:
        return {
            "before": len(s.activity),
            "held": {t.id for t in s.tasks if t.coding and t.coding.status == "waiting_human"},
            "known": {r.id for r in s.requests},
            "known_prs": {p.id for p in s.prs},
        }

    async def _apply(self, fn: Any, **params: Any) -> Any:
        try:
            return await super()._apply(fn, **params)
        except BaseException:
            self._resend_pending = []  # 실패한 액션이 줄 세운 다시 보내기는 버린다
            raise

    async def _settle(self, s: State, mark: dict[str, Any]) -> None:
        # 사람이 고른 다시 보내기는 이번 액션 몫이다. 바로 꺼내 둬서 뒤에서 실패해도 새지 않는다
        resend, self._resend_pending = self._resend_pending, []
        await self._relate_new(s, mark["known"])
        self._relate_paths(s, mark["known_prs"])
        await self._advance(s)
        mark["outgoing"] = self._dev_outgoing(s) + resend
        self._to_outbox(s, mark["before"])

    def _after(self, s: State, mark: dict[str, Any], changed: bool) -> None:
        # 넘기기는 상태를 바꿔 끼운 뒤 잠금 밖에서 보낸다 (잠금을 쥔 채 네트워크를 부르지 않는다)
        for item in mark["outgoing"]:
            self._spawn_send(*item)
        if changed:  # 사람 확인으로 새로 멈춘 코딩 작업 → 받은 요청에 개발팀 할 일
            for t in s.tasks:
                cr = t.coding
                if cr and cr.status == "waiting_human" and t.id not in mark["held"]:
                    self._spawn_hold(t.id)
        self._kick_waiting()

    def _spawn_send(self, skill: str, ctx: str, payload: BaseModel, key: str) -> None:
        task = asyncio.create_task(self._send_one(skill, ctx, payload, key))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    # ---- 넘기기 기록 (상태 파일 옆 `<상태 파일>.handoff.json`)
    #
    # 예전 빌드가 이 빌드의 상태 파일을 그대로 읽도록 `State`와 `_unsent` 모양은 그대로 둔다.
    # 다시 보낸 횟수와 자동으로 다시 보내지 않는 넘기기(거절·할 일함)는 옆 파일에 둔다.

    def _init_state(self, fresh: bool = False) -> None:
        self._attempt_counts: dict[str, int] = {}
        self._handoff_failed: list[HandoffFailure] = []
        # 받는 쪽이 받았다고 답한 넘기기 {멱등 키: 입력 지문}. 다시 켰을 때 맞추기가 같은 입력을
        # 다시 보내지 않는다 (11/23, 켤 때마다 "start dev 부탁해요"가 몇 줄씩 쌓이던 문제)
        self._acked: dict[str, str] = {}
        super()._init_state(fresh)
        if not fresh:
            self._load_handoff()

    def _handoff_path(self) -> str | None:
        return f"{self._state_path}.handoff.json" if self._state_path else None

    def _load_handoff(self) -> None:
        """옆 파일을 읽는다. 없거나 깨졌으면 빈 기록, 못 읽는 줄은 건너뛴다."""
        path = self._handoff_path()
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        counts = data.get("attempts")
        if isinstance(counts, dict):
            for k, v in counts.items():
                if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                    self._attempt_counts[str(k)] = v
        rows = data.get("failed")
        for row in rows if isinstance(rows, list) else []:
            try:
                self._handoff_failed.append(HandoffFailure.model_validate(row))
            except ValidationError:
                continue
        acked = data.get("acked")  # 예전 옆 파일에는 없다
        if isinstance(acked, dict):
            for k, v in acked.items():
                if isinstance(v, str) and v:
                    self._acked[str(k)] = v

    def _save(self) -> None:
        super()._save()
        self._save_handoff()

    def _save_handoff(self) -> None:
        """옆 파일만 쓴다. 모르는 키는 읽을 때 건너뛰어 예전·다음 빌드가 서로 읽는다."""
        path = self._handoff_path()
        if not path:
            return
        data = {
            "attempts": {k: v for k, v in self._attempt_counts.items() if k in self._unsent},
            "failed": [f.model_dump(mode="json") for f in self._handoff_failed],
            "acked": self._acked,
        }
        try:
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass

    def public_snapshot(self) -> dict:
        """화면 상태에 자동으로 다시 보내지 않는 넘기기(`handoff_failed`, 읽기 전용)를 덧붙인다."""
        data = super().public_snapshot()
        data["handoff_failed"] = [
            f.model_dump(mode="json", exclude={"payload"}) for f in self._handoff_failed
        ]
        return data

    def _forget(self, key: str) -> None:
        self._due.pop(key, None)
        self._unsent.pop(key, None)
        self._attempt_counts.pop(key, None)

    async def _hold_back(
        self, kind: str, skill: str, ctx: str, body: dict, key: str, reason: str, text: str
    ) -> None:
        """자동으로 다시 보내지 않는 넘기기로 남기고 활동에 한 줄 (같은 기록이면 다시 안 남김)."""
        self._forget(key)
        fp = fingerprint(body)
        known = next((f for f in self._handoff_failed if f.key == key), None)
        if known and (known.kind, known.fingerprint, known.reason) == (kind, fp, reason):
            self._save()
            return
        fail = HandoffFailure(
            kind=kind, key=key, skill=skill, context_id=ctx, reason=reason, at=self.clock(),
            fingerprint=fp, payload=body,
        )  # fmt: skip
        self._handoff_failed = [f for f in self._handoff_failed if f.key != key] + [fail]
        frm, to = ("planning", "dev") if self.role == "planning" else ("dev", "planning")

        async def record(s: State) -> None:
            s.activity.append(self._act(self.clock(), ctx, frm, to, skill, "A2A", text, "A"))

        await self._apply(record)  # 활동을 바꿔 끼우며 옆 파일도 저장하고 화면에 알린다

    async def _send_one(self, skill: str, ctx: str, payload: BaseModel, key: str) -> None:
        """한 건 보낸다. 실패해도 상태는 그대로 두고 활동에 한 줄 남긴다.

        - 보냄: 못 보낸 기록·거절 기록을 지운다.
        - 받는 쪽이 거절(`classify` PERMANENT): 다시 보내지 않는다. 옆 파일 기록(refused)·활동 한 줄
          (사람이 받는 쪽을 고친 뒤 `resend_handoff`로 다시 보낸다).
        - 사람 할 일함으로 넘어감(HANDED): 거절이 아니다. 기록(handed)과 활동 한 줄, 주기적으로
          다시 보내지 않고 다시 켰을 때 맞추기가 다시 보낸다.
        - 닿지 못함(TRANSIENT): `_unsent`에 두고 30초·60초·120초… 최대 15분 간격으로 다시 보낸다.
          횟수는 옆 파일에 남는다. 활동에는 처음 실패만 남긴다."""
        try:
            how = await self._send_dev(skill, ctx, self.tenant_id, payload, key)
        except Exception:  # 넘기기 실패가 이미 바꾼 상태를 되돌리지 않게 한다
            how = "보내지 못함 (오류)"
        kind = classify(how)
        body = payload.model_dump(mode="json")
        if kind == SENT:
            was = key in self._unsent or any(f.key == key for f in self._handoff_failed)
            self._forget(key)
            self._handoff_failed = [f for f in self._handoff_failed if f.key != key]
            self._ack(key, fingerprint(body))
            if was:
                self._save()
                self.version += 1
                self._notify()
            else:
                self._save_handoff()
            return
        self._acked.pop(key, None)  # 받았다는 기록은 이번 결과로 바꾼다
        if kind == PERMANENT:
            hint = permanent_hint(how)
            note = f" ({hint})" if hint else ""
            text = f"{skill} {how}{note} → 받는 쪽이 거절해 다시 보내지 않음 (고친 뒤 다시 보내기)"
            await self._hold_back("refused", skill, ctx, body, key, str(how), text)
            return
        if kind == HANDED:
            text = (
                f"{skill} {how} → 사람 할 일함으로 넘어감 (받는 쪽을 다시 켜면 맞추기가 다시 보냄)"
            )
            await self._hold_back("handed", skill, ctx, body, key, str(how), text)
            return
        first = key not in self._unsent
        attempts = 1 if first else self._attempts(key) + 1
        self._unsent[key] = [skill, ctx, body]  # 예전 모양 그대로 (횟수는 옆 파일)
        self._attempt_counts[key] = attempts
        self._due[key] = _monotonic() + retry_delay(attempts)
        self._save()
        self._schedule_retry()
        if not first:
            return  # 다시 보내기 실패는 활동에 거듭 남기지 않는다
        frm, to = ("planning", "dev") if self.role == "planning" else ("dev", "planning")
        cap = RETRY_MAX_SECONDS // 60
        text = f"{skill} {how} → {RETRY_SECONDS}초부터 간격을 늘려 다시 보냄 (최대 {cap}분)"

        async def log(s: State) -> None:
            s.activity.append(self._act(self.clock(), ctx, frm, to, skill, "A2A", text, "A"))

        await self._apply(log)

    def _attempts(self, key: str) -> int:
        """못 보낸 넘기기를 보낸 횟수. 옆 파일에 없으면(예전 빌드가 남긴 것) 1번으로 본다."""
        return self._attempt_counts.get(key, 1)

    @staticmethod
    def _payload(skill: str, body: dict) -> BaseModel:
        model = DevHandoffInput if skill == START_DEV else DevResultInput
        return model.model_validate(body)

    def _next_wait(self) -> float:
        """가장 먼저 다시 보낼 넘기기까지 남은 초 (시각이 없는 건 바로)."""
        now = _monotonic()
        dues = [self._due.get(k, now) for k in self._unsent]
        return max(0.0, min(dues, default=now) - now)

    def _schedule_retry(self) -> None:
        """못 보낸 넘기기를 때가 된 것부터 다시 보낸다 (한 번에 하나만 돈다)."""
        if self._retrying or not self._unsent:
            return
        self._retrying = True

        async def loop() -> None:
            try:
                while self._unsent:
                    await asyncio.sleep(self._next_wait())
                    await self.resend_unsent(due_only=True)
            finally:
                self._retrying = False

        try:
            task = asyncio.get_running_loop().create_task(loop())
        except RuntimeError:
            self._retrying = False
            return
        self._retry_task = task  # wait_sent는 기다리지 않는다 (계속 도는 루프)

    async def resend_unsent(self, due_only: bool = False) -> None:
        """못 보낸 넘기기를 한 번씩 다시 보낸다. due_only면 다시 보낼 때가 된 것만.

        저장된 내용을 읽지 못하는 줄은 루프를 멈추지 않게 빼서 거절 기록으로 옮긴다 (활동 한 줄)."""
        now = _monotonic()
        for key, entry in list(self._unsent.items()):
            if due_only and self._due.get(key, now) > now:
                continue
            try:
                skill, ctx, body = entry[:3]
                payload = self._payload(skill, body)
            except (TypeError, ValueError):  # ValidationError도 ValueError다
                skill, ctx = (str(x) for x in (list(entry) + ["", ""])[:2])
                body = entry[2] if isinstance(entry, list) and len(entry) > 2 else {}
                body = body if isinstance(body, dict) else {}
                reason = "저장된 내용을 읽지 못함"
                text = f"{skill or '넘기기'} {key} {reason} → 다시 보내지 않음"
                await self._hold_back("refused", skill, ctx, body, key, reason, text)
                continue
            await self._send_one(skill, ctx, payload, key)

    def _ack(self, key: str, fp: str) -> None:
        """받는 쪽이 받았다고 답한 넘기기를 남긴다. 오래된 것부터 지워 크기를 묶는다."""
        self._acked.pop(key, None)
        self._acked[key] = fp
        while len(self._acked) > ACKED_MAX:
            self._acked.pop(next(iter(self._acked)))

    def handoff_known(self, keys: list[str]) -> list[str]:
        """`handoff_known` (read): keys 중 이 서비스가 반영한(중복 거르기 기록에 있는) 키."""
        return [k for k in dict.fromkeys(keys) if k in self._idempotent]

    def _peer_role(self) -> str:
        return "dev" if self.role == "planning" else "planning"

    async def _peer_missing(self, keys: list[str]) -> set[str]:
        """받았다고 기록한 keys 중 받는 쪽이 갖고 있지 않은 키 (받는 쪽이 상태를 잃음).

        한 번 묻는다 (키가 많으면 나눠서). 묻지 못하면 빈 집합: 받았다는 기록대로 건너뛴다."""
        if not keys:
            return set()
        known: set[str] = set()
        for i in range(0, len(keys), HANDOFF_KNOWN_MAX_KEYS):
            part = keys[i : i + HANDOFF_KNOWN_MAX_KEYS]
            try:
                got = await self._ask_known(self._peer_role(), self.tenant_id, part)
            except Exception:  # noqa: BLE001 - 묻지 못한 것으로 본다
                got = None
            if got is None:
                _log.warning("%s: 받는 쪽에 넘기기를 묻지 못해 기록대로 건너뛴다", self.role)
                return set()
            known.update(got)
        return set(keys) - known

    def _peer_service(self) -> str:
        """넘기기를 받는 서비스 id (`call_agent`와 같이 `route_target`으로 정한다)."""
        if self.role == "planning":
            return route_target("dev", START_DEV)
        return route_target("planning", RECEIVE_DEV_RESULT)

    async def _peer_up(self) -> bool:
        """받는 서비스의 `/health`가 답하는지.

        켜지 않은 서비스(할 일함으로 넘김)는 기다리지 않는다."""
        service = self._peer_service()
        if not routing.is_enabled(service):
            return True
        try:
            url = f"{template_config.agent_url(service)}/health"
        except KeyError:
            return True
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=PEER_PROBE_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def wait_for_peer(self) -> bool:
        """받는 서비스가 뜰 때까지 2초·4초·8초… 최대 30초 간격으로 묻는다. 모두 합쳐 5분까지.

        떴으면 True, 끝내 안 뜨면 False (그래도 보내고 닿지 못한 건 간격을 늘려 다시 보낸다)."""
        return await wait_until_up(self._peer_up)

    async def _safety_up(self) -> bool:
        """실행 관문(safety)의 `/health`가 답하는지. 목 모드에서 `AX_SAFETY_URL`이 없으면 관문은
        safety를 부르지 않으니 뜬 것으로 본다 (`ax_agent_template.execute`와 같은 기준)."""
        if template_config.is_mock() and not os.getenv("AX_SAFETY_URL"):
            return True
        try:
            url = f"{template_config.agent_url('safety')}/health"
        except KeyError:
            return True
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=PEER_PROBE_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def wait_for_safety(self) -> bool:
        """실행 관문(safety)이 뜰 때까지 기다린다 (`wait_for_peer`와 같은 간격, 최대 5분).
        같이 다시 켜질 때 관문을 먼저 부르면 닿지 못해 거부(exe_unavailable)로 끝나기 때문이다."""
        return await wait_until_up(self._safety_up)

    async def resync_peer(self, wait_peer: bool = False) -> None:
        """다시 켰을 때 상대 서비스와 맞춘다: 못 보낸 것을 다시 보내고, 넘긴 기획서 중 받는 쪽이
        아직 받았다고 하지 않은 것을 보낸다 (같은 멱등 키라 상대가 이미 가진 건 그대로).

        - wait_peer면(켤 때) 받는 서비스가 뜰 때까지 기다린 뒤 보낸다 (`wait_for_peer`, 최대 5분).
          끝내 안 뜨면 그대로 보내고 닿지 못한 건 간격을 늘려 다시 보낸다.
        - 받는 쪽이 같은 입력을 받았다고 한 넘기기(acked, 지문 같음)는 건너뛰기 전에 받는 쪽에 한 번
          묻는다(`handoff_known`, read). 받는 쪽이 갖고 있지 않은 키(상태를 잃음)는 다시 보낸다.
          묻지 못하면 기록대로 건너뛴다. 입력이 바뀌었거나 받았다는 기록이 없으면 보낸다.
        - 받는 쪽이 같은 입력을 이미 거절한 넘기기(refused, 지문 같음)는 건너뛴다. 입력이 바뀌었으면
          다시 보낸다. 사람 할 일함으로 넘어간 것(handed)은 다시 보낸다 (받는 쪽이 켜졌을 수 있다).
          받는 쪽을 고친 뒤 거절된 건은 사람이 `resend_handoff`로 보낸다.
        - 받았다는 기록은 지금 넘길 것에 없는 키(지운 기획서·지난 지문)를 지워 크기를 묶는다."""
        if not self.role:
            return
        if wait_peer and not await self.wait_for_peer():
            _log.warning("%s: 받는 서비스가 뜨지 않아 그대로 맞춘다", self.role)
        await self.resend_unsent()
        refused = {f.key: f.fingerprint for f in self._handoff_failed if f.kind == "refused"}
        handed = {f.key for f in self._handoff_failed if f.kind == "handed"}
        outgoing = self._dev_outgoing(self.state, full=True)
        live = {key for *_, key in outgoing}
        self._acked = {k: v for k, v in self._acked.items() if k in live}
        plan: list[tuple[str, str, BaseModel, str]] = []
        skip: list[str] = []  # 받았다는 기록대로면 건너뛸 키 (받는 쪽에 한 번 묻는다)
        for skill, ctx, payload, key in outgoing:
            fp = fingerprint(payload.model_dump(mode="json"))
            if refused.get(key) == fp:
                continue
            if key not in handed and key not in self._unsent and self._acked.get(key) == fp:
                skip.append(key)
            plan.append((skill, ctx, payload, key))
        missing = await self._peer_missing(list(dict.fromkeys(skip)))
        skipped = set(skip) - missing
        for skill, ctx, payload, key in plan:
            if key in skipped:
                continue
            await self._send_one(skill, ctx, payload, key)
        self._save()

    async def _resend_handoff(
        self, s: State, key: str | None = None, everything: bool = False
    ) -> ActionResult | None:
        """사람 버튼: 자동으로 다시 보내지 않는 넘기기를 같은 멱등 키로 다시 보낸다 (잠금 밖에서).

        받는 쪽은 실패한 요청을 멱등 기록에 남기지 않아 같은 키로 다시 받는다. 기록은 보낸 결과로
        지우거나 바꾼다 (또 거절하면 다시 남는다). 저장된 내용을 읽지 못하는 건 보낼 수 없다.

        everything(`{"type": "resendHandoff", "all": true}`)이면 받았다는 기록(acked)을 무시하고
        넘긴 것을 모두 다시 보낸다 (받는 쪽이 상태를 잃었을 때)."""
        to = "dev" if self.role == "planning" else "planning"
        if everything and key is None:
            return self._resend_everything(s, to)
        picked = [f for f in self._handoff_failed if key is None or f.key == key]
        if not picked:
            raise ActionError("NOT_FOUND", "다시 보낼 넘기기가 없다")
        sent: list[str] = []
        for f in picked:
            try:
                payload = self._payload(f.skill, f.payload)
            except ValueError:
                continue
            self._resend_pending.append((f.skill, f.context_id, payload, f.key))
            sent.append(f.key)
            text = f"{f.skill} 사람이 다시 보냄\n지난 결과: {f.reason}"
            act = self._act(self.clock(), f.context_id, "human", to, f.skill, "사람", text)
            s.activity.append(act)
        if not sent:
            raise ActionError("INVALID_PAYLOAD", "저장된 내용을 읽지 못해 다시 보낼 수 없다")
        return ActionResult(output={"resent": sent})

    def _resend_everything(self, s: State, to: str) -> ActionResult:
        """넘긴 것을 모두 다시 보낸다 (받았다는 기록·거절 기록과 상관없이, 같은 멱등 키)."""
        items = self._dev_outgoing(s, full=True)
        keys = {k for *_, k in items}
        for f in self._handoff_failed:  # 지금 넘길 것에 없는 멈춘 넘기기도 같이
            if f.key in keys:
                continue
            try:
                items.append((f.skill, f.context_id, self._payload(f.skill, f.payload), f.key))
            except ValueError:
                continue
        if not items:
            raise ActionError("NOT_FOUND", "다시 보낼 넘기기가 없다")
        self._resend_pending.extend(items)
        ctx = items[0][1]
        text = f"넘긴 것 {len(items)}건을 사람이 모두 다시 보냄"
        s.activity.append(self._act(self.clock(), ctx, "human", to, "resend_handoff", "사람", text))
        return ActionResult(output={"resent": [k for *_, k in items]})

    async def wait_sent(self) -> None:
        """보내는 중인 넘기기가 끝날 때까지 기다린다 (테스트용)."""
        while self._sending:
            await asyncio.gather(*list(self._sending), return_exceptions=True)

    def _to_outbox(self, s: State, before: int) -> None:
        """기획·개발이 다른 부서로 보낸 활동은 그 부서 창구(outbox)에도 쌓는다."""
        next_id = len(s.outbox)
        for a in s.activity[before:]:
            if a.from_ == "planning" and a.to not in ("planning", "dev"):
                next_id += 1
                s.outbox.append(
                    OutMsg(
                        id=next_id,
                        at=a.at,
                        context_id=a.context_id,
                        tenant_id=a.tenant_id,
                        to=a.to,
                        point=a.point,
                        text=a.text,
                    )
                )

    async def _execute(
        self,
        action: str,
        ctx: str,
        target: str,
        summary: str,
        params: dict | None = None,
        refs: list[str] | None = None,
        allow: tuple[str, ...] = ("executed",),
    ) -> ExecuteDecision:
        """인사가 실행하는 기록(`record_case`)은 실제 모드에서 기획 관문을 거치지 않는다.

        안전 레이어 등록표에서 `record_case`의 owner는 hr이라, 기획이 관문에 물으면
        `held / C / unregistered_action`으로 보류되고(사람 보류 큐에 쌓임) 인사를 부르지 못했다.
        상태를 바꾸는 쪽은 인사이므로 인사가 자기 관문으로 실행한다 (총괄과 같은 방식, 11/2 준하 님
        실서버 확인). 목 모드(`_hr` None)는 예전처럼 관문 기록을 남긴다.
        """
        if action in HR_EXECUTED_ACTIONS and self._hr is not None:
            return ExecuteDecision(execution_id="hr_gate", grade="A", status="executed")
        return await super()._execute(action, ctx, target, summary, params, refs, allow)

    @staticmethod
    def _request_of_spec(s: State, spec: Spec) -> Request | None:
        return next((r for r in s.requests if r.spec == spec.id), None) or next(
            (r for r in s.requests if r.id == spec.request), None
        )

    def _hold(
        self,
        target: Spec | PR,
        state: str,
        check: Check,
        decision: ExecuteDecision | None = None,
        evidence: Any = None,  # CrossEvidence (개발 cross_check),
    ) -> AutoReview:
        """자동 확정·자동 머지를 멈추고 대기열에 올린다 (판단은 기존 사람 버튼)."""
        target.auto_review = AutoReview(
            state=state,
            rules=list(check.rules),
            params=dict(check.params),
            at=self.clock(),
            gate_status=decision.status if decision else None,
            review_id=decision.review_id if decision else None,
            evidence=[line.dump() for line in evidence.lines] if evidence else [],
        )
        return target.auto_review

    # ---- 화면 액션 진입점

    async def dispatch(self, action: dict) -> ActionResult:
        """`{"type": "confirmSpec", "spec": "SPEC-7"}` 같은 프로토타입 Action을 적용한다."""
        if not isinstance(action, dict) or not isinstance(action.get("type"), str):
            raise ActionError("INVALID_ACTION", "type이 없다")
        name = action_name(action["type"])
        if name not in self.ACTIONS:
            raise ActionError("UNKNOWN_ACTION", f"모르는 액션: {action['type']}")
        if name in SIMULATOR_ACTIONS and not template_config.is_mock(AGENT_NAME):
            raise ActionError("SIMULATOR_DISABLED", "시뮬레이터 액션은 목 모드에서만 받는다")
        method, model = self.ACTIONS[name]
        try:
            params = model.model_validate({k: v for k, v in action.items() if k != "type"})
        except ValidationError as exc:
            raise ActionError("INVALID_ACTION", str(exc)) from exc
        kwargs = {k: v for k, v in params.model_dump().items()}
        return await self._apply(getattr(self, method), **kwargs)

    # 이름이 있는 메서드 (테스트·내부 호출용). 모두 dispatch와 같은 길을 탄다.
    async def inbound(self, kind: str) -> ActionResult:
        return await self.dispatch({"type": "inbound", "kind": kind})

    async def send(self, to: str, point: str, text: str) -> ActionResult:
        return await self.dispatch({"type": "send", "to": to, "point": point, "text": text})

    async def mark_read(self, to: str) -> ActionResult:
        return await self.dispatch({"type": "mark_read", "to": to})

    # ---- 사람 클릭 없이 다음 단계로 (flow 요청만, 시드 건은 그대로)

    async def _advance(self, s: State) -> None:
        """액션마다 더 진행할 게 없을 때까지 다음 단계를 돈다: 기획서 초안(개선 초안은 채택 먼저) →
        자동 확정 검사 → PR 만들기 → 검수 불일치면 수정 후 재검수 → 자동 머지 검사.

        멈추는 곳: 자동 확정·머지 규칙에 걸린 건(`auto_review` 대기열), 검수 불일치 2회,
        safety 보류·거절. 관문이 거부하면 그 단계에서 멈추고 앞선 단계는 그대로 둔다."""
        if effective_level(s.auto_policy) == 0:  # L0 = 자동 처리 중지: 접수에서 멈춘다
            return
        for _ in range(ADVANCE_LIMIT):
            try:
                if not await self._advance_step(s):
                    return
            except GateRefused:
                return

    @staticmethod
    def _upsert(rows: list, items: list) -> None:
        """id가 같으면 그 자리에서 바꾸고, 없으면 뒤에 붙인다."""
        index = {r.id: i for i, r in enumerate(rows)}
        for item in items:
            if item.id in index:
                rows[index[item.id]] = item
            else:
                index[item.id] = len(rows)
                rows.append(item)

    # ---------------------------------------------------------------- 사람·에이전트 액션

    async def _send(self, s: State, to: str, point: str, text: str, ctx: str) -> None:
        t = self.clock()
        await self._execute("send_message", ctx, f"{to}:{point}:{len(s.activity)}", text)
        s.seq += 1
        s.activity.append(self._act(t, ctx, "planning", to, point, "A2A", text))

    async def _mark_read(self, s: State, to: str) -> None:
        for m in s.outbox:
            if m.to == to:
                m.read = True

    async def _notify_result(
        self,
        s: State,
        req: Request,
        status: Literal["completed", "declined"],
        *,
        prs: list[PR] | None = None,
        spec_id: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        """요청을 닫을 때 요청 부서에 `receive_change_result`를 보낸다(계약 #293).

        같은 요청에 묶인 중복 요청(`duplicate_of`)의 부서에도 한 번씩 보낸다. summary는 코드가 만든
        한 줄이고 고객 응대 문구로 쓰지 않는다(#293 리뷰). 보내기 실패는 요청 닫기를 막지 않는다.
        """
        merged = [p for p in (prs or []) if p.merged] if status == "completed" else []
        times = [p.merged_at for p in merged if p.merged_at]
        label = "완료" if status == "completed" else "기각"
        summary = f"{req.id} {label}" + (
            f" · {', '.join(p.id for p in merged)} 머지" if merged else ""
        )
        result = ChangeResultInput(
            request_id=req.id,
            status=status,
            pr_numbers=[p.number for p in merged],
            merged_at=max(times) if times else None,
            spec_id=spec_id,
            reason_code=reason_code if status == "declined" else None,
            summary=summary,
        )
        seen: set[str] = set()
        for r in [req, *(x for x in s.requests if x.duplicate_of == req.id)]:
            if r.from_ in seen:
                continue
            seen.add(r.from_)
            try:
                how = await self._send_result(r.from_, req.context_id, self.tenant_id, result)
            except Exception:  # 통지 실패가 요청 닫기를 되돌리지 않게 한다
                how = "보내지 못함 (오류)"
            s.activity.append(
                self._act(
                    self.clock(),
                    req.context_id,
                    "planning",
                    r.from_,
                    "receive_change_result",
                    "A2A",
                    f"{summary} 결과 통지 · {how}",
                    "A",
                )
            )


_log = logging.getLogger("ax.workflow")

RETRY_SECONDS = 30  # 못 보낸 넘기기를 처음 다시 보내기까지 (그 뒤로 두 배씩)
ACKED_MAX = 5000  # 받았다는 기록 상한 (다시 켰을 때 맞추기가 지금 없는 키도 지운다)
# 켤 때 받는 서비스가 뜰 때까지 기다리기: 2초부터 두 배씩, 한 번에 최대 30초, 모두 합쳐 5분
PEER_WAIT_FIRST_SECONDS = 2.0
PEER_WAIT_STEP_MAX_SECONDS = 30.0
PEER_WAIT_MAX_SECONDS = 300.0
PEER_PROBE_TIMEOUT_SECONDS = 3.0
RETRY_MAX_SECONDS = 15 * 60  # 다시 보내는 간격 상한


def retry_delay(attempts: int) -> float:
    """닿지 못한 넘기기를 attempts번 보낸 뒤 다음까지 기다릴 초: 30·60·120… 최대 15분."""
    return float(min(RETRY_SECONDS * 2 ** max(0, attempts - 1), RETRY_MAX_SECONDS))


def _monotonic() -> float:
    return time.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def wait_until_up(probe: Callable[[], Awaitable[bool]]) -> bool:
    """`probe`가 True일 때까지 2초·4초·8초… 최대 30초 간격으로 묻는다. 모두 합쳐 5분까지.
    떴으면 True, 끝내 안 뜨면 False. 묻다 난 오류는 아직 안 뜬 것으로 본다."""
    waited, gap = 0.0, PEER_WAIT_FIRST_SECONDS
    while True:
        try:
            if await probe():
                return True
        except Exception:  # noqa: BLE001 - 묻다 난 오류는 아직 안 뜬 것으로 본다
            pass
        if waited >= PEER_WAIT_MAX_SECONDS:
            return False
        step = min(gap, PEER_WAIT_MAX_SECONDS - waited)
        await _sleep(step)
        waited += step
        gap = min(gap * 2, PEER_WAIT_STEP_MAX_SECONDS)
