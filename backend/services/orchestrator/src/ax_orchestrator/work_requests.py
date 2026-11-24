"""의뢰 기록: 고객사 담당자가 대화로 한 의뢰를 총괄이 저장하고, 재시작해도 이어 갈 수 있게 한다.

이 단계는 기록과 이어가기 정책까지다. 의뢰 접수(대화), 일 고르기(분류 LLM), 부서에 시키기는
이후 단계에서 이 기록 위에 붙인다.

- 의뢰 하나는 한 줄(dict)이다: 의뢰자(`account`), 문장 원문, 고른 일(`task`)·확신도·이유,
  계획(`plan`: 단계 목록과 선행 단계), 단계별 진행(`steps`), 상태(`status`), 로그.
- 장애 이벤트와 달리 재시작 때 "중단됨"으로 닫지 않는다(`restore`).
  대신 단계별로 정한 대로 이어 간다.
  - 끝난 단계(`completed`·`failed`·`skipped`·`rejected_by_human`)와
    아직 안 시작한 단계(`pending`)는 그대로 둔다.
  - 승인·사람·긴 작업을 기다리던 단계(`held`·`waiting_human`·`waiting_async`)는 저장해 둔 번호
    (`review_id`·`handoff_task_id`·`async_task_id`)로 계속 기다린다.
  - 부서에 보내던 중이던 단계(`running`)는 읽기 일(`grade`가 `read`)이면 다시 시키고(`pending`),
    상태를 바꾸는 일이면 이미 처리됐을 수 있어 다시 보내지 않는다. 사람 확인 대기(`waiting_human`,
    이유 `restart_unconfirmed`)로 둔다.
- 부서가 돌려준 큰 결과는 여기 두지 않고 결과 번호(`result_ref`)만 둔다.
- 저장소(`IncidentStore`, `ORCHESTRATOR_DB`)를 주면 파일에도 적어 다시 켜도 남는다.
"""

from __future__ import annotations

import copy
import logging
import uuid
from typing import TYPE_CHECKING, Any

from ax_orchestrator.models import now

if TYPE_CHECKING:
    from ax_orchestrator.store import IncidentStore

# 의뢰 상태: 받음 → 일 고름 → 진행 중 ⇄ 기다림 → 끝남·실패·거절. 사람이 봐야 하면 needs_human
OPEN_STATES = ("received", "classified", "running", "waiting")
CLOSED_STATES = ("done", "failed", "rejected", "needs_human")
STEP_DONE = ("completed", "failed", "skipped", "rejected_by_human")
STEP_WAITING = ("held", "waiting_human", "waiting_async")
READ_GRADE = "read"
RESTART_UNCONFIRMED = "restart_unconfirmed"
RESTART_NOTE = "재시작으로 보냄 여부를 알 수 없어 다시 보내지 않았다 (사람이 확인 필요)"
REQUEUE_NOTE = "재시작으로 중단돼 다시 시킨다 (읽기 일)"
MAX_TEXT = 2000
MAX_REQUESTS = 1000

log = logging.getLogger("orchestrator.work_requests")


def new_request_id() -> str:
    return f"wr_{uuid.uuid4().hex[:10]}"


def _blank_step() -> dict[str, Any]:
    return {
        "status": "pending",
        "attempts": 0,
        "grade": None,
        "note": "",
        "reason_code": None,
        "result_ref": None,
        "review_id": None,
        "handoff_task_id": None,
        "async_task_id": None,
        "last_signal_at": None,  # 긴 작업을 받아 간 뒤 마지막으로 소식을 들은 때
        "started_at": None,
        "finished_at": None,
    }


def settle_step(step: dict[str, Any]) -> str | None:
    """재시작 뒤 단계 하나를 이어 갈 수 있는 상태로 정리한다 (제자리에서 고친다).

    돌려주는 값: `requeued`(다시 시킴) · `unconfirmed`(보냈는지 몰라 사람 확인 대기) · None(그대로).
    """
    if step.get("status") != "running":
        return None
    if step.get("grade") == READ_GRADE:
        step["status"] = "pending"
        step["note"] = REQUEUE_NOTE
        return "requeued"
    step["status"] = "waiting_human"
    step["reason_code"] = RESTART_UNCONFIRMED
    step["note"] = RESTART_NOTE
    return "unconfirmed"


