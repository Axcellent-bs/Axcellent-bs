"""의뢰 실행: 접수된 의뢰의 계획을 첫 작업 부서부터 순서대로 시킨다 (의뢰 자동 넘김 2단계).

장애 이벤트용 엔진(`engine.py`)은 이벤트(Incident) 전제라서 건드리지 않고, 의뢰 기록
(`work_requests.py`) 위에서 도는 작은 실행기를 따로 둔다. 부서를 부르는 길(레지스트리·호출기)은
엔진의 것을 그대로 쓴다.

- 시킬 수 있는 단계(`ready_steps`): 안 시작했고 선행 단계가 모두 성공으로 끝난 것. 흐름의 첫 부서가
  먼저고, 앞 단계가 실패하면 뒤 단계는 시키지 않고 건너뜀으로 닫는다.
- LLM은 부르지 않는다. 순서·부서·skill은 일 목록이 정한 대로다.
- 고객 글은 부서에 `untrusted` 칸(데이터)으로만 간다. 지시문으로 넣지 않는다.
- 읽기(`read`)와 상태를 바꾸는 A·B 단계는 스스로 시킨다. A(자동)·B(교차검증)는 부서가 안전 관문
  (`execute()`)으로 등급대로 처리하므로 총괄이 한 번 더 막지 않는다. 다만 고객 문장에서 입력이
  나오는 상태 변경(`human_gate`: 기능 개발 요청·환불 접수)과 C 등급, 사람 확인 단계는 여기서
  멈추고 `waiting_human`으로 둔다. 분류 LLM이 고객 글에 속아도 사람 승인 없이는 실행되지
  않게 하는 방어다.
- 부서가 받아 간 긴 작업(`working`)은 `waiting_async`로 두고 작업 번호를 적는다. 부서가 보내는
  `task.progress`·`task.completed`를 `receive_async`로 받아 단계를 끝내고 다음 단계를 이어 시킨다.
  알림이 `ASYNC_SILENCE`초 넘게 없으면 응답 끊김으로 그 단계를 실패로 닫는다(`expire_silent`).
- 사람 승인·할 일함 대기는 번호만 적고 `held`·`waiting_human`으로 둔다.
- 앞 단계 결과 중 다음 단계가 통째로 받는 값(홍보 영상의 기획 입력)만 의뢰 기록 안 `carry`에 두고
  쓰고 나면 지운다. 화면·조회에는 내지 않는다.
- 끝나지 않은 일이 있어도 서버는 멈추지 않는다. 실행 중 예외는 그 단계를 실패로 닫고 의뢰를
  사람 확인(`needs_human`)으로 돌린다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
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
from ax_orchestrator import request_inputs, request_tasks
from ax_orchestrator.engine import TRANSFER_CODES, Engine
from ax_orchestrator.models import now
from ax_orchestrator.work_requests import (
    OPEN_STATES,
    RESTART_UNCONFIRMED,
    STEP_WAITING,
    WorkRequests,
    ready_steps,
)

HUMAN_GRADE = request_tasks.HUMAN_GRADE

log = logging.getLogger("orchestrator.request_runner")

MAX_FAILED_RETRIES = 2
MAX_CANCELED_RETRIES = 1
# 11/25 의뢰 단계는 기본으로 시간 제한을 두지 않는다(부서가 답할 때까지 기다린다). 30초로 끊으니
# 기획 제작안처럼 길게 쓰는 단계가 일하는 중에 "응답 대기 시간 초과"로 실패했다.
# 단계에 `timeout_s`를 주면 그때만 끊는다. 마감 칸은 계약상 비울 수 없어 아주 먼 시각을 넣는다
STEP_TIMEOUT: float | None = None
NO_LIMIT_DEADLINE = timedelta(days=7)
RETRY_DELAY = 0.2
WAITING = ("held", "waiting_human", "waiting_async")
NOT_OK = ("failed", "skipped", "rejected_by_human")
HIDE_NOTE = "사람이 의뢰를 삭제함"  # 의뢰 삭제(멈추고 숨김)로 닫은 단계·의뢰의 이유
# 의뢰문 수정에서 "실제로 새 의뢰가 생긴" 접수 결과(허용 목록). 이때만 옛 의뢰를 숨기고 잇는다.
# 일 접수·되묻기(입력 되묻기 포함)·두 번째 되묻기에서 사람에게·담당 불명·구매자 연결 없음 안내
NEW_REQUEST_OUTCOMES = frozenset({"task", "clarify", "unclear", "unknown", "no_link"})
EDIT_TOLD = "의뢰문을 고쳐 새 의뢰로 다시 접수했어요. 이전 의뢰({old})는 멈추고 숨겼어요."
NEEDS_APPROVAL = "needs_approval"
HUMAN_CHECK = "human_check"
NO_AGENT = "no_agent"
NO_REF = "no_ref"
APPROVED = "approved"
NO_INPUT = "no_input"  # 고객 문장에서 부서가 요구하는 입력을 찾지 못해 부서에 보내지 않음
NO_CARRY = "no_carry"  # 앞 단계가 넘겨야 할 결과가 없어 이 단계를 시키지 않음
NO_INSIGHT = "no_insight"  # 기획에 넘길 근거(facts)가 없어 영상 제작안을 건너뜀
ASYNC_SILENT = "async_silent"
ASYNC_SILENCE = 300.0  # 긴 작업 알림이 이만큼(초) 없으면 응답 끊김 (엔진의 기준과 같다)
WATCH_INTERVAL = 60.0
MAX_EARLY_KEYS = 100
MAX_EARLY_EACH = 20
REF_PATTERN = re.compile(r"[\w:.\-]{1,80}")  # 부서가 정한 결과 번호 모양 (문장은 받지 않는다)
# 11/23 회사 캐릭터 홍보 영상: 마케팅이 영상(C generate_creative_video)을 사람 승인에 올렸다(보류)·
# 사람이 거절했다. 보류는 실패가 아니라 "영상 승인 대기"이고, 승인 결정이 오면 같은 단계를
# 다시 보낸다
VIDEO_HELD = "video_held"
VIDEO_REJECTED = "video_rejected"
VIDEO_APPROVAL = "video_approval"
VIDEO_WAIT_NOTE = "영상 승인 대기"
VIDEO_REJECT_NOTE = "사람이 영상 생성을 거절함"
# 11/24 홍보 영상: 회사 캐릭터 답 대기. 의뢰 상태는 사람 확인 대기와 같은 `waiting`이고, 이 단계는
# `decide`(승인·거절)가 아니라 캐릭터 답(`set_brand_character`)으로만 닫는다
CHARACTER_WAIT = "character_wait"
CHARACTER_WAIT_NOTE = "캐릭터 답 대기"
PLAN_STARTED = ("running", "waiting_async", "completed")  # 기획이 이미 캐릭터 없이 받은 상태
CATBOOKS = "catbooks"
CREATIVE_RECORD = re.compile(r"CR-[0-9a-f]{12}")  # 마케팅 제작 기록 번호
CAST_MAX, CAST_ROLE_MAX = 3, 20  # 계약 marketing_brand.MAX_CAST·MAX_CAST_ROLE과 같다


def clean_cast(
    record_id: str | None, cast: list[dict[str, str]] | None
) -> tuple[list[dict[str, str]], bool]:
    """등장 캐릭터 (11/27). (정리한 목록, 맞나). 없으면 ([], True).

    번호는 CR 모양·겹침 없음·최대 3, 역할은 앞뒤 공백을 떼고 20자 이내. record_id를 같이 주면
    첫 번째와 같아야 한다(첫 번째 = 주인공 = brand_character_record_id)."""
    if not cast:
        return [], True
    out: list[dict[str, str]] = []
    for m in cast:
        rid = str((m or {}).get("record_id") or "")
        role = " ".join(str((m or {}).get("role_ko") or "").split())
        if not CREATIVE_RECORD.fullmatch(rid) or len(role) > CAST_ROLE_MAX:
            return [], False
        if any(x["record_id"] == rid for x in out):
            return [], False
        out.append({"record_id": rid, "role_ko": role})
    if len(out) > CAST_MAX or (record_id is not None and record_id != out[0]["record_id"]):
        return [], False
    return out, True


def cast_line(members: list[dict[str, str]]) -> str:
    """"등장 캐릭터 2명(주인공 CR-…, 동료 CR-…)" (대화 문장·단계 메모)."""
    parts = [f"{m['role_ko']} {m['record_id']}".strip() for m in members]
    return f"등장 캐릭터 {len(members)}명({', '.join(parts)})"
# 11/26 영상 제작 단계가 영상을 사람 승인에 올린 뒤(대기·끝남·거절)면 캐릭터를 바꾸지 않는다
VIDEO_SENT = ("held", "completed", "rejected_by_human")
REPLACED_RECORD_KEY = "replaces_creative_record_id"  # 바꾸기 전 캐릭터의 의뢰 기록(CR-…)
REPREPARE_NOTE = "캐릭터를 바꿔 다시 준비한다"
RESET_STEP: dict[str, Any] = {
    "status": "pending",
    "attempts": 0,
    "reason_code": None,
    "result_ref": None,
    "review_id": None,
    "handoff_task_id": None,
    "async_task_id": None,
    "last_signal_at": None,
    "started_at": None,
    "finished_at": None,
}
# 11/27 사람이 실패한 단계부터 다시 시도 (`retry_step`). 앞 단계 결과는 그대로 두고 그 단계와
# 뒤 단계만 되돌린다. 단계마다 3번까지. 다시 시도하면 부서에 새 멱등 키(:retryN)로 보낸다
RETRY_MAX = 3
RETRY_NOTE = "사람이 이 단계부터 다시 시도"
# 다시 시도할 수 있는 단계: 실패 · 영상 거절/기한 지남(취소) · 앞 단계 결과가 없어 막힘
RETRY_REJECTED = ("video_rejected",)
RETRY_SKIPPED = ("no_insight", "no_carry")


def retryable(step: dict[str, Any]) -> bool:
    """사람이 [이 단계부터 다시]를 누를 수 있는 단계인가 (화면 판정과 같은 규칙)."""
    status, code = step.get("status"), step.get("reason_code")
    return (
        status == "failed"
        or (status == "rejected_by_human" and code in RETRY_REJECTED)
        or (status == "skipped" and code in RETRY_SKIPPED)
    )


def step_key(rid: str, skill: str, step_id: str, row: dict[str, Any]) -> str:
    """부서에 보내는 멱등 키. 다시 시도한 단계면 `:retryN` (같은 키면 예전 결과·결정이 돌아온다)."""
    n = int(((row.get("steps") or {}).get(step_id) or {}).get("retry_count") or 0)
    return f"{rid}:{skill}:{step_id}" + (f":retry{n}" if n else "")


def downstream(plan: list[dict[str, Any]], step_id: str) -> list[str]:
    """그 단계와 그 단계에 (이어서) 기대는 뒤 단계들 (계획 순서)."""
    hit = {step_id}
    for spec in plan:
        if set(spec.get("after") or []) & hit:
            hit.add(spec["step_id"])
    return [s["step_id"] for s in plan if s["step_id"] in hit]


Notifier = Callable[[str, str], None]
Escalator = Callable[[str, str, str | None], None]
ScopeCheck = Callable[[dict[str, Any], dict[str, Any]], bool]
# 총괄이 자기 기록을 읽기만 하는 단계(`kind="self"`)의 읽기 함수: skill 이름 → (의뢰 줄) → 결과
SelfRead = Callable[[dict[str, Any]], dict[str, Any]]
OUT_OF_SCOPE = "out_of_scope"


def _dig(out: dict[str, Any], path: str) -> Any:
    """`items.0.campaign_id`처럼 점으로 이은 길을 따라 결과 안쪽 값을 꺼낸다. 없으면 None."""
    cur: Any = out
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def in_catalog(row: dict[str, Any], spec: dict[str, Any]) -> bool:
    """이 단계가 고른 일의 일 목록에 있는 단계(부서·skill)와 같은가. 다르면 시키지 않는다."""
    task = request_tasks.get(row.get("task"))
    return task is not None and any(
        s.dept == spec.get("dept") and s.skill == spec.get("skill") and s.kind == spec.get("kind")
        for s in task.steps
    )


ASYNC_FAILED_NOTE = "긴 작업이 끝나지 못했다"
_OUTCOME_CODE = re.compile(r"^[A-Za-z0-9_]{1,60}$")


def async_failure_note(output: Any) -> str:
    """긴 작업 실패 문구. 부서 결과의 outcome이 코드 모양이면 붙인다(부서 문장은 옮기지 않는다)."""
    outcome = output.get("outcome") if isinstance(output, dict) else None
    if isinstance(outcome, str) and _OUTCOME_CODE.fullmatch(outcome):
        return f"{ASYNC_FAILED_NOTE} ({outcome})"
    return ASYNC_FAILED_NOTE


class RequestRunner:
    def __init__(
        self,
        requests: WorkRequests,
        get_engine: Callable[[], Engine | None],
        *,
        step_timeout: float | None = STEP_TIMEOUT,
        retry_delay: float = RETRY_DELAY,
        notify: Notifier | None = None,
        escalate: Escalator | None = None,
        scope_check: ScopeCheck = in_catalog,
        clock: Callable[[], datetime] = now,
        self_reads: dict[str, SelfRead] | None = None,
    ) -> None:
        # 부서를 부르지 않고 총괄이 읽는 읽기 전용 단계(11/26 롤백 상태).
        # 여기 없는 skill이면 실패로 닫는다
        self._self_reads = self_reads or {}
        self._clock = clock
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
        # 접수 응답보다 먼저 온 긴 작업 알림: (context_id, 작업 번호) → [(종류, 보낸 곳, 데이터)]
        self._early: dict[tuple[str, str], list[tuple[str, str, dict[str, Any]]]] = {}

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
            for sid, step in row["steps"].items():  # 꺼져 있던 동안의 침묵은 세지 않는다
                if step["status"] == "waiting_async":
                    self._update(row["request_id"], sid, last_signal_at=now().isoformat())
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
        if task is not None and self._stale(row, task):  # 일 목록이 바뀐 뒤 시작하는 의뢰
            self._requests.refresh_plan(request_id, request_tasks.plan_of(task))
            row = self._requests.get(request_id) or row
        if task is not None and task.blocked_by:
            self._requests.set_status(
                request_id, "needs_human", f"아직 자동으로 진행할 수 없는 일: {task.blocked_by}"
            )
            return
        self._requests.set_status(request_id, "running", "부서에 시키기 시작했다")
        while True:
            row = self._requests.get(request_id)
            if row is None or row.get("stopped_at"):  # 사람이 멈췄으면 다음 단계로 넘기지 않는다
                return
            ready = ready_steps(row)
            if not ready:
                break
            await asyncio.gather(*(self._step(row, sid) for sid in ready))
        self._finish(request_id)

    @staticmethod
    def _ctx(row: dict[str, Any]) -> dict[str, Any]:
        """입력 규칙이 쓰는 의뢰자 정보: 이름(계정)과 구매자 번호(로그인 계정에서 받은 값)."""
        return {"name": row.get("account"), "buyer_id": row.get("buyer_id")}

    def _stale(self, row: dict[str, Any], task: request_tasks.TaskDef) -> bool:
        """시작 전인데 저장된 계획이 지금 일 목록과 다른가 (옛 계획으로 돌지 않게)."""
        if any(s["status"] != "pending" for s in row["steps"].values()):
            return False
        return self._requests.plan_differs(row["request_id"], request_tasks.plan_of(task))

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
        if spec.get("kind") == "human" and spec.get("skill") == request_tasks.CHARACTER_SKILL:
            current = self._requests.get(rid) or row
            if current["steps"][step_id]["status"] != "pending":
                return  # 시작하기 전에 이미 답이 왔다 (set_brand_character가 닫았다)
            self._update(
                rid,
                step_id,
                status="waiting_human",
                reason_code=CHARACTER_WAIT,
                note=CHARACTER_WAIT_NOTE,
                started_at=stamp(),
            )
            return
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
        if spec.get("kind") == request_tasks.SELF_KIND:
            self._run_self(rid, step_id, spec, row)
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
        carried = None
        if spec.get("takes_carry"):  # 넘길 값이 없으면 승인도 묻지 않고 이 단계를 건너뛴다
            carried = self._carried(row, spec)
            if carried is None and spec.get("on_missing") == "fail":
                self._update(
                    rid,
                    step_id,
                    status="failed",
                    reason_code=NO_CARRY,
                    note="앞 단계가 넘겨야 할 결과가 없어 시킬 수 없다",
                    started_at=stamp(),
                    finished_at=stamp(),
                )
                return
            if carried is None:
                self._update(
                    rid,
                    step_id,
                    status="skipped",
                    reason_code=NO_INSIGHT,
                    note="기획에 넘길 근거(facts)가 없어 이 단계를 건너뛰었다",
                    started_at=stamp(),
                    finished_at=stamp(),
                )
                return
        filled = None
        if spec.get(
            "fill"
        ):  # 고객 문장에서 기간 같은 입력을 채운다. 못 채우면 부서에 보내지 않는다
            filled = request_inputs.fill(
                spec["fill"], row["text"], self._clock(), row["request_id"], self._ctx(row)
            )
            if filled.ask or filled.handoff:
                self._update(
                    rid,
                    step_id,
                    status="failed",
                    reason_code=NO_INPUT,
                    note="필요한 입력(기간 등)을 고객 문장에서 찾지 못해 부서에 보내지 않았다",
                    started_at=stamp(),
                    finished_at=stamp(),
                )
                return
        step = row["steps"][step_id]
        grade = entry.grade or step.get("grade")
        gated = bool(spec.get("human_gate")) or grade == HUMAN_GRADE
        if gated and step.get("reason_code") != APPROVED:  # 사람 승인 없이는 시키지 않는다
            self._update(
                rid,
                step_id,
                status="waiting_human",
                grade=grade,
                reason_code=NEEDS_APPROVAL,
                note="상태를 바꾸는 일이라 사람 승인을 기다린다"
                + (f" ({filled.note})" if filled is not None and filled.note else ""),
                started_at=stamp(),
            )
            return
        payload = carried if carried is not None else self._input_of(row, spec, filled)
        if carried is not None and spec.get("text_key"):  # 넘겨받은 값 + 고객 문장 칸 (11/23)
            payload = {**carried, spec["text_key"]: row["text"]}
        if payload is None:
            # 11/26: 안내문(empty_ref_note)이 있는 단계는 "고를 게 없을 뿐"이라 사람 확인으로
            # 넘기는 실패가 아니라 건너뜀으로 닫는다(예: 올릴 수 있는 의뢰 영상이 아직 없음)
            note = spec.get("empty_ref_note")
            self._update(
                rid,
                step_id,
                status="skipped" if note else "failed",
                reason_code=NO_REF,
                note=note or "앞 단계의 결과 번호가 없어 시킬 수 없다",
                started_at=stamp(),
                finished_at=stamp(),
            )
            return
        # 사람이 화면에서 정한 의뢰 기록 값(번호)만. pass_row는 값만 더한다(준비 단계 표시 아님)
        for key in [*(spec.get("row_input") or []), *(spec.get("pass_row") or [])]:
            if row.get(key):
                payload = {**payload, key: row[key]}
        # 11/26 캐릭터를 바꿔 다시 준비하면 예전 의뢰 기록 번호를 같이 넘긴다(마케팅이 멈춤 표시)
        replaced = row.get(REPLACED_RECORD_KEY)
        if request_tasks.BRAND_CHARACTER_KEY in (spec.get("row_input") or []) and replaced:
            payload = {**payload, REPLACED_RECORD_KEY: replaced}
        self._update(rid, step_id, status="running", grade=grade, started_at=stamp())
        result = await self._call(engine, row, spec, entry.dept, step_id, payload, grade)
        self._apply(rid, step_id, result, spec)

    def _run_self(self, rid: str, step_id: str, spec: dict[str, Any], row: dict[str, Any]) -> None:
        """총괄이 자기 기록을 읽는 읽기 전용 단계. 부서·`execute()`를 부르지 않고 번호만 남긴다."""
        stamp = now().isoformat
        read = self._self_reads.get(spec.get("skill") or "")
        task = request_tasks.get(row.get("task"))
        listed = next(  # 일 목록의 단계가 읽기 등급일 때만(계획 기록에는 등급이 없다)
            (
                st
                for st in (task.steps if task else ())
                if st.kind == request_tasks.SELF_KIND and st.skill == spec.get("skill")
            ),
            None,
        )
        if read is None or listed is None or listed.grade != request_tasks.READ_GRADE:
            self._update(
                rid,
                step_id,
                status="failed",
                reason_code=NO_AGENT,
                note="총괄이 읽을 수 있는 읽기 단계가 아니라 시키지 않았다",
                started_at=stamp(),
                finished_at=stamp(),
            )
            return
        self._update(rid, step_id, status="running", started_at=stamp())
        try:
            out = read(row)
        except Exception:  # noqa: BLE001 - 읽기 실패는 이 단계만 실패로 닫는다
            log.exception("총괄 읽기 단계 실패: %s/%s", rid, step_id)
            self._update(
                rid,
                step_id,
                status="failed",
                note="사건 기록을 읽지 못했다",
                finished_at=stamp(),
            )
            return
        self._complete(rid, step_id, spec, out, "사건 기록 읽기")

    # ---- 부서 호출 ----------------------------------------------------------------
    @staticmethod
    def _input_of(
        row: dict[str, Any], spec: dict[str, Any], filled: request_inputs.Fill | None = None
    ) -> dict[str, Any] | None:
        """부서에 보낼 입력: 일 목록의 고정 입력, 고객 문장 칸(있으면), 앞 단계 결과 번호.

        앞 단계 결과는 번호(`result_ref`)만 넘긴다. 필요한데 없으면 None.
        """
        payload: dict[str, Any] = dict(spec.get("fixed_input") or {})
        if filled is not None:
            payload.update(filled.payload)
        text_key = spec.get("text_key")
        if text_key:  # 부서가 문장을 `input` 칸으로만 읽는 일이다 (문장은 데이터로만 쓰인다)
            payload[text_key] = row["text"]
        key = spec.get("takes_ref")
        if not key:
            return payload
        for before in spec.get("after") or []:
            ref = row["steps"][before].get("result_ref")
            if ref:
                payload[key] = ref
                return payload
        return None

    async def _call(
        self,
        engine: Engine,
        row: dict[str, Any],
        spec: dict[str, Any],
        dept: str,
        step_id: str,
        payload: dict[str, Any],
        grade: str | None = None,
    ) -> AgentResult | str:
        """부서를 부른다. 못 받으면 이유.

        읽기 단계만 실패·취소를 정해진 횟수 다시 부른다. 상태를 바꾸거나 비용이 드는 단계(A·B·C)는
        한 번만 보낸다: 첫 호출이 이미 일을 시작했을 수 있어 다시 보내면 중복·충돌(예: 영상 제작이
        "이미 진행 중"으로 거절)이 난다. 기다리는 시간은 단계의 `timeout_s`, 없으면 기본값이다.
        둘 다 없으면 끊지 않는다(11/25).
        """
        rid = row["request_id"]
        retry = grade == request_tasks.READ_GRADE  # 등급을 모르면 다시 보내지 않는다
        limit = spec.get("timeout_s") or self._step_timeout
        timeout = float(limit) if limit else None
        failed = canceled = attempts = 0
        while True:
            attempts += 1
            self._update(rid, step_id, attempts=attempts)
            req = AgentRequest(
                meta=RequestMeta(
                    context_id=row.get("context_id") or f"ctx_{rid}",
                    token="orchestrator",
                    path=["orchestrator"],
                    deadline=now() + (timedelta(seconds=timeout) if timeout else NO_LIMIT_DEADLINE),
                    idempotency_key=step_key(rid, spec["skill"], step_id, row),
                    **self._tenant_meta(),
                ),
                skill=spec["skill"],
                input=payload,
                untrusted={"request_text": row["text"]},  # 고객 글은 데이터 칸으로만
            )
            if timeout is None:
                result = await engine.caller(dept, req)
            else:
                try:
                    result = await asyncio.wait_for(engine.caller(dept, req), timeout=timeout + 5)
                except TimeoutError:
                    return "부서 응답 시간 초과"
            if retry and result.status == TaskStatus.CANCELED and canceled < MAX_CANCELED_RETRIES:
                canceled += 1
                await asyncio.sleep(self._retry_delay)
                continue
            retryable = result.output.get("retryable") is not False
            if (
                retry
                and result.status == TaskStatus.FAILED
                and retryable
                and failed < MAX_FAILED_RETRIES
            ):
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
            self._complete(rid, step_id, spec, out, outcome)
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
                    last_signal_at=done,
                    note="부서가 긴 작업으로 받아 진행 중",
                )
                self._drain_early(rid, step_id, spec, task_id)
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
        elif not self._video_gate(rid, step_id, {**out, "outcome": outcome}):
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
        ref = _dig(out, key) if key else None
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

    # ---- 긴 작업 알림 ---------------------------------------------------------------
    def _carried(self, row: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
        """앞 단계가 다음 단계로 넘기려고 둔 값. 없으면 None."""
        source = [spec["carry_from"]] if spec.get("carry_from") else spec.get("after") or []
        for before in source:
            value = self._requests.get_carry(row["request_id"], before)
            if value:
                return value
        return None

    def _complete(
        self, rid: str, step_id: str, spec: dict[str, Any], out: dict[str, Any], outcome: str
    ) -> None:
        """단계를 완료로 닫는다. 결과 번호를 읽고, 다음 단계가 통째로 받을 값(`carries`)을 둔다."""
        key = spec.get("carries")
        keys = spec.get("carry_keys") or []
        if keys:  # 결과의 여러 칸을 한 묶음으로 (하나라도 비면 넘기지 않는다)
            bundle = {k: out.get(k) for k in keys}
            self._requests.set_carry(rid, step_id, bundle if all(bundle.values()) else None)
        elif key:
            value = out.get(key)
            ok = isinstance(value, dict) and bool(value)
            if key == "insight":  # 기획 입력은 근거(facts)가 있어야 쓸 수 있다
                ok = ok and bool(value.get("facts"))
            self._requests.set_carry(rid, step_id, value if ok else None)
        # 넘겨받은 값은 쓰고 나면 지운다. 11/26 영상 초안 준비(캐릭터를 받는 단계)는 기획 결과를
        # 둔다: 영상 요청 전에 캐릭터를 바꾸면 같은 기획으로 다시 준비한다(`_reprepare`)
        keep = request_tasks.BRAND_CHARACTER_KEY in (spec.get("row_input") or [])
        if spec.get("takes_carry") and not keep:
            source = [spec["carry_from"]] if spec.get("carry_from") else spec.get("after") or []
            for before in source:
                self._requests.set_carry(rid, before, None)
                # 영상까지 끝났으면 준비 단계가 남겨 둔 기획 결과도 지운다(더 다시 준비하지 않음)
                row = self._requests.get(rid) or {}
                prev = next((p for p in row.get("plan") or [] if p["step_id"] == before), None)
                if prev and request_tasks.BRAND_CHARACTER_KEY in (prev.get("row_input") or []):
                    kept = [prev["carry_from"]] if prev.get("carry_from") else prev.get("after")
                    for origin in kept or []:
                        self._requests.set_carry(rid, origin, None)
        self._update(
            rid,
            step_id,
            status="completed",
            note=outcome,
            result_ref=self._ref_of(out, spec),
            finished_at=now().isoformat(),
        )

    def _context_of(self, row: dict[str, Any]) -> str:
        return row.get("context_id") or f"ctx_{row['request_id']}"

    def receive_async(
        self, kind: str, source: str, data: dict[str, Any], context_id: str | None
    ) -> str:
        """부서의 `task.progress`·`task.completed`를 기다리는 단계에 반영한다.

        돌려주는 값: progressed · resumed(완료를 반영하고 다음 단계를 이어 시킴) ·
        ignored(보낸 곳이나 skill이 맡긴 부서와 다름: 위조 방지) · kept(단계가 아직 대기에 안 올라
        보관) · unmatched(이미 끝났거나 맞는 의뢰·단계가 없음).
        """
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not context_id:
            return "unmatched"
        row = next(
            (r for r in self._requests.open_requests() if self._context_of(r) == context_id), None
        )
        if row is None:
            return "unmatched"
        steps = row["steps"]
        for sid, step in steps.items():
            if step["status"] == "waiting_async" and step.get("async_task_id") == task_id:
                spec = next(s for s in row["plan"] if s["step_id"] == sid)
                return self._apply_async(row["request_id"], sid, spec, kind, source, data)
        if any(s.get("async_task_id") == task_id for s in steps.values()):
            return "unmatched"  # 이미 끝난 작업의 되풀이
        if not any(s["status"] in ("pending", "running") for s in steps.values()):
            return "unmatched"
        key = (context_id, task_id)
        if key not in self._early and len(self._early) >= MAX_EARLY_KEYS:
            return "unmatched"
        kept = self._early.setdefault(key, [])
        if len(kept) >= MAX_EARLY_EACH:
            return "unmatched"
        kept.append((kind, source, data))
        return "kept"

    def _drain_early(self, rid: str, step_id: str, spec: dict[str, Any], task_id: str) -> None:
        """접수 응답보다 먼저 온 알림이 있으면 대기에 오른 단계에 이어서 반영한다."""
        row = self._requests.get(rid)
        if row is None:
            return
        for kind, source, data in self._early.pop((self._context_of(row), task_id), []):
            step = (self._requests.get(rid) or row)["steps"][step_id]
            if step["status"] != "waiting_async":
                break
            self._apply_async(rid, step_id, spec, kind, source, data)

    def _apply_async(
        self,
        rid: str,
        step_id: str,
        spec: dict[str, Any],
        kind: str,
        source: str,
        data: dict[str, Any],
    ) -> str:
        if source != spec.get("dept") or data.get("skill") != spec.get("skill"):
            log.warning("긴 작업 알림의 보낸 곳·skill이 맡긴 부서와 달라 무시: %s/%s", rid, step_id)
            return "ignored"
        if (self._requests.get(rid) or {}).get("stopped_at"):  # 사람이 멈춘 뒤 온 결과
            return "unmatched"
        stamp = now().isoformat()
        if kind == "progress":  # 부서가 쓴 문장은 옮기지 않고 진행률만 둔다
            percent = data.get("percent")
            note = "부서가 긴 작업으로 받아 진행 중"
            if isinstance(percent, int):
                note += f" ({percent}%)"
            self._update(rid, step_id, last_signal_at=stamp, note=note)
            return "progressed"
        if data.get("status") == "done":
            out = data.get("output")
            self._complete(rid, step_id, spec, out if isinstance(out, dict) else {}, "긴 작업 완료")
        elif not self._video_gate(rid, step_id, data.get("output")):
            # 11/25 부서가 준 이유 코드(영문·숫자·_ 만)를 붙여 대화방 카드에서 원인을 보게 한다
            self._update(
                rid,
                step_id,
                status="failed",
                note=async_failure_note(data.get("output")),
                finished_at=stamp,
            )
        self.start(rid)
        return "resumed"

    def expire_silent(self, max_silence: float = ASYNC_SILENCE) -> list[str]:
        """알림이 `max_silence`초 넘게 없는 긴 작업 단계를 응답 끊김으로 실패로 닫고 마무리한다."""
        expired: list[str] = []
        for row in self._requests.open_requests():
            rid = row["request_id"]
            for sid, step in row["steps"].items():
                last = step.get("last_signal_at") or step.get("started_at")
                if step["status"] != "waiting_async" or not last:
                    continue
                if (now() - datetime.fromisoformat(last)).total_seconds() < max_silence:
                    continue
                self._update(
                    rid,
                    sid,
                    status="failed",
                    reason_code=ASYNC_SILENT,
                    note=f"긴 작업 알림이 {max_silence:.0f}초 넘게 없어 끝내지 못했다",
                    finished_at=now().isoformat(),
                )
                if rid not in expired:
                    expired.append(rid)
        for rid in expired:
            self.start(rid)
        return expired

    async def watch_async(
        self, interval: float = WATCH_INTERVAL, max_silence: float = ASYNC_SILENCE
    ) -> None:
        """긴 작업 알림이 끊긴 단계를 주기적으로 닫는다 (서버가 도는 동안 계속)."""
        while True:
            await asyncio.sleep(interval)
            try:
                self.expire_silent(max_silence)
            except Exception:  # noqa: BLE001 - 점검 실패가 서버를 멈추지 않게 한다
                log.exception("긴 작업 응답 끊김 점검 실패")
            try:  # 승인 결정 알림을 놓쳤을 때를 대비해 영상 승인 대기를 안전 관문에 되묻는다
                await self.recheck_videos()
            except Exception:  # noqa: BLE001
                log.exception("영상 승인 대기 점검 실패")

    # ---- 회사 캐릭터 홍보 영상: 영상 승인 대기 (11/23) -------------------------------------
    def _tenant_meta(self) -> dict[str, str]:
        """부서를 부를 때 회사 칸.

        캣북스(지금 기본)면 비워 받는 쪽 기본값(shop-demo)을 그대로 둔다."""
        company = self._requests.company_id
        return {"tenant_id": company} if company and company != CATBOOKS else {}

    def _video_gate(self, rid: str, step_id: str, out: Any) -> bool:
        """마케팅이 영상을 사람 승인에 올렸거나(보류) 사람이 거절했으면 단계에 옮기고 True.

        보류는 실패로 치지 않고 `held`(영상 승인 대기)로 두며 `review_id`를 남긴다. 거절은
        `rejected_by_human`이고 의뢰는 사람 확인(needs_human)으로 간다(`_finish`)."""
        out = out if isinstance(out, dict) else {}
        outcome = out.get("outcome")
        if outcome == VIDEO_HELD:
            review = out.get("review_id")
            ok = isinstance(review, str) and REF_PATTERN.fullmatch(review) is not None
            self._update(
                rid,
                step_id,
                status="held",
                reason_code=VIDEO_APPROVAL,
                review_id=review if ok else None,
                note=VIDEO_WAIT_NOTE,
            )
            return True
        if outcome == VIDEO_REJECTED:
            self._update(
                rid,
                step_id,
                status="rejected_by_human",
                reason_code=VIDEO_REJECTED,
                note=VIDEO_REJECT_NOTE,
                finished_at=now().isoformat(),
            )
            return True
        return False

    def set_brand_character(
        self, request_id: str, record_id: str | None, by: str | None = None
    ) -> str:
        """홍보 영상 의뢰에 쓸 회사 대표 캐릭터를 정한다.

        돌려주는 값: ok · missing · invalid(번호 모양이 아님) · not_video · started.
        자세한 규칙은 `answer_brand_character`."""
        return self.answer_brand_character(request_id, record_id, by)[0]

    def answer_brand_character(
        self, request_id: str, record_id: str | None, by: str | None = None,
        cast: list[dict[str, str]] | None = None,
    ) -> tuple[str, bool]:  # fmt: skip
        """회사 캐릭터 답을 받는다. (결과, plan_outdated).

        - 캐릭터 단계(11/24)가 기다리는 중이면 답(CR-… 또는 None=없어요)을 적고 단계를 완료로
          닫은 뒤 다음 단계(데이터분석)를 시작한다. 답이 없으면 기다린다(자동으로 넘기지 않는다).
        - 답한 뒤에도 영상 준비 단계를 시작하기 전까지는 다시 바꿀 수 있다(그 뒤면 `started`).
          기획이 이미 시작됐거나 끝난 뒤 바꾸면 plan_outdated=True(기획서는 예전 캐릭터 기준).
        - 캐릭터 단계가 없는 옛 의뢰는 지금처럼 적기만 한다.
        - 11/27 cast: 등장 캐릭터 [{record_id, role_ko}] 최대 3(첫 번째 = 주인공 = record_id).
          사람이 화면에서 정한 값만 받는다. 한 명이면 지금과 같은 기록.
        """
        members, ok = clean_cast(record_id, cast)
        if not ok:
            return "invalid", False
        if members:
            record_id = members[0]["record_id"]
        if record_id is not None and not CREATIVE_RECORD.fullmatch(record_id):
            return "invalid", False
        row = self._requests.get(request_id)
        if row is None:
            return "missing", False
        key = request_tasks.BRAND_CHARACTER_KEY
        prepare = next((p for p in row["plan"] if key in (p.get("row_input") or [])), None)
        if prepare is None:
            return "not_video", False
        step = row["steps"].get(prepare["step_id"]) or {}
        closed = row["status"] not in OPEN_STATES or bool(row.get("stopped_at"))
        if closed:
            return "started", False
        if step.get("status") != "pending":
            # 11/26 영상 요청(사람 승인 요청) 전까지는 바꾼다: 같은 의뢰를 새 캐릭터로 다시 준비
            return self._reprepare(row, prepare, record_id, by, members)
        outdated = any(
            p.get("dept") == "planning"
            and (row["steps"].get(p["step_id"]) or {}).get("status") in PLAN_STARTED
            for p in row["plan"]
        )
        self._requests.set_brand_character(request_id, record_id, by, members)
        said = f"캐릭터 {record_id}로 할게요." if record_id else "캐릭터 없이 할게요."
        if len(members) >= 2:
            said = f"{cast_line(members)}로 할게요."
        char = next(
            (p for p in row["plan"] if p.get("skill") == request_tasks.CHARACTER_SKILL), None
        )
        char_step = (row["steps"].get(char["step_id"]) or {}) if char else {}
        if char is not None and char_step.get("status") in ("pending", "waiting_human"):
            who = f" ({by})" if by else ""
            self._update(
                request_id,
                char["step_id"],
                status="completed",
                note=f"{said[:-1]}{who}",
                started_at=char_step.get("started_at") or now().isoformat(),
                finished_at=now().isoformat(),
            )
            nxt = next((p for p in row["plan"] if char["step_id"] in (p.get("after") or [])), None)
            dept = request_tasks.dept_label(nxt.get("dept") if nxt else None)
            self._tell(row, f"{said} {dept}에 시켰어요.")
            self.start(request_id)
        else:
            self._tell(row, said)
        return "ok", outdated

    def _reprepare(
        self, row: dict[str, Any], prepare: dict[str, Any], record_id: str | None, by: str | None,
        members: list[dict[str, str]] | None = None,
    ) -> tuple[str, bool]:  # fmt: skip
        """영상 초안 준비가 시작된 뒤 캐릭터를 바꾼다 (11/26). (결과, plan_outdated=True).

        - 영상 제작 단계가 영상을 사람 승인에 올렸으면(held·completed·rejected_by_human)
          `video_requested`.
        - 준비 단계가 도는 중이면 `busy`(잠깐 뒤 다시). 기획 결과(carry)가 없으면 `no_plan`.
        - 아니면 새 캐릭터를 적고 준비·제작 단계를 처음 상태로 되돌려 다시 시킨다(`reprepared`).
          마케팅은 새 캐릭터의 의뢰 기록에 시트·콘티·키프레임을 다시 준비하고(그림 크레딧을
          다시 쓴다), 예전 기록(`replaces_creative_record_id`)은 멈춤 표시를 해 그 기록으로는
          영상을 요청하지 않는다. 예전 제작 단계의 긴 작업 결과는 작업 번호가 달라 반영되지 않는다.
        """
        rid = row["request_id"]
        steps = row["steps"]
        make = next((p for p in row["plan"] if prepare["step_id"] in (p.get("after") or [])), None)
        make_step = (steps.get(make["step_id"]) or {}) if make else {}
        if make_step.get("status") in VIDEO_SENT:
            return "video_requested", False
        prep = steps.get(prepare["step_id"]) or {}
        if prep.get("status") == "running":
            return "busy", False
        source = prepare.get("carry_from") or next(iter(prepare.get("after") or []), None)
        if not source or not self._requests.get_carry(rid, source):
            return "no_plan", False
        old = prep.get("result_ref")
        self._requests.set_brand_character(rid, record_id, by, members)
        self._requests.set_replaced_record(
            rid, old if isinstance(old, str) and CREATIVE_RECORD.fullmatch(old) else None
        )
        for spec in (prepare, make):
            if spec is not None:
                self._update(rid, spec["step_id"], **RESET_STEP, note=REPREPARE_NOTE)
        said = f"캐릭터 {record_id}로" if record_id else "캐릭터 없이"
        if members and len(members) >= 2:
            said = f"{cast_line(members)}로"
        self._tell(row, f"{said} 바꿔 콘티·키프레임을 다시 준비할게요(그림 크레딧을 다시 써요).")
        self._requests.set_status(rid, "running", "캐릭터를 바꿔 영상 초안을 다시 준비한다")
        self.start(rid)
        return "reprepared", True

    def held_videos(self, review_id: str | None = None) -> list[tuple[str, str, str]]:
        """영상 승인 대기 단계: (의뢰, 단계, review_id). `review_id`를 주면 그것만."""
        found = []
        for row in self._requests.open_requests():
            if row.get("stopped_at"):
                continue
            for sid, step in row["steps"].items():
                rev = step.get("review_id")
                if step["status"] != "held" or step.get("reason_code") != VIDEO_APPROVAL:
                    continue
                if rev and (review_id is None or rev == review_id):
                    found.append((row["request_id"], sid, rev))
        return found

    def resume_video(self, request_id: str, step_id: str) -> bool:
        """승인 결정이 난 영상 단계를 다시 보낸다(같은 멱등 키·같은 입력).

        마케팅이 같은 키로 안전 관문에 다시 묻고 승인이면 그때 만든다(관문 밖에서 만들지 않는다)."""
        row = self._requests.get(request_id)
        step = (row or {}).get("steps", {}).get(step_id) or {}
        if row is None or row["status"] not in OPEN_STATES or row.get("stopped_at"):
            return False
        if step.get("status") != "held" or step.get("reason_code") != VIDEO_APPROVAL:
            return False
        self._update(
            request_id, step_id, status="pending", note="영상 승인 결정을 받아 다시 확인한다"
        )
        self.start(request_id)
        return True

    async def recheck_videos(self, review_id: str | None = None) -> int:
        """영상 승인 대기 단계를 안전 관문에 되묻는다(engine.settle_review).

        결정이 났으면(승인·거절·만료) 그 단계를 다시 보낸다. 아직이면 그대로 둔다."""
        engine = self._get_engine()
        if engine is None:
            return 0
        resumed = 0
        for rid, sid, rev in self.held_videos(review_id):
            if await engine.settle_review(rev, None) is None:
                continue  # 아직 사람이 정하지 않았다
            resumed += self.resume_video(rid, sid)
        return resumed

    # ---- 마무리 ------------------------------------------------------------------
    def _finish(self, rid: str) -> None:
        """남은 단계를 정리하고 의뢰 상태를 정한다 (끝남·실패·기다림)."""
        row = self._requests.get(rid)
        if row is None or row.get("stopped_at"):  # 사람이 삭제한 의뢰: 상태(rejected)를 그대로 둔다
            return
        self._skip_blocked(row)
        row = self._requests.get(rid)
        if row is None:
            return
        states = [s["status"] for s in row["steps"].values()]
        waits = [s for s in row["steps"].values() if s["status"] in ("held", "waiting_human")]
        if waits and all(s.get("reason_code") == CHARACTER_WAIT for s in waits):
            # 캐릭터 답 대기: 접수 안내가 이미 물었다 (같은 waiting 상태, 문장만 다르다)
            self._requests.set_status(rid, "waiting", "회사 캐릭터 답을 기다린다")
        elif waits:
            self._requests.set_status(rid, "waiting", "승인이나 사람 확인을 기다린다")
            self._tell(row, "확인이 필요한 단계가 있어 담당자의 확인을 기다리고 있어요.")
        elif "waiting_async" in states:  # 부서가 긴 작업을 하는 중: 알림이 오면 이어 간다
            self._requests.set_status(rid, "running", "부서의 긴 작업이 끝나기를 기다린다")
            self._tell(row, "부서가 분석을 진행 중이에요. 끝나면 이어서 진행할게요.")
        elif "failed" in states:  # 재시도 뒤에도 끝내지 못했다: 멈추지 않고 사람에게 넘긴다
            self._requests.set_status(rid, "needs_human", "단계가 끝나지 못해 사람에게 넘겼다")
            self._tell(row, "일부 단계를 끝내지 못했어요. 담당자가 확인할 거예요.")
            failed = next(k for k, v in row["steps"].items() if v["status"] == "failed")
            if self.escalate is not None:
                self.escalate(rid, "step_failed", failed)
        elif any(v["reason_code"] == NO_INSIGHT for v in row["steps"].values()):
            self._requests.set_status(
                rid, "needs_human", "기획에 넘길 근거가 없어 영상 제작안을 만들지 못했다"
            )
            self._tell(row, "영상 제작안을 만들 근거가 부족해 담당자가 확인할 거예요.")
            skipped = next(k for k, v in row["steps"].items() if v["reason_code"] == NO_INSIGHT)
            if self.escalate is not None:
                self.escalate(rid, "step_failed", skipped)
        elif any(v["reason_code"] == VIDEO_REJECTED for v in row["steps"].values()):
            self._requests.set_status(rid, "needs_human", VIDEO_REJECT_NOTE)
            self._tell(row, "영상 생성을 거절해서 담당자가 확인할 거예요.")
        elif empty := next(
            (
                v["note"]
                for v in row["steps"].values()
                if v["status"] == "skipped" and v.get("reason_code") == NO_REF and v.get("note")
            ),
            None,
        ):  # 11/26: 고를 게 없을 뿐(예: 올릴 수 있는 의뢰 영상이 아직 없음) — 사람 확인 아님
            self._requests.set_status(rid, "done", empty)
            self._tell(row, empty)
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
        - 재시작으로 멈춤(`restart_unconfirmed`, 11/24): 보냈는지 몰라 멈춘 단계. 승인하면 같은
          멱등 키로 다시 보낸다(pending). 거절하면 `rejected_by_human`.
        어느 쪽이든 뒤 단계는 앞 단계가 성공해야만 시킨다.
        """
        row = self._requests.get(request_id)
        step = (row or {}).get("steps", {}).get(step_id)
        if row is None or step is None:
            return "missing"
        if step["status"] != "waiting_human" or step["reason_code"] not in (
            HUMAN_CHECK,
            NEEDS_APPROVAL,
            RESTART_UNCONFIRMED,
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
        elif step["reason_code"] == RESTART_UNCONFIRMED:
            # running까지 갔던 단계라 사람 승인이 필요한 일이면 이미 승인을 받았다
            # (APPROVED로 둬 다시 묻지 않는다)
            self._update(
                request_id,
                step_id,
                status="pending",
                reason_code=APPROVED,
                note=f"재시작으로 멈춘 단계를 사람이 다시 보내기로 했다{who}",
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
        """사람이 일을 골라 의뢰를 다시 돌린다.

        돌려주는 값: ok · missing · unknown_task · busy · stopped.
        계획과 단계 진행은 새로 시작한다(이전 선택은 기록에 남는다). 상태를 바꾸는 단계는 이번에도
        승인을 거친다. 부서에 보내는 중이거나 기다리는 단계가 있으면(`busy`) 바꾸지 않는다.
        사람이 삭제해 멈춘 의뢰(`stopped_at`)는 되돌려 숨김을 풀었어도 다시 돌리지 않는다
        (`stopped`). 고친 의뢰문으로 새로 접수하는 길(`edit`)만 있다.
        """
        row = self._requests.get(request_id)
        if row is None:
            return "missing"
        if row.get("stopped_at"):
            return "stopped"
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

    def retry_step(self, request_id: str, step_id: str, by: str | None = None) -> str:
        """사람이 실패한 단계부터 다시 시도한다 (11/27).

        돌려주는 값: ok · missing · not_failed · busy · stopped · limit.
        그 단계와 뒤 단계만 시작 전으로 되돌린다. 앞 단계(완료·결과 번호·넘길 값)와 계획은
        그대로라 보고서·제안·제작 기록을 다시 만들지 않는다. 승인이 필요한 단계는 이번에도 사람
        승인을 기다리고, 부서에는 새 멱등 키(:retryN)로 보내 안전 관문에 다시 묻는다.
        """
        row = self._requests.get(request_id)
        if row is None or step_id not in (row.get("steps") or {}):
            return "missing"
        if row.get("stopped_at"):
            return "stopped"
        step = row["steps"][step_id]
        if not retryable(step):
            return "not_failed"
        busy = request_id in self._running or any(
            s["status"] in ("running", "held", "waiting_async", "waiting_human")
            for s in row["steps"].values()
        )
        if busy:
            return "busy"
        count = int(step.get("retry_count") or 0) + 1
        if count > RETRY_MAX:
            return "limit"
        who = by or "사람"
        for sid in downstream(row["plan"], step_id):
            self._update(request_id, sid, **RESET_STEP, note=RETRY_NOTE if sid == step_id else "")
        self._update(request_id, step_id, retry_count=count)
        number = next(i for i, s in enumerate(row["plan"], 1) if s["step_id"] == step_id)
        line = f"사람이 {number}단계를 다시 시도 ({who}, {count}/{RETRY_MAX})"
        self._requests.note(request_id, line)
        self._requests.set_status(request_id, "running", "실패한 단계부터 다시 시킨다")
        self.start(request_id)
        return "ok"

    # ---- 의뢰 삭제(멈추고 숨김)·의뢰문 수정 (11/23) -------------------------------------
    def hide(self, request_id: str, by: str | None = None) -> str:
        """사람이 의뢰를 삭제한다 = 멈추고 숨김. 돌려주는 값: ok · missing.

        끝나지 않은 의뢰(받음·일 고름·진행·기다림·사람 확인 필요)면 남은 단계
        (시작 전·승인·사람 확인·긴 작업 대기)를 `skipped`("사람이 의뢰를 삭제함")로 닫고
        의뢰는 `rejected`. 사람 확인 거절과 같은 길이고 이유만 다르다.
        이미 부서에 맡긴 긴 작업은 취소하지 않는다(취소 계약 없음).
        결과가 와도 다음 단계로 넘기지 않는다(`stopped_at`). 끝난 의뢰는 숨기기만 한다.
        """
        row = self._requests.get(request_id)
        if row is None:
            return "missing"
        if row["status"] in (*OPEN_STATES, "needs_human") and not row.get("stopped_at"):
            self._requests.mark_stopped(request_id)
            who = f" ({by})" if by else ""
            done = now().isoformat()
            for sid, step in row["steps"].items():
                if step["status"] in ("pending", *STEP_WAITING):
                    self._update(request_id, sid, status="skipped",
                                 note=f"{HIDE_NOTE}{who}", finished_at=done)  # fmt: skip
            self._requests.set_status(request_id, "rejected", f"{HIDE_NOTE}{who}")
        self._requests.hide(request_id, by)
        return "ok"

    def unhide(self, request_id: str, by: str | None = None) -> str:
        """숨김만 되돌린다 (멈춘 단계·상태는 그대로). 돌려주는 값: ok · missing."""
        if self._requests.get(request_id) is None:
            return "missing"
        self._requests.unhide(request_id, by)
        return "ok"

    async def edit(
        self, request_id: str, text: str, intake: Any, llm: Any, by: str | None = None
    ) -> tuple[str, str | None]:
        """의뢰문 수정 = 고쳐서 다시 접수. (결과, 의뢰 번호).

        결과: ok(새 의뢰 번호) · missing · same(바뀐 내용 없음) · duplicate(같은 의뢰가 이미 있음,
        그 번호) · limited(하루 접수 한도) · rejected(고친 글이 접수할 수 없는 일, 새 거절 기록
        번호) · not_request(의뢰로 접수되지 않음).
        순서: 대화 접수와 같은 함수(`RequestIntake.receive`, 분류·되묻기 그대로)로 새 의뢰를 먼저
        만든다. 접수 결과가 **새 의뢰가 생긴 경우**(`NEW_REQUEST_OUTCOMES`, 허용 목록)일 때만 옛
        의뢰를 삭제(멈추고 숨김)하고 둘을 잇는다. 그 밖의 결과·예외는 모두 옛 의뢰를 그대로 둔다.
        """
        row = self._requests.get(request_id)
        if row is None:
            return "missing", None
        if " ".join(text.split()) == " ".join((row.get("text") or "").split()):
            return "same", None
        result = await intake.receive(row.get("account"), text, llm,
                                      thread_id=row.get("thread_id"),
                                      buyer_id=row.get("buyer_id"))  # fmt: skip
        new_id = result.request_id
        outcome = result.outcome if result.handled else None
        if outcome in ("duplicate", "limited", "rejected"):  # 옛 의뢰·기존 의뢰 모두 그대로
            return outcome, new_id
        if outcome not in NEW_REQUEST_OUTCOMES or not new_id or new_id == request_id:
            return "not_request", None
        self.hide(request_id, by)
        self._requests.link_replacement(request_id, new_id)
        # 새 의뢰 번호를 옛 의뢰의 대화에 남긴다("[의뢰 wr_새번호] …"). 화면은 그 줄 아래에
        # 진행 카드를 붙인다. 알리지 못해도 수정은 성공이다(_tell 규칙)
        new_row = self._requests.get(new_id)
        if new_row is not None:
            self._tell(new_row, EDIT_TOLD.format(old=request_id))
        return "ok", new_id

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
