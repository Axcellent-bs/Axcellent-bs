"""의뢰 실행: 접수된 의뢰의 계획을 첫 작업 부서부터 순서대로 시킨다 (의뢰 자동 넘김 2단계).

장애 이벤트용 엔진(`engine.py`)은 이벤트(Incident) 전제라서 건드리지 않고, 의뢰 기록
(`work_requests.py`) 위에서 도는 작은 실행기를 따로 둔다. 부서를 부르는 길(레지스트리·호출기)은
엔진의 것을 그대로 쓴다.

- 시킬 수 있는 단계(`ready_steps`): 안 시작했고 선행 단계가 모두 성공으로 끝난 것. 흐름의 첫 부서가
  먼저고, 앞 단계가 실패하면 뒤 단계는 시키지 않고 건너뜀으로 닫는다.
- LLM은 부르지 않는다. 순서·부서·skill은 일 목록이 정한 대로다.
- 고객 글은 부서에 `untrusted` 칸(데이터)으로만 간다. 지시문으로 넣지 않는다.
- 읽기(`read`) 단계만 스스로 시킨다. 상태를 바꾸는 단계(A·B·C)와 사람 확인 단계는 승인·확인이
  필요해서 여기서 멈추고 `waiting_human`으로 둔다(승인 이어가기는 9단계).
- 부서가 받아 간 긴 작업(`working`)과 사람 승인·할 일함 대기는 번호만 적고 `held`·`waiting_async`·
  `waiting_human`으로 둔다(이어 받기는 이후 단계).
- 끝나지 않은 일이 있어도 서버는 멈추지 않는다. 실행 중 예외는 그 단계를 실패로 닫고 의뢰를
  사람 확인(`needs_human`)으로 돌린다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ax_contracts.schemas import (
    HANDED_TO_HUMAN,
    AgentRequest,
    AgentResult,
    ErrorCode,
    InputReason,
    RequestMeta,
    TaskStatus,
)
from ax_orchestrator import request_tasks
from ax_orchestrator.engine import TRANSFER_CODES, Engine
from ax_orchestrator.models import now
from ax_orchestrator.work_requests import OPEN_STATES, READ_GRADE, WorkRequests, ready_steps

log = logging.getLogger("orchestrator.request_runner")

MAX_FAILED_RETRIES = 2
MAX_CANCELED_RETRIES = 1
STEP_TIMEOUT = 30.0
RETRY_DELAY = 0.2
WAITING = ("held", "waiting_human", "waiting_async")
NOT_OK = ("failed", "skipped", "rejected_by_human")
NEEDS_APPROVAL = "needs_approval"
HUMAN_CHECK = "human_check"
NO_AGENT = "no_agent"
NO_REF = "no_ref"
APPROVED = "approved"
REF_PATTERN = re.compile(r"[\w:.\-]{1,80}")  # 부서가 정한 결과 번호 모양 (문장은 받지 않는다)
Notifier = Callable[[str, str], None]
Escalator = Callable[[str, str, str | None], None]
ScopeCheck = Callable[[dict[str, Any], dict[str, Any]], bool]
OUT_OF_SCOPE = "out_of_scope"


def in_catalog(row: dict[str, Any], spec: dict[str, Any]) -> bool:
    """이 단계가 고른 일의 일 목록에 있는 단계(부서·skill)와 같은가. 다르면 시키지 않는다."""
    task = request_tasks.get(row.get("task"))
    return task is not None and any(
        s.dept == spec.get("dept") and s.skill == spec.get("skill") and s.kind == spec.get("kind")
        for s in task.steps
    )


class RequestRunner:
    def __init__(
        self,
        requests: WorkRequests,
        get_engine: Callable[[], Engine | None],
        *,
        step_timeout: float = STEP_TIMEOUT,
        retry_delay: float = RETRY_DELAY,
        notify: Notifier | None = None,
        escalate: Escalator | None = None,
        scope_check: ScopeCheck = in_catalog,
    ) -> None:
        self._requests = requests
        self._get_engine = get_engine
        self._step_timeout = step_timeout
        self._retry_delay = retry_delay
        # 의뢰자에게 진행을 알린다: (대화 번호, 문장). 정해진 문장 틀만 보낸다. None이면 안 알린다
        self.notify = notify
        # 끝내지 못한 의뢰를 사람 받은 요청에 올린다: (번호, 이유, 실패 단계). None이면 기록만
        self.escalate = escalate
        self._scope_check = scope_check
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- 시작 ------------------------------------------------------------------
    def start(self, request_id: str) -> None:
        """의뢰를 뒤에서 실행한다 (대화 답은 기다리지 않는다). 이미 실행 중이면 한 번만 돈다."""
        task = asyncio.get_running_loop().create_task(self.run(request_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def resume_open(self) -> int:
        """다시 켠 뒤 시킬 단계가 남은 열린 의뢰를 이어서 시킨다. 이어 받은 수를 돌려준다."""
        count = 0
        for row in self._requests.open_requests():
            if row["status"] in ("classified", "running", "waiting") and ready_steps(row):
                self.start(row["request_id"])
                count += 1
        return count

    async def wait_idle(self) -> None:
        """실행 중인 일이 모두 끝날 때까지 기다린다 (시험·종료용)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ---- 실행 ------------------------------------------------------------------
    async def run(self, request_id: str) -> None:
        if request_id in self._running:
            return
        self._running.add(request_id)
        try:
            await self._drive(request_id)
        except Exception as exc:  # noqa: BLE001 - 실행 오류가 서버를 멈추지 않게 사람 확인으로 돌린다
            log.exception("의뢰 실행 실패: %s", request_id)
            self._safe_status(request_id, "needs_human", f"실행 중 오류 ({type(exc).__name__})")
        finally:
            self._running.discard(request_id)

    def _safe_status(self, request_id: str, status: str, message: str) -> None:
        try:
            self._requests.set_status(request_id, status, message)
        except KeyError:
            pass

    async def _drive(self, request_id: str) -> None:
        row = self._requests.get(request_id)
        if row is None or row["status"] not in OPEN_STATES or not row["plan"]:
            return
        task = request_tasks.get(row["task"]) if row.get("task") else None
        if task is not None and task.blocked_by:
            self._requests.set_status(
                request_id, "needs_human", f"아직 자동으로 진행할 수 없는 일: {task.blocked_by}"
            )
            return
        self._requests.set_status(request_id, "running", "부서에 시키기 시작했다")
        while True:
            row = self._requests.get(request_id)
            if row is None:
                return
            ready = ready_steps(row)
            if not ready:
                break
            await asyncio.gather(*(self._step(row, sid) for sid in ready))
        self._finish(request_id)

    async def _step(self, row: dict[str, Any], step_id: str) -> None:
        """단계 하나를 시킨다. 어떤 일이 있어도 이 단계는 `pending`을 벗어난다 (무한 반복 방지)."""
        rid = row["request_id"]
        try:
            await self._run_step(row, step_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("의뢰 단계 실패: %s/%s", rid, step_id)
            self._update(
                rid,
                step_id,
                status="failed",
                note=f"실행 중 오류 ({type(exc).__name__})",
                finished_at=now().isoformat(),
            )

    async def _run_step(self, row: dict[str, Any], step_id: str) -> None:
        rid = row["request_id"]
        spec = next(s for s in row["plan"] if s["step_id"] == step_id)
        stamp = now().isoformat
        if spec.get("kind") == "human":
            self._update(
                rid,
                step_id,
                status="waiting_human",
                reason_code=HUMAN_CHECK,
                note="사람 확인을 기다린다",
                started_at=stamp(),
            )
            return
        if not self._scope_check(row, spec):  # 일 목록에 없는 단계는 어떤 부서에도 보내지 않는다
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=OUT_OF_SCOPE,
                note="고른 일의 목록에 없는 단계라 시키지 않았다",
                started_at=stamp(),
                finished_at=stamp(),
            )
            return
        engine = self._get_engine()
        entry = engine.registry.find(spec["skill"]) if engine else None
        if engine is not None and entry is not None and entry.dept != spec.get("dept"):
            entry = None  # 계획의 부서와 카드의 부서가 다르면 부르지 않는다
        if engine is None or entry is None or not entry.callable_by_orchestrator:
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=NO_AGENT,
                note="담당 부서를 확인할 수 없다 (카드에 없거나 총괄이 시킬 수 없는 일)",
                started_at=stamp(),
                finished_at=stamp(),
            )
            return
        step = row["steps"][step_id]
        grade = entry.grade or step.get("grade")
        if grade != READ_GRADE and step.get("reason_code") != APPROVED:  # 승인 없이는 시키지 않는다
            self._update(
                rid,
                step_id,
                status="waiting_human",
                grade=grade,
                reason_code=NEEDS_APPROVAL,
                note="상태를 바꾸는 일이라 승인을 기다린다",
                started_at=stamp(),
            )
            return
        payload = self._input_of(row, spec)
        if payload is None:
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=NO_REF,
                note="앞 단계의 결과 번호가 없어 시킬 수 없다",
                started_at=stamp(),
                finished_at=stamp(),
            )
            return
        self._update(rid, step_id, status="running", grade=grade, started_at=stamp())
        result = await self._call(engine, row, spec, entry.dept, step_id, payload)
        self._apply(rid, step_id, result, spec)

    # ---- 부서 호출 ----------------------------------------------------------------
    @staticmethod
    def _input_of(row: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
        """부서에 보낼 입력. 앞 단계 결과는 번호(`result_ref`)만 넘긴다. 필요한데 없으면 None."""
        key = spec.get("takes_ref")
        if not key:
            return {}
        for before in spec.get("after") or []:
            ref = row["steps"][before].get("result_ref")
            if ref:
                return {key: ref}
        return None

    async def _call(
        self,
        engine: Engine,
        row: dict[str, Any],
        spec: dict[str, Any],
        dept: str,
        step_id: str,
        payload: dict[str, Any],
    ) -> AgentResult | str:
        """부서를 부른다. 읽기 일이라 실패·마감 초과는 정해진 횟수만 다시 부른다. 못 받으면 이유."""
        rid = row["request_id"]
        failed = canceled = attempts = 0
        while True:
            attempts += 1
            self._update(rid, step_id, attempts=attempts)
            req = AgentRequest(
                meta=RequestMeta(
                    context_id=row.get("context_id") or f"ctx_{rid}",
                    token="orchestrator",
                    path=["orchestrator"],
                    deadline=now() + timedelta(seconds=self._step_timeout),
                    idempotency_key=f"{rid}:{spec['skill']}:{step_id}",
                ),
                skill=spec["skill"],
                input=payload,
                untrusted={"request_text": row["text"]},  # 고객 글은 데이터 칸으로만
            )
            try:
                result = await asyncio.wait_for(
                    engine.caller(dept, req), timeout=self._step_timeout + 5
                )
            except TimeoutError:
                return "부서 응답 시간 초과"
            if result.status == TaskStatus.CANCELED and canceled < MAX_CANCELED_RETRIES:
                canceled += 1
                await asyncio.sleep(self._retry_delay)
                continue
            retryable = result.output.get("retryable") is not False
            if result.status == TaskStatus.FAILED and retryable and failed < MAX_FAILED_RETRIES:
                failed += 1
                await asyncio.sleep(self._retry_delay)
                continue
            return result

    def _apply(
        self, rid: str, step_id: str, result: AgentResult | str, spec: dict[str, Any]
    ) -> None:
        """부서 결과를 단계 상태로 옮긴다. 부서가 돌려준 문장은 옮기지 않고 상태·코드만 둔다."""
        done = now().isoformat()
        if isinstance(result, str):
            self._update(rid, step_id, status="failed", note=result, finished_at=done)
            return
        status, outcome = result.status, result.outcome or ""
        out = result.output or {}
        if status == TaskStatus.COMPLETED and outcome not in TRANSFER_CODES:
            ref = self._ref_of(out, spec)
            self._update(
                rid, step_id, status="completed", note=outcome, result_ref=ref, finished_at=done
            )
        elif status == TaskStatus.INPUT_REQUIRED:
            self._wait(rid, step_id, result, out)
        elif status == TaskStatus.WORKING:
            task_id = out.get("task_id")
            if isinstance(task_id, str) and task_id:
                self._update(
                    rid,
                    step_id,
                    status="waiting_async",
                    async_task_id=task_id,
                    note="부서가 긴 작업으로 받아 진행 중",
                )
            else:
                self._update(
                    rid,
                    step_id,
                    status="failed",
                    note="긴 작업을 받았다는데 작업 번호가 없다",
                    finished_at=done,
                )
        elif status == TaskStatus.REJECTED and outcome == ErrorCode.SERVICE_DISABLED.value:
            self._update(
                rid,
                step_id,
                status="skipped",
                reason_code=outcome,
                note="확인할 수 없다 (받는 서비스가 꺼져 있다)",
                finished_at=done,
            )
        else:
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=outcome or None,
                note=f"{status.value}: {outcome or '사유 없음'}",
                finished_at=done,
            )

    @staticmethod
    def _ref_of(out: dict[str, Any], spec: dict[str, Any]) -> str | None:
        """부서가 정한 결과 번호만 읽는다. 번호 모양이 아니면(문장 등) 버린다."""
        key = spec.get("ref_key")
        ref = out.get(key) if key else None
        return ref if isinstance(ref, str) and REF_PATTERN.fullmatch(ref) else None

    def _wait(self, rid: str, step_id: str, result: AgentResult, out: dict[str, Any]) -> None:
        """부서가 사람·다른 부서의 판단을 기다린다 (input-required). 번호만 적고 멈춘다."""
        reason = result.reason_code
        code = reason.value if reason else None
        if result.outcome == HANDED_TO_HUMAN:
            tid = out.get("task_id")
            self._update(
                rid,
                step_id,
                status="waiting_human",
                reason_code=code,
                handoff_task_id=tid if isinstance(tid, str) else None,
                note="부서 서비스가 꺼져 있어 사람 할 일함으로 넘어갔다",
            )
        elif reason == InputReason.HUMAN_INPUT:
            review = out.get("review_id")
            self._update(
                rid,
                step_id,
                status="held",
                reason_code=code,
                review_id=review if isinstance(review, str) else None,
                note="사람 승인을 기다린다",
            )
        else:
            self._update(
                rid,
                step_id,
                status="waiting_human",
                reason_code=code,
                note="부서가 더 필요한 입력을 기다린다",
            )

    # ---- 마무리 ------------------------------------------------------------------
    def _finish(self, rid: str) -> None:
        """남은 단계를 정리하고 의뢰 상태를 정한다 (끝남·실패·기다림)."""
        row = self._requests.get(rid)
        if row is None:
            return
        self._skip_blocked(row)
        row = self._requests.get(rid)
        if row is None:
            return
        states = [s["status"] for s in row["steps"].values()]
        if any(s in WAITING for s in states):
            self._requests.set_status(rid, "waiting", "승인이나 사람 확인을 기다린다")
            self._tell(row, "확인이 필요한 단계가 있어 담당자의 확인을 기다리고 있어요.")
        elif "failed" in states:  # 재시도 뒤에도 끝내지 못했다: 멈추지 않고 사람에게 넘긴다
            self._requests.set_status(rid, "needs_human", "단계가 끝나지 못해 사람에게 넘겼다")
            self._tell(row, "일부 단계를 끝내지 못했어요. 담당자가 확인할 거예요.")
            failed = next(k for k, v in row["steps"].items() if v["status"] == "failed")
            if self.escalate is not None:
                self.escalate(rid, "step_failed", failed)
        elif any(s in NOT_OK for s in states):  # 사람이 거절했거나 앞 단계 때문에 건너뜀
            self._requests.set_status(rid, "failed", "사람이 거절했거나 앞 단계가 끝나지 못했다")
            self._tell(row, "의뢰를 진행하지 않기로 했어요.")
        elif all(s == "completed" for s in states):
            self._requests.set_status(rid, "done", "모든 단계를 마쳤다")
            self._tell(row, "모든 단계를 마쳤어요.")
        else:
            self._requests.set_status(
                rid, "needs_human", "진행 상태를 알 수 없어 사람 확인이 필요하다"
            )

    def _tell(self, row: dict[str, Any], text: str) -> None:
        """의뢰를 접수한 대화로 진행을 알린다. 알리지 못해도 의뢰 진행은 멈추지 않는다."""
        thread = row.get("thread_id")
        if self.notify is None or not thread:
            return
        try:
            self.notify(thread, f"[의뢰 {row['request_id']}] {text}")
        except Exception:  # noqa: BLE001
            log.warning("의뢰 진행 알림 실패: %s", row["request_id"])

    # ---- 사람 확인·승인 -------------------------------------------------------------
    def decide(self, request_id: str, step_id: str, approved: bool, by: str | None = None) -> str:
        """사람이 확인·승인을 정한다. 돌려주는 값: ok · missing · not_waiting.

        - 사람 확인 단계: 승인하면 완료로 닫고 뒤 단계를 이어 시킨다. 거절하면 `rejected_by_human`.
        - 승인 대기(상태를 바꾸는 일): 승인하면 시킬 수 있게 되돌린다(부서의 안전 관문은
          따로 거친다). 거절하면 `rejected_by_human`.
        어느 쪽이든 뒤 단계는 앞 단계가 성공해야만 시킨다.
        """
        row = self._requests.get(request_id)
        step = (row or {}).get("steps", {}).get(step_id)
        if row is None or step is None:
            return "missing"
        if step["status"] != "waiting_human" or step["reason_code"] not in (
            HUMAN_CHECK,
            NEEDS_APPROVAL,
        ):
            return "not_waiting"
        who = f" ({by})" if by else ""
        done = now().isoformat()
        if not approved:
            self._update(
                request_id,
                step_id,
                status="rejected_by_human",
                note=f"사람이 거절했다{who}",
                finished_at=done,
            )
        elif step["reason_code"] == HUMAN_CHECK:
            self._update(
                request_id,
                step_id,
                status="completed",
                note=f"사람이 확인했다{who}",
                finished_at=done,
            )
        else:
            self._update(
                request_id,
                step_id,
                status="pending",
                reason_code=APPROVED,
                note=f"사람이 승인했다{who}",
            )
        self.start(request_id)
        return "ok"

    def rerun(self, request_id: str, task_name: str, by: str | None = None) -> str:
        """사람이 일을 골라 의뢰를 다시 돌린다. 돌려주는 값: ok · missing · unknown_task · busy.

        계획과 단계 진행은 새로 시작한다(이전 선택은 기록에 남는다). 상태를 바꾸는 단계는 이번에도
        승인을 거친다. 부서에 보내는 중이거나 기다리는 단계가 있으면(`busy`) 바꾸지 않는다.
        """
        row = self._requests.get(request_id)
        if row is None:
            return "missing"
        task = request_tasks.get(task_name)
        if task is None:
            return "unknown_task"
        busy = request_id in self._running or any(
            s["status"] in ("running", "held", "waiting_async") for s in row["steps"].values()
        )
        if busy:
            return "busy"
        self._requests.replan(request_id, task=task.name, plan=request_tasks.plan_of(task), by=by)
        self.start(request_id)
        return "ok"

    def _skip_blocked(self, row: dict[str, Any]) -> None:
        """앞 단계가 성공하지 못해 시킬 수 없게 된 뒤 단계를 건너뜀으로 닫는다 (연달아 반복)."""
        rid = row["request_id"]
        changed = True
        while changed:
            changed = False
            row = self._requests.get(rid) or row
            for spec in row["plan"]:
                step = row["steps"][spec["step_id"]]
                if step["status"] != "pending":
                    continue
                bad = [a for a in spec.get("after") or [] if row["steps"][a]["status"] in NOT_OK]
                if bad:
                    self._update(
                        rid,
                        spec["step_id"],
                        status="skipped",
                        note=f"앞 단계가 끝나지 못해 건너뜀 ({', '.join(bad)})",
                        finished_at=now().isoformat(),
                    )
                    changed = True

    def _update(self, rid: str, step_id: str, **fields: Any) -> None:
        self._requests.update_step(rid, step_id, **fields)