def ready_steps(row: dict[str, Any]) -> list[str]:
    """지금 시킬 수 있는 단계: 안 시작했고 선행 단계가 모두 성공으로 끝난 것 (계획 순서)."""
    steps = row.get("steps") or {}
    ready: list[str] = []
    for spec in row.get("plan") or []:
        sid = spec["step_id"]
        if (steps.get(sid) or {}).get("status") != "pending":
            continue
        if all((steps.get(a) or {}).get("status") == "completed" for a in spec.get("after") or []):
            ready.append(sid)
    return ready


def settle_request(row: dict[str, Any]) -> dict[str, list[str]]:
    """재시작 뒤 의뢰 하나를 정리한다. 열려 있지 않으면 건드리지 않는다.

    돌려주는 값: {"requeued": [...], "unconfirmed": [...]} 단계 번호들.
    """
    result: dict[str, list[str]] = {"requeued": [], "unconfirmed": []}
    if row.get("status") not in OPEN_STATES:
        return result
    for sid, step in (row.get("steps") or {}).items():
        action = settle_step(step)
        if action == "requeued":
            result["requeued"].append(sid)
        elif action == "unconfirmed":
            result["unconfirmed"].append(sid)
    if result["unconfirmed"]:
        row["status"] = "waiting"
    ids = result["requeued"] + result["unconfirmed"]
    if ids:
        row.setdefault("log", []).append(
            {"at": now().isoformat(), "message": f"재시작 뒤 단계를 정리했다: {', '.join(ids)}"}
        )
    return result


