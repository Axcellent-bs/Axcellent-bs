"""기획·개발 서비스가 같이 쓰는 적용 틀 (분리 1단계: `planning_agent.domain.service`에서 옮김).

규칙:

- 프로세스 메모리에 상태 하나를 둔다. 상태 파일(`PLANNING_STATE_DIR`)이 있으면 이어 쓴다.
- 액션은 상태 복사본 위에서 계산하고, 끝까지 성공했을 때만 바꿔 끼운다
  (중간 실패 시 원래 상태 유지).
- 상태를 바꾸는 단계는 템플릿 실행 관문 `ax_agent_template.execute.execute()`를 거친다. 관문이
  `executed`가 아닌 결과(거절·보류·무응답)를 주면 `GateRefused`를 던지고 그 액션 전체를 버린다
  (fail-closed). `soft_actions`만은 보류돼도 본 작업을 되돌리지 않는다.
- 서비스마다 다른 일(새 요청 관계 찾기·다음 단계 진행·넘기기)은
  `_mark`·`_settle`·`_after` 훅으로 붙인다.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template import config as template_config
from ax_agent_template import execute as gate_module
from ax_contracts.schemas import TENANT_ID_DEFAULT, Evidence, ExecuteDecision, RequestMeta
from ax_workflow import store
from ax_workflow.clock import Clock, default_clock_factory
from ax_workflow.models import Activity, State, dump

Gate = Callable[[str, dict, RequestMeta, Evidence], Awaitable[ExecuteDecision]]


async def template_gate(
    action: str, params: dict, context: RequestMeta, evidence: Evidence
) -> ExecuteDecision:
    """템플릿 실행 관문. 모듈 속성으로 찾아 부르므로 테스트에서 `_send_to_safety`를 바꿀 수 있다."""
    return await gate_module.execute(action, params, context, evidence)


class GateRefused(Exception):
    """관문이 실행을 허락하지 않았다. 액션 전체를 버린다."""

    def __init__(self, action: str, idempotency_key: str, decision: ExecuteDecision):
        super().__init__(f"{action} 실행 거부 ({decision.status})")
        self.action = action
        self.idempotency_key = idempotency_key
        self.decision = decision


class ActionError(Exception):
    """잘못된 액션 (모르는 type, 빠진 필드, 목 모드 밖 시뮬레이터)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class GateCall:
    action: str
    idempotency_key: str
    status: str
    grade: str
    execution_id: str


@dataclass
class ActionResult:
    changed: bool = False
    outcome: str | None = None
    output: dict[str, Any] = field(default_factory=dict)


class StateService:
    """상태·구독·적용 틀·실행 관문·활동 기록. 서비스는 이 위에 업무 액션을 얹는다.

    하위 클래스는 필요한 필드를 다 둔 뒤 마지막에 `_init_state()`를 부른다 (시드는 `_seed`)."""

    agent_name = "planning"  # 관문에 보내는 에이전트 토큰 이름
    soft_actions: frozenset[str] = frozenset()  # 보류돼도 본 작업을 되돌리지 않는 부수 기록

    def __init__(
        self,
        clock: Clock | None = None,
        gate: Gate | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        role: str = "",
        real_example: bool = False,
    ):
        self.role = role
        self._clock_factory: Callable[[], Clock] = (
            (lambda: clock) if clock else default_clock_factory()
        )
        self._gate = gate or template_gate
        self.tenant_id = tenant_id
        self.gate_calls: list[GateCall] = []
        # 보류돼도 본 작업을 막지 않는 부수 기록 (soft_actions)
        self.soft_holds: list[GateCall] = []
        self._subscribers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self.version = 0
        # 실행 중인 서버는 예시 대신 실제 머지된 작업 하나로 시작한다 (seed.real_state)
        self.real_example = real_example

    # ---- 상태·구독

    def _seed(self) -> State:
        raise NotImplementedError

    def _init_state(self, fresh: bool = False) -> None:
        self.clock: Clock = self._clock_factory()
        self.state: State = self._seed()
        self.state.tenant_id = self.tenant_id
        self._idempotent: dict[str, ActionResult] = {}
        self._dev_sent: dict[str, str] = {}  # dev: 기획서 → 마지막으로 돌려준 결과 지문
        # 못 보낸 넘기기 (멱등 키 → [skill, context_id, 입력]). 주기적으로 다시 보낸다
        self._unsent: dict[str, list] = {}
        # 상태 파일(PLANNING_STATE_DIR)이 있으면 이어서 시작한다. 초기화(fresh)면 시드로 덮어쓴다
        self._state_path = store.path_for(self.role)
        if self._state_path and not fresh:
            saved = store.load(self._state_path)
            if saved is not None:
                self.state, idem, self._dev_sent, self._unsent = saved
                self._idempotent = {k: ActionResult(**v) for k, v in idem.items()}
        elif self._state_path:
            self._save()

    def _save(self) -> None:
        """상태를 파일에 이어 쓴다 (파일 위치가 있을 때만). 실패해도 동작은 계속한다."""
        if not self._state_path:
            return
        try:
            store.save(self._state_path, self.state, self._idempotent, self._dev_sent, self._unsent)
        except OSError:
            pass

    def reset(self) -> None:
        """초기 데이터로 되돌린다 (구독자는 유지하고 새 상태를 알린다). 상태 파일도 덮어쓴다."""
        self._init_state(fresh=True)
        self.gate_calls.clear()
        self.version += 1
        self._notify()

    def snapshot(self) -> dict:
        return dump(self.state)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[id(queue)] = (asyncio.get_running_loop(), queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.pop(id(queue), None)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _notify(self) -> None:
        for key, (loop, queue) in list(self._subscribers.items()):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, self.version)
            except RuntimeError:  # 닫힌 이벤트 루프
                self._subscribers.pop(key, None)

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = self._locks[loop] = asyncio.Lock()
        return lock

    # ---- 적용 틀

    async def _apply(self, fn: Callable[..., Awaitable[ActionResult | None]], **params: Any):
        """복사본에 액션을 적용하고, 끝까지 성공하면 바꿔 끼운다. 관문 거부면 원래 상태 유지."""
        async with self._lock():
            s = self.state.model_copy(deep=True)
            mark = self._mark(s)
            result = await fn(s, **params) or ActionResult()
            await self._settle(s, mark)
            result.changed = s != self.state
            if result.changed:
                self.state = s
                self.version += 1
                self._save()
                self._notify()
        # 네트워크를 부르는 뒷일은 상태를 바꿔 끼운 뒤 잠금 밖에서 한다
        self._after(s, mark, result.changed)
        return result

    def _mark(self, s: State) -> Any:
        """액션 전 상태에서 뒤에 비교할 것을 적어 둔다 (서비스마다)."""
        return None

    async def _settle(self, s: State, mark: Any) -> None:
        """액션 뒤, 바꿔 끼우기 전에 같은 복사본에서 더 할 일 (서비스마다)."""

    def _after(self, s: State, mark: Any, changed: bool) -> None:
        """바꿔 끼운 뒤 잠금 밖에서 할 일 (서비스마다). `s`는 이번 액션이 계산한 상태."""

    # ---- 실행 관문·활동 기록

    def _token(self) -> str:
        try:
            return template_config.agent_token(self.agent_name)
        except RuntimeError:
            return "unconfigured"  # 관문 쪽에서 거부된다 (fail-closed)

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
        """실행 관문을 거친다. 허용 상태가 아니면 `GateRefused` (fail-closed)."""
        key = f"{ctx}:{action}:{target}"
        meta = RequestMeta(
            context_id=ctx,
            tenant_id=self.tenant_id,
            token=self._token(),
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            idempotency_key=key,
        )
        evidence = Evidence(data_summary=summary, ref_ids=refs or [target])
        try:
            decision = await self._gate(
                action, {"target": target, **(params or {})}, meta, evidence
            )
        except Exception:
            decision = ExecuteDecision(execution_id="exe_unavailable", grade="C", status="rejected")
        self.gate_calls.append(
            GateCall(action, key, decision.status, decision.grade, decision.execution_id)
        )
        if decision.status not in allow:
            # 부수 기록(예: 인사 record_case)은 다른 부서가 실행하는 일이라 이쪽 관문이 보류할 수
            # 있다. 본 작업은 이미 끝났으니 되돌리지 않고 보류만 남긴다 (soft_holds)
            if action in self.soft_actions:
                self.soft_holds.append(
                    GateCall(action, key, decision.status, decision.grade, decision.execution_id)
                )
                return decision
            raise GateRefused(action, key, decision)
        return decision

    def _act(
        self,
        at: datetime,
        ctx: str,
        frm: str,
        to: str,
        point: str,
        kind: str,
        text: str,
        grade: str | None = None,
        seq: int | None = None,
    ) -> Activity:
        return Activity(
            seq=seq,
            at=at,
            context_id=ctx,
            tenant_id=self.tenant_id,
            from_=frm,
            to=to,
            point=point,
            kind=kind,
            grade=grade,
            text=text,
        )