class WorkRequests:
    def __init__(self, store: IncidentStore | None = None) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._store = store

    @property
    def company_id(self) -> str | None:
        """이 의뢰 기록의 회사(저장소 회사 칸). 저장소가 없으면 None (11/23)."""
        return self._store.company_id if self._store is not None else None

    # ---- 저장 -----------------------------------------------------------------
    def restore(self) -> dict[str, int]:
        """저장된 의뢰를 올리고 열려 있던 것을 이어 갈 수 있게 정리한다.

        돌려주는 값: {"restored": 올린 수, "open": 열려 있는 수, "requeued": 다시 시킬 단계 수,
        "unconfirmed": 사람 확인으로 둔 단계 수}. 실제로 이어서 시키는 일은 의뢰 실행이 한다.
        """
        counts = {"restored": 0, "open": 0, "requeued": 0, "unconfirmed": 0}
        if self._store is None:
            return counts
        for row in self._store.load_requests():
            rid = row.get("request_id")
            if not isinstance(rid, str):
                continue
            if row.get("status") in OPEN_STATES:
                changed = settle_request(row)
                counts["open"] += 1
                counts["requeued"] += len(changed["requeued"])
                counts["unconfirmed"] += len(changed["unconfirmed"])
                if changed["requeued"] or changed["unconfirmed"]:
                    self._save(row)
            self._rows[rid] = row
            counts["restored"] += 1
        return counts

    def _save(self, row: dict[str, Any]) -> None:
        row["updated_at"] = now().isoformat()
        self._rows[row["request_id"]] = row
        if self._store is None:
            return
        try:
            self._store.save_request(row["request_id"], row["created_at"], row)
        except Exception:  # noqa: BLE001 - 저장 실패가 접수를 막지 않게 한다 (로그에 남는다)
            log.exception("의뢰 저장 실패: %s", row["request_id"])

    # ---- 만들기·고치기 ----------------------------------------------------------
    def create(self, account: str, text: str, *, request_id: str | None = None) -> dict[str, Any]:
        """의뢰를 `received`로 만든다. 문장은 원문 그대로(너무 길면 잘라서) 둔다."""
        stamp = now().isoformat()
        row = {
            "request_id": request_id or new_request_id(),
            "account": account,
            "text": text[:MAX_TEXT],
            "created_at": stamp,
            "updated_at": stamp,
            "status": "received",
            "task": None,
            "confidence": None,
            "reason": None,
            "alternatives": [],
            "classification": None,
            "plan": [],
            "steps": {},
            "log": [{"at": stamp, "message": "의뢰를 받았다"}],
            "context_id": None,
            "thread_id": None,  # 접수한 대화 번호. 진행을 알릴 때 쓴다
            "handoff_task_id": None,  # 사람 받은 요청에 올린 일 번호
            "carry": {},  # 다음 단계로 그대로 넘길 앞 단계 결과 (화면·조회에는 내지 않는다)
            "buyer_id": None,  # 의뢰자 계정에 연결된 구매자 번호 (대시보드가 로그인 세션에서 준 값)
            # 11/23 사람이 의뢰를 삭제(멈추고 숨김)·의뢰문을 고쳐 다시 접수.
            # 옛 기록에는 칸이 없다(.get으로 읽는다)
            "stopped_at": None,  # 멈춘 시각: 이 뒤로 온 부서 결과는 다음 단계로 넘기지 않는다
            "hidden_at": None,
            "hidden_by": None,
            "replaces": None,  # 이 의뢰가 고쳐 다시 접수한 옛 의뢰 번호
            "replaced_by": None,  # 이 의뢰를 고쳐 다시 접수한 새 의뢰 번호
        }
        self._save(row)
        self._trim()
        return copy.deepcopy(row)

    def set_plan(
        self,
        request_id: str,
        *,
        task: str,
        confidence: float | None,
        reason: str | None,
        alternatives: list[str] | None,
        plan: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """고른 일과 계획을 적는다.

        계획의 단계는 {step_id, dept, skill, label, after[]}이다. `grade`(read·A·B·C)를
        같이 주면 재시작 뒤 이어가기(`settle_step`)에 쓴다.
        """
        row = self._row(request_id)
        row.update(
            status="classified",
            task=task,
            confidence=confidence,
            reason=reason,
            alternatives=list(alternatives or []),
            plan=[
                {k: v for k, v in spec.items() if k != "grade"}
                | {"after": list(spec.get("after") or [])}
                for spec in plan
            ],
        )
        row["steps"] = {}
        row["carry"] = {}
        for spec in plan:
            step = _blank_step()
            step["grade"] = spec.get("grade")
            row["steps"][spec["step_id"]] = step
        row["log"].append({"at": now().isoformat(), "message": f"일을 골랐다: {task}"})
        self._save(row)
        return copy.deepcopy(row)

    def set_buyer(self, request_id: str, buyer_id: str | None) -> None:
        """의뢰자의 구매자 번호를 적는다. 환불·교환 접수가 이 값으로만 구매자를 정한다."""
        row = self._row(request_id)
        row["buyer_id"] = buyer_id
        self._save(row)

    def set_brand_character(self, request_id: str, record_id: str | None, by: str | None) -> None:
        """홍보 영상 의뢰에 쓸 회사 대표 캐릭터(마케팅 제작 기록 CR-…)를 적는다.

        None이면 캐릭터 없이 만든다.

        값은 기록 번호뿐이다. 영상 준비 단계가 이 값을 마케팅 입력으로 넘긴다 (11/23)."""
        row = self._row(request_id)
        row["brand_character_record_id"] = record_id
        # 11/24: 언제 정했는지(화면이 기획 시작 시각과 견줘 "기획서는 예전 캐릭터 기준"을 띄운다)
        row["brand_character_at"] = now().isoformat()
        what = f"회사 캐릭터 {record_id}로 만들기" if record_id else "캐릭터 없이 만들기"
        row["log"].append({"at": now().isoformat(), "message": f"{what} ({by or '사람'})"})
        self._save(row)

    def set_carry(self, request_id: str, step_id: str, value: dict[str, Any] | None) -> None:
        """단계 결과 중 다음 단계가 그대로 받을 값을 둔다 (None이면 비운다). 쓰고 나면 지운다."""
        row = self._row(request_id)
        carry = row.setdefault("carry", {})
        if value:
            carry[step_id] = copy.deepcopy(value)
        else:
            carry.pop(step_id, None)
        self._save(row)

    def get_carry(self, request_id: str, step_id: str) -> dict[str, Any] | None:
        row = self._rows.get(request_id)
        value = ((row or {}).get("carry") or {}).get(step_id)
        return copy.deepcopy(value) if isinstance(value, dict) and value else None

    @staticmethod
    def _plan_view(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in spec.items() if k != "grade"}
            | {"after": list(spec.get("after") or [])}
            for spec in plan
        ]

    def plan_differs(self, request_id: str, plan: list[dict[str, Any]]) -> bool:
        """저장된 계획(단계·부서·입력 규칙·등급)이 `plan`과 다른가."""
        row = self._rows.get(request_id)
        if row is None:
            return False
        if row["plan"] != self._plan_view(plan):
            return True
        return any(
            (row["steps"].get(spec["step_id"]) or {}).get("grade") != spec.get("grade")
            for spec in plan
        )

    def refresh_plan(self, request_id: str, plan: list[dict[str, Any]]) -> dict[str, Any]:
        """시작 전 의뢰의 계획을 새 계획으로 바꾼다 (일·확신도·이유는 그대로)."""
        row = self._row(request_id)
        row["plan"] = self._plan_view(plan)
        row["steps"] = {}
        row["carry"] = {}
        for spec in plan:
            step = _blank_step()
            step["grade"] = spec.get("grade")
            row["steps"][spec["step_id"]] = step
        row["log"].append(
            {
                "at": now().isoformat(),
                "message": "일 목록이 바뀌어 계획을 지금 기준으로 다시 만들었다",
            }
        )
        self._save(row)
        return copy.deepcopy(row)

    def link_thread(self, request_id: str, thread_id: str | None) -> None:
        """의뢰를 접수한 대화를 적는다 (진행 알림을 그 대화로 보낸다)."""
        row = self._row(request_id)
        row["thread_id"] = thread_id
        self._save(row)

    def note(self, request_id: str, message: str) -> None:
        """진행 로그에 한 줄을 남긴다."""
        row = self._row(request_id)
        row["log"].append({"at": now().isoformat(), "message": message})
        self._save(row)

    def set_handoff(self, request_id: str, task_id: str) -> None:
        """사람 받은 요청에 올린 일 번호를 적는다."""
        row = self._row(request_id)
        row["handoff_task_id"] = task_id
        self._save(row)

    def replan(
        self, request_id: str, *, task: str, plan: list[dict[str, Any]], by: str | None
    ) -> dict[str, Any]:
        """사람이 일을 바꿔 다시 돌린다. 이전 선택은 `corrections`에 남겨 정확도 평가에 쓴다."""
        row = self._row(request_id)
        before = {
            "at": now().isoformat(),
            "by": by,
            "from_task": row.get("task"),
            "to_task": task,
            "from_status": row.get("status"),
            "from_confidence": row.get("confidence"),
        }
        self.set_plan(
            request_id,
            task=task,
            confidence=None,
            reason=f"사람이 골랐다{f' ({by})' if by else ''}",
            alternatives=[],
            plan=plan,
        )
        row = self._row(request_id)
        row.setdefault("corrections", []).append(before)
        row["handoff_task_id"] = None  # 다시 사람에게 넘겨야 하면 새로 올린다
        row["log"].append(
            {
                "at": before["at"],
                "message": f"사람이 일을 바꿔 다시 돌린다: {before['from_task']} → {task}",
            }
        )
        self._save(row)
        return copy.deepcopy(row)

    def set_classification(self, request_id: str, **fields: Any) -> dict[str, Any]:
        """분류 결과를 남긴다(지시문 버전·종류·확신도·이유·차선 후보). 정확도를 잴 때 쓴다."""
        row = self._row(request_id)
        row["classification"] = dict(fields)
        row["log"].append({"at": now().isoformat(), "message": "분류 결과를 남겼다"})
        self._save(row)
        return copy.deepcopy(row)

    def update_step(self, request_id: str, step_id: str, **fields: Any) -> dict[str, Any]:
        """단계 하나의 값을 고친다 (status·attempts·note·result_ref·review_id 등)."""
        row = self._row(request_id)
        step = row["steps"].get(step_id)
        if step is None:
            raise KeyError(f"계획에 없는 단계: {step_id}")
        unknown = set(fields) - set(_blank_step())
        if unknown:
            raise KeyError(f"단계에 없는 칸: {', '.join(sorted(unknown))}")
        step.update(fields)
        self._save(row)
        return copy.deepcopy(row)

    def set_status(
        self, request_id: str, status: str, message: str | None = None
    ) -> dict[str, Any]:
        if status not in OPEN_STATES + CLOSED_STATES:
            raise ValueError(f"알 수 없는 의뢰 상태: {status}")
        row = self._row(request_id)
        row["status"] = status
        if message:
            row["log"].append({"at": now().isoformat(), "message": message})
        self._save(row)
        return copy.deepcopy(row)

    # ---- 숨기기·고쳐 다시 접수 (11/23) ------------------------------------------------
    def hide(self, request_id: str, by: str | None) -> dict[str, Any]:
        """목록에서 숨긴다 (기록은 남는다). 이미 숨겼으면 처음 기록 그대로."""
        row = self._row(request_id)
        if not row.get("hidden_at"):
            stamp = now().isoformat()
            row["hidden_at"], row["hidden_by"] = stamp, by
            who = by or "누군지 모름"
            row["log"].append({"at": stamp, "message": f"사람이 의뢰를 숨겼다 ({who})"})
            self._save(row)
        return copy.deepcopy(row)

    def mark_stopped(self, request_id: str) -> None:
        """사람이 멈췄다고 적는다 (실행·긴 작업 결과가 다음 단계로 이어지지 않게)."""
        row = self._row(request_id)
        if not row.get("stopped_at"):
            row["stopped_at"] = now().isoformat()
            self._save(row)

    def unhide(self, request_id: str, by: str | None) -> dict[str, Any]:
        """숨김만 되돌린다 (멈춘 단계·상태는 그대로)."""
        row = self._row(request_id)
        if row.get("hidden_at"):
            row["hidden_at"], row["hidden_by"] = None, None
            who = by or "누군지 모름"
            row["log"].append({"at": now().isoformat(), "message": f"숨긴 의뢰를 되돌렸다 ({who})"})
            self._save(row)
        return copy.deepcopy(row)

    def link_replacement(self, old_id: str, new_id: str) -> None:
        """의뢰문을 고쳐 다시 접수한 두 의뢰를 잇는다 (옛 것 `replaced_by` · 새 것 `replaces`)."""
        old, new = self._row(old_id), self._row(new_id)
        stamp = now().isoformat()
        old["replaced_by"], new["replaces"] = new_id, old_id
        moved = f"의뢰문을 고쳐 새 의뢰({new_id})로 다시 접수했다"
        old["log"].append({"at": stamp, "message": moved})
        new["log"].append({"at": stamp,
                           "message": f"의뢰({old_id})의 의뢰문을 고쳐 다시 접수했다"})  # fmt: skip
        self._save(old)
        self._save(new)

    # ---- 읽기 ------------------------------------------------------------------
    def get(self, request_id: str) -> dict[str, Any] | None:
        row = self._rows.get(request_id)
        return copy.deepcopy(row) if row else None

    def list(
        self,
        account: str | None = None,
        *,
        status: str | None = None,
        hidden: bool | None = None,
    ) -> list[dict[str, Any]]:
        """받은 순서대로. `account`를 주면 그 의뢰자의 것만 (일반회원은 자기 의뢰만 본다).

        `hidden`: None이면 전부, False면 숨기지 않은 것만, True면 숨긴 것만."""
        rows = [
            r
            for r in self._rows.values()
            if (account is None or r["account"] == account)
            and (status is None or r["status"] == status)
            and (hidden is None or bool(r.get("hidden_at")) == hidden)
        ]
        rows.sort(key=lambda r: (r["created_at"], r["request_id"]))
        return copy.deepcopy(rows)

    def open_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.list() if r["status"] in OPEN_STATES]

    def _row(self, request_id: str) -> dict[str, Any]:
        row = self._rows.get(request_id)
        if row is None:
            raise KeyError(f"없는 의뢰: {request_id}")
        return row

    def _trim(self) -> None:
        """닫힌 의뢰 중 오래된 것부터 지워 `MAX_REQUESTS`를 넘지 않게 한다 (열린 것은 둔다)."""
        extra = len(self._rows) - MAX_REQUESTS
        if extra <= 0:
            return
        closed = sorted(
            (r for r in self._rows.values() if r["status"] in CLOSED_STATES),
            key=lambda r: r["created_at"],
        )
        for row in closed[:extra]:
            self._rows.pop(row["request_id"], None)
            if self._store is not None:
                self._store.drop_request(row["request_id"])
