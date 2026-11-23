"""안전 관문이 사람 확인으로 보류(held)한 개발 AI 액션을, 사람이 승인한 뒤 한 번만 이어서 한다.

전에는 보류 건을 사람이 승인해도(안전 확인 탭·결재함) 개발 AI는 아무것도 하지 않았다. 사람이 같은
버튼을 다시 눌러야 했고, 레포 쓰기(`repo_write`)는 누를 때마다 새 키로 물어 또 보류됐다.

흐름:

1. 보류될 수 있는 호출 자리는 `recipe(kind, **args)`로 "승인되면 무엇을 다시 할지"를 적어 둔다.
   `DevService._execute`가 관문의 `held` 판정(`review_id`)을 받으면 그 적어 둔 것을 보류 기록으로
   남긴다 (`HoldStore`, `PLANNING_STATE_DIR/hold_resume_dev.json`).
2. 결정은 `safety.review_decided` 이벤트(REDIS_URL이 있을 때) 또는 폴링(PR 맞추기 루프, 60초)이
   가져온다. 승인이면 `pending → resuming`으로 바꿔 파일에 먼저 쓰고, 그 뒤에 한 번 다시 한다.
   거절·만료는 다시 하지 않고 기록만 한다.
3. 다시 할 때도 원래와 같은 경로·같은 멱등 키로 `execute()`를 부른다. 관문은 승인된 판정
   (`executed`)을 그대로 돌려준다 (관문을 건너뛰지 않는다).
4. 결과는 보류 기록(`status`·`reason`)과 활동 줄에 남긴다. 그 사이 상태가 바뀌어 할 필요가 없으면
   (작업 중단·PR 이미 머지·닫힘 등) `skipped` + 이유로 남긴다.

한 번만: 상태 확인과 `resuming` 표시 사이에 await가 없어(한 이벤트 루프) 같은 승인이 두 번 와도,
두 번 눌러도 하나만 이긴다. 다시 켰을 때 `resuming`으로 남은 기록은 다시 하지 않고 `failed`
("결과 모름 → 사람 확인")로 둔다.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ax_agent_template import config as template_config
from ax_contracts.schemas import ExecuteDecision
from ax_workflow.auth import acting, session_mode
from ax_workflow.core import ActionError, GateRefused
from ax_workflow.store import ENV_DIR

if TYPE_CHECKING:
    from dev_agent.service import DevService

log = logging.getLogger(__name__)
FILE_NAME = "hold_resume_dev.json"
MOCK_NAME = "planning"  # 개발 서비스도 `PLANNING_MOCK`을 따른다 (deploy_store와 같음)

Status = Literal[
    "pending", "resuming", "done", "held_again", "failed", "skipped", "rejected", "expired"
]
OPEN = "pending"
# 다시 할 필요가 없어진 경우로 보는 ActionError 코드 (그 사이 상태가 바뀜)
STALE_CODES = frozenset({"NOT_FOUND", "NOT_RETRYABLE", "NOT_MERGED", "REQUEST_PAUSED"})
KEEP_CLOSED = 200  # 끝난 보류 기록을 남기는 건수
KEEP_DAYS = 30  # 끝난 보류 기록을 남기는 날수
POLL_MAX = 20  # 한 차례 폴링에서 묻는 보류 건 수
POLL_CONCURRENCY = 5  # 동시에 묻는 수
FETCH_TIMEOUT = 10.0  # 보류 건 하나를 묻는 데 기다리는 초
MISSING = "안전 확인 기록 없음 → 하지 않음 (관문이 그 보류 건을 모른다)"
INTERRUPTED = "다시 하던 중 서버가 다시 켜져 결과를 모름 → 다시 하지 않음, 사람이 확인"


class HeldAction(BaseModel):
    """관문이 보류한 액션 하나와 승인되면 다시 할 방법 (`kind` + `args`)."""

    review_id: str
    action: str
    key: str  # 관문 멱등 키 (`{ctx}:{action}:{target}`)
    ctx: str
    kind: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Status = OPEN
    reason: str | None = None  # 결과·건너뛴 이유·실패 이유 (사람이 읽는 한 줄)
    decided_by: str | None = None
    next_review: str | None = None  # 다시 하다 새로 보류되면 그 보류 건
    created_at: datetime
    finished_at: datetime | None = None


class Stale(Exception):
    """그 사이 상태가 바뀌어 다시 할 필요가 없다 (`skipped`로 남긴다)."""


# ---- 호출 자리가 적어 두는 "승인되면 다시 할 것"

_recipe: contextvars.ContextVar[tuple[str, dict[str, Any]] | None] = contextvars.ContextVar(
    "hold_recipe", default=None
)
_resuming: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hold_resuming", default=None
)


@contextlib.contextmanager
def recipe(kind: str, **args: Any) -> Iterator[None]:
    """이 안의 관문 호출이 보류되면 `kind`·`args`로 다시 한다 (관문 호출 하나만 감싼다)."""
    token = _recipe.set((kind, args))
    try:
        yield
    finally:
        _recipe.reset(token)


def current_resume() -> str | None:
    """지금 승인된 보류 건을 이어서 하는 중이면 그 review_id."""
    return _resuming.get()


def clean_context() -> contextvars.Context:
    """뒤에서 따로 도는 작업(코딩 실행 등)용 문맥. 이어서 하던 보류 건·적어 둔 것을 물려받지 않는다.

    물려받으면 나중에 그 작업이 새로 보류될 때 이미 닫힌 보류 건에 이어 붙는다."""
    from ax_workflow import auth

    ctx = contextvars.copy_context()
    ctx.run(_resuming.set, None)
    ctx.run(_recipe.set, None)
    ctx.run(auth._actor.set, None)  # 사람 결정을 한 사람도 물려받지 않는다
    return ctx


# ---- 저장소


def _path() -> str | None:
    folder = (os.getenv(ENV_DIR) or "").strip()
    return os.path.join(folder, FILE_NAME) if folder else None


def _mode() -> str:
    return "mock" if template_config.is_mock(MOCK_NAME) else "real"


class HoldStore:
    """보류 기록. 파일이 있으면 읽고, 바뀔 때마다 통째로 쓴다 (deploy_store와 같은 방식)."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self.clock = clock
        self.rows: dict[str, HeldAction] = {}
        self._load()

    def reset(self) -> None:
        self.rows = {}
        self._save()

    def get(self, review_id: str) -> HeldAction | None:
        return self.rows.get(review_id)

    def pending(self) -> list[HeldAction]:
        return [r for r in self.rows.values() if r.status == OPEN]

    def listed(self) -> list[dict[str, Any]]:
        """화면·점검용 목록. 다시 할 재료(`args`: 사람이 적은 글·경로 등)는 내보내지 않는다."""
        rows = sorted(self.rows.values(), key=lambda r: r.created_at, reverse=True)
        return [
            {
                "review_id": r.review_id,
                "kind": r.kind,
                "action": r.action,
                "status": r.status,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "task_id": r.args.get("task_id"),
            }
            for r in rows
        ]

    def note(self, action: str, key: str, ctx: str, decision: ExecuteDecision) -> HeldAction | None:
        """관문 `held` 판정을 남긴다. 적어 둔 것이 없거나 같은 보류 건이 이미 있으면 그대로."""
        made = _recipe.get()
        rid = decision.review_id
        if made is None or not rid:
            return None
        parent = self.rows.get(_resuming.get() or "")
        if parent is not None and parent.review_id != rid:
            parent.next_review = rid
        if rid in self.rows:
            self._save()
            return self.rows[rid]
        kind, args = made
        row = HeldAction(
            review_id=rid, action=action, key=key, ctx=ctx, kind=kind,
            args=dict(args), created_at=self.clock(),
        )  # fmt: skip
        self.rows[rid] = row
        self._save()
        return row

    def claim(self, review_id: str, by: str | None) -> HeldAction | None:
        """`pending`이면 `resuming`으로 바꿔 파일에 쓰고 돌려준다. 이미 누가 가져갔으면 None.

        확인과 표시 사이에 await가 없어 같은 이벤트 루프의 두 호출 중 하나만 이긴다."""
        row = self.rows.get(review_id)
        if row is None or row.status != OPEN:
            return None
        row.status, row.decided_by = "resuming", by
        if not self._save():  # 표시를 못 남기면 다시 켤 때 또 할 수 있다 → 하지 않고 대기로 둔다
            row.status, row.decided_by = OPEN, None
            return None
        return row

    def close(self, review_id: str, status: Status, reason: str | None) -> HeldAction | None:
        row = self.rows.get(review_id)
        if row is None:
            return None
        row.status, row.reason, row.finished_at = status, reason, self.clock()
        self._save()
        return row

    # 파일

    def _prune(self) -> None:
        """끝난 기록은 최근 KEEP_CLOSED건·KEEP_DAYS일만 둔다. 대기·하던 중인 건은 늘 둔다."""
        now = self.clock()
        closed = sorted(
            (r for r in self.rows.values() if r.status not in (OPEN, "resuming")),
            key=lambda r: r.finished_at or r.created_at,
            reverse=True,
        )
        for i, r in enumerate(closed):
            old = now - (r.finished_at or r.created_at) > timedelta(days=KEEP_DAYS)
            if i >= KEEP_CLOSED or old:
                del self.rows[r.review_id]

    def _save(self) -> bool:
        """파일에 쓴다. 저장 경로가 없으면(메모리만) True, 쓰기에 실패하면 False."""
        self._prune()
        path = _path()
        if path is None:
            return True
        data = {"mode": _mode(), "rows": [r.model_dump(mode="json") for r in self.rows.values()]}
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("보류 기록 저장 실패 (%s): %s", path, exc)
            return False
        return True

    def _load(self) -> None:
        path = _path()
        if path is None or not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("mode") != _mode():
                return  # 다른 모드의 기록은 버린다
            rows = [HeldAction.model_validate(r) for r in data.get("rows") or []]
        except (OSError, ValueError, TypeError):
            log.warning("보류 기록을 읽지 못함 (%s) → 빈 기록으로 시작", path)
            return
        self.rows = {r.review_id: r for r in rows}
        stuck = [r for r in rows if r.status == "resuming"]
        for r in stuck:  # 한 번만: 하던 중 꺼진 건은 다시 하지 않는다
            r.status, r.reason, r.finished_at = "failed", INTERRUPTED, self.clock()
        if stuck:
            self._save()


# 이 보류 건으로 끝난 키는 다시 물어도 같은 판정만 재생한다 (관문 멱등). 사람이 다시 누르면 새 키
REFUSED_DONE: frozenset[str] = frozenset({"rejected", "expired", "skipped"})


def fresh_target(store: HoldStore, ctx: str, action: str, target: str) -> str:
    """사람 버튼 관문 대상. 같은 대상의 보류가 거절·만료·건너뜀으로 끝났으면 `:r<n>`을 붙인다.

    고정 키(PR·머리 커밋마다 하나)는 두 번 클릭·다시 보내기를 한 보류 건으로 묶지만, 거절·만료된
    판정을 영원히 재생한다. 끝난 건수만큼 새 키를 써서 사람이 다시 승인을 받을 수 있게 한다.
    보류가 열려 있는 동안은 건수가 그대로라 같은 키(같은 보류 건)다."""
    base = f"{ctx}:{action}:{target}"
    done = sum(
        1
        for r in store.rows.values()
        if r.status in REFUSED_DONE and (r.key == base or r.key.startswith(f"{base}:r"))
    )
    return f"{target}:r{done}" if done else target


# ---- 결정 → 이어서 하기

Resumer = Callable[["DevService", HeldAction], Awaitable[str]]


async def decide(
    svc: DevService,
    review_id: str,
    decision: Literal["approved", "rejected", "expired"],
    by: str | None = None,
    source: str = "poll",
) -> HeldAction | None:
    """사람 결정 하나를 반영한다. 승인이면 원래 액션을 한 번 다시 한다. 모르는 건이면 None."""
    store = svc.holds
    row = store.get(review_id)
    if row is None or row.status != OPEN:
        return row  # 이미 처리함 (중복 이벤트·폴링·두 번 클릭)
    if decision != "approved":
        status: Status = "expired" if decision == "expired" else "rejected"
        why = (
            "보류 기한이 지나 자동 거절 → 하지 않음"
            if status == "expired"
            else "사람이 거절 → 하지 않음"
        )
        store.close(review_id, status, why + (f" ({by})" if by else ""))
        await _log(svc, row, f"{_label(row)} 보류 {review_id} {why}")
        if row.kind == "policy_demote":  # 막힌 축소를 닫음 → 줄인 단계로 보던 것도 푼다
            word = "보류 만료" if status == "expired" else "보류 거절"
            await svc.drop_shrink_pending(int(row.args["frm"]), int(row.args["to"]), word)
        return store.get(review_id)
    claimed = store.claim(review_id, by)
    if claimed is None:
        return store.get(review_id)
    resumer = RESUMERS.get(claimed.kind)
    token = _resuming.set(review_id)
    status, reason = "done", ""
    try:
        if resumer is None:
            raise Stale(f"이어서 할 방법을 모르는 종류: {claimed.kind}")
        reason = await resumer(svc, claimed)
    except Stale as exc:
        status, reason = "skipped", f"건너뜀: {exc}"
    except ActionError as exc:
        stale = exc.code in STALE_CODES
        status = "skipped" if stale else "failed"
        reason = f"{'건너뜀' if stale else '실패'}: {exc.message} ({exc.code})"
    except GateRefused as exc:
        again = exc.decision.status == "held"
        status = "held_again" if again else "failed"
        tail = f" → 새 보류 {exc.decision.review_id}" if again and exc.decision.review_id else ""
        reason = f"관문이 다시 {exc.decision.status}{tail}"
    except Exception as exc:  # noqa: BLE001 - 실패를 조용히 버리지 않고 남긴다
        log.exception("보류 승인 뒤 이어서 하기 실패: %s", review_id)
        status, reason = "failed", f"실패: {type(exc).__name__}: {exc}"
    finally:
        _resuming.reset(token)
    done = store.get(review_id)
    if status == "done" and done is not None and done.next_review:
        status, reason = "held_again", f"{reason} · 다음 단계가 새로 보류됨 {done.next_review}"
    store.close(review_id, status, reason)
    await _log(svc, claimed, f"{_label(claimed)} 보류 {review_id} 승인 → {reason} ({source})")
    if status == "failed":
        await _mark_failure(svc, claimed, reason)
    return store.get(review_id)


def _label(row: HeldAction) -> str:
    from dev_agent.wording import action_ko

    return action_ko(row.action)


async def _log(svc: DevService, row: HeldAction, text: str) -> None:
    async def put(s: Any) -> None:
        s.activity.append(
            svc._act(svc.clock(), row.ctx, "safety", "dev", "hold.resume", "execute", text, "C")
        )

    try:
        await svc._apply(put)
    except Exception:  # noqa: BLE001 - 기록 실패가 결과(보류 기록)를 바꾸지 않게 한다
        log.exception("보류 이어서 하기 활동을 남기지 못함: %s", row.review_id)


async def _mark_failure(svc: DevService, row: HeldAction, reason: str) -> None:
    """실패를 작업 오류 줄에도 남긴다 (작업에 묶인 보류만)."""
    task_id = row.args.get("task_id") or _task_of_pr(svc, row.args.get("pr"))
    if not task_id:
        return

    async def put(s: Any) -> None:
        t = next((x for x in s.tasks if x.id == task_id), None)
        if t is None or t.coding is None:
            return
        line = f"보류 승인 뒤 이어서 하기 {reason}"
        t.coding.error = f"{t.coding.error}\n{line}" if t.coding.error else line
        t.coding.updated_at = svc.clock()

    with contextlib.suppress(Exception):
        await svc._apply(put)


def _task_of_pr(svc: DevService, pr: Any) -> str | None:
    p = svc._find_pr(svc.state, pr) if pr else None
    return p.task if p is not None else None


# ---- 종류별로 다시 하기 (원래 호출과 같은 경로·같은 키)


def repo_of(connection_id: str) -> dict[str, str]:
    """보류 때 연결이 가리키던 레포 (승인 뒤 연결이 다른 레포로 바뀌었는지 보려고 적어 둔다)."""
    from dev_agent import vcs

    conn = vcs.find_connection(connection_id)
    return {"repo": conn.repo, "base_url": conn.base_url} if conn is not None else {}


def expect_repo(connection_id: str, repo: Any, base_url: Any) -> None:
    """연결이 없어졌거나 다른 레포를 가리키면 `Stale` (승인은 그 레포에 대한 것이었다)."""
    from dev_agent import vcs

    conn = vcs.find_connection(connection_id)
    if conn is None:
        raise Stale(f"연결 {connection_id}가 없다")
    if repo is not None and (conn.repo, conn.base_url) != (repo, base_url or ""):
        raise Stale(f"연결 {connection_id}가 다른 레포를 가리킴 ({repo} → {conn.repo})")


async def _coding_pr(svc: DevService, row: HeldAction) -> str:
    """코딩 작업 PR 열기(generate_pr). "승인하고 PR 열기"(retry approved)와 같은 길."""
    task_id, key = str(row.args["task_id"]), row.args.get("key")
    t = next((x for x in svc.state.tasks if x.id == task_id), None)
    if t is None or t.coding is None:
        raise Stale(f"작업 {task_id}가 없다")
    cr = t.coding
    if t.discarded:
        raise Stale(f"{task_id}는 폐기한 작업이다")
    if cr.hold_key != key:
        raise Stale(f"{task_id}의 보류 키가 바뀜 (다시 시도·새 보류 건)")
    if task_id in svc._coding or cr.pr is not None or cr.status != "waiting_human":
        raise Stale(f"{task_id}는 이미 {cr.status} 상태 (사람 버튼으로 이어 갔거나 바뀜)")
    await svc.retry_dev_task(task_id, approved=True)
    # PR이 열렸다는 뜻이 아니다: 열기 실행을 다시 띄웠고, 결과는 작업 상태(열림·실패)에 남는다
    return f"{task_id} PR 열기 실행을 다시 시작함 (같은 코드·같은 키, 결과는 작업 상태에)"


async def _merge_pr(svc: DevService, row: HeldAction) -> str:
    """AI가 만든 PR 머지 (자동 머지·사람 머지 버튼과 같은 `merge_pr` 흐름)."""
    p = svc._find_pr(svc.state, row.args["pr"])
    if p is None:
        raise Stale(f"{row.args['pr']}가 없다")
    if p.merged:
        raise Stale(f"{p.id}는 이미 머지됨")
    if p.closed:
        raise Stale(f"{p.id}는 닫힘")
    from dev_agent import vcs_bridge

    # 승인은 보류 때 머리 커밋에 대한 것이다. 그 뒤 새 커밋이 올라왔으면 이어서 하지 않는다
    # (다시 부르면 새 키로 새 판정을 받는다: 사람이 다시 머지 버튼)
    held = row.args.get("head")
    try:
        now: str | None = await vcs_bridge.head_of(p)
    except Exception:  # noqa: BLE001 - 못 읽으면 머지 쪽이 실패로 남긴다 (MERGE_FAILED)
        now = None
    if held and now is not None and now != held:
        raise Stale(f"{p.id}에 새 커밋이 올라와 승인한 코드와 다르다 ({held[:12]} → {now[:12]})")
    # 같은 머리면 보류 때와 같은 키 (`_do_merge`). 형상관리 병합 실패는 MERGE_FAILED (실패로 남는다)
    await svc.merge_pr(p.id)
    after = svc._find_pr(svc.state, p.id)
    if after is None or not after.merged:
        raise ActionError("MERGE_FAILED", f"{p.id} 머지되지 않음")
    return f"{p.id} 머지함"


async def _repo_write(svc: DevService, row: HeldAction) -> str:
    """레포 작업 공간 사람 버튼 (사람이 올린 PR 머지·되돌리기 PR 열기). 보류 때 키 그대로."""
    from dev_agent import repo_api

    a = row.args
    op, what = await repo_api.resume_op(dict(a["op"]))
    await svc.repo_write(
        str(a["action"]), str(a["target"]), str(a["text"]), a.get("params"), op,
        resume=dict(a["op"]), key_target=str(a["key_target"]),
    )  # fmt: skip
    return what


async def _incident_revert(svc: DevService, row: HeldAction) -> str:
    """장애 되돌리기 (`revert_merged_pr`). 같은 입력으로 다시 부르면 이미 연 되돌리기 PR을 쓴다."""
    from dev_agent import incident_repo

    call = dict(row.args["call"])
    expect_repo(str(call["connection_id"]), row.args.get("repo"), row.args.get("base_url"))
    out = await incident_repo.revert_merged(svc, **call)
    if out.status == "held":
        nxt = (svc.holds.get(row.review_id) or row).next_review
        if nxt:
            return f"되돌리기 다음 단계가 관문에서 다시 보류됨 → 새 보류 {nxt}"
        # 관문이 아니라 되돌리기 PR CI 실패·형상관리 오류로 멈춤 → 사람이 본다
        raise ActionError("REVERT_HELD", "되돌리기 PR CI 실패·형상관리 오류로 멈춤 → 사람 확인")
    n = f" #{out.revert_pr_number}" if out.revert_pr_number else ""
    return f"되돌리기 PR{n} {out.status}"


async def _policy_promote(svc: DevService, row: HeldAction) -> str:
    pol = svc.state.auto_policy
    frm, to = int(row.args["frm"]), int(row.args["to"])
    if pol.proposal is None or pol.proposal.to != to or pol.level != frm:
        raise Stale(f"승급 제안 L{frm} → L{to}가 더는 없다 (지금 L{pol.level})")
    # 누른 사람(세션 모드 관리자, 보류 때 적음). 세션 모드에서 없으면 보류 건을 승인한 사람.
    # 세션 설정이 꺼진 로컬·시험 모드는 예전처럼 팀장(목)
    who = row.args.get("actor") or (row.decided_by if session_mode() else None)
    with acting(str(who) if who else None):
        await svc.approve_promotion()
    return f"자동 처리 범위 L{frm} → L{to} 승급함"


async def _policy_demote(svc: DevService, row: HeldAction) -> str:
    frm, to = int(row.args["frm"]), int(row.args["to"])
    if svc.state.auto_policy.level != frm:
        raise Stale(f"자동 처리 범위가 이미 L{svc.state.auto_policy.level}")
    await svc.resume_policy_demote(row.ctx, frm, to, str(row.args.get("text") or ""))
    return f"자동 처리 범위 L{frm} → L{to} 축소함"


async def _deploy_dispatch(svc: DevService, row: HeldAction) -> str:
    """infra `deploy_version`(배포 실행)이 관문에서 보류됐던 배포.

    같은 요청 id로 한 번 다시 보낸다 (infra 관문이 승인된 판정을 돌려준다)."""
    from dev_agent.deploy_store import get_deploy_store

    store = get_deploy_store()
    dep_id = str(row.args["dep_id"])
    dep = store.get(dep_id)
    if dep is None:
        raise Stale(f"배포 {dep_id}가 없다")
    if dep.run is None or dep.run.state != "held" or dep.run.review_id != row.review_id:
        state = dep.run.state if dep.run else "보내기 전"
        raise Stale(f"배포 {dep_id}가 이미 보류 상태가 아니다 ({state})")
    done = await store.dispatch(svc, dep_id, resume=True)
    run = done.run
    if run is not None and run.state == "dispatched":
        return f"배포 {dep_id} 보냄 → {run.tool} 실행 {run.run_id or '번호 모름'}"
    if run is not None and run.state == "held":
        return f"배포 {dep_id} 다시 보류"
    if run is not None and run.state in ("unknown", "handed"):
        # 보냈지만 결과를 모르거나 사람 할 일함으로 넘어갔다: 못 보냄이 아니다
        return f"배포 {dep_id} {run.reason or run.state}"
    reason = (run.reason if run else None) or "결과 없음"
    raise ActionError("DEPLOY_FAILED", f"배포 {dep_id} 못 보냄: {reason}")


RESUMERS: dict[str, Resumer] = {
    "deploy_dispatch": _deploy_dispatch,
    "coding_pr": _coding_pr,
    "merge_pr": _merge_pr,
    "repo_write": _repo_write,
    "incident_revert": _incident_revert,
    "policy_promote": _policy_promote,
    "policy_demote": _policy_demote,
}


# ---- 결정 가져오기 (폴링·이벤트)

Fetch = Callable[[str], Awaitable[dict[str, Any] | None]]


async def _fetch_review(review_id: str) -> dict[str, Any] | None:
    """안전 관문 보류 건 조회 (읽기 GET). 못 읽으면 None, 다음 차례에 다시 묻는다."""
    import httpx

    from ax_agent_template.config import agent_token, agent_url, get_agent_name

    try:
        async with httpx.AsyncClient() as http:
            res = await http.get(
                f"{agent_url('safety')}/api/safety/reviews/{review_id}",
                headers={"Authorization": f"Bearer {agent_token(get_agent_name())}"},
                timeout=10.0,
            )
            if res.status_code == 404:
                return {"status": "missing"}
            res.raise_for_status()
            data = res.json().get("data")
            return data if isinstance(data, dict) and "status" in data else None
    except Exception:  # noqa: BLE001 - 다음 차례에 다시 묻는다
        return None


def _can_fetch() -> bool:
    # 목 모드에서 AX_SAFETY_URL이 없으면 관문도 없다 (execute.py와 같은 규칙)
    return not (template_config.is_mock() and not os.getenv("AX_SAFETY_URL"))


async def poll_once(svc: DevService, fetch: Fetch | None = None) -> int:
    """대기 중인 보류 건마다 결정을 묻고 반영한다. 반영한 건수."""
    if fetch is None and not _can_fetch():
        return 0
    get = fetch or _fetch_review
    rows = sorted(svc.holds.pending(), key=lambda r: r.created_at)[:POLL_MAX]
    gate = asyncio.Semaphore(POLL_CONCURRENCY)

    async def ask(rid: str) -> dict[str, Any] | None:
        async with gate:
            try:
                return await asyncio.wait_for(get(rid), timeout=FETCH_TIMEOUT)
            except Exception:  # noqa: BLE001 - 다음 차례에 다시 묻는다
                return None

    infos = await asyncio.gather(*(ask(r.review_id) for r in rows))
    handled = 0
    for row, info in zip(rows, infos, strict=True):
        status = (info or {}).get("status")
        if status == "missing":
            svc.holds.close(row.review_id, "skipped", MISSING)
            await _log(svc, row, f"{_label(row)} 보류 {row.review_id} {MISSING}")
        elif status in ("approved", "rejected", "expired"):
            await decide(svc, row.review_id, status, (info or {}).get("decided_by"), "poll")
        else:
            continue
        handled += 1
    return handled


def setup_subscriber(get_service: Callable[[], DevService]) -> bool:
    """`safety.review_decided` 구독. REDIS_URL이 없으면 폴링만 쓴다 (False)."""
    from pydantic import ValidationError

    from ax_agent_template import events
    from ax_contracts.schemas import EventEnvelope, EventType
    from ax_contracts.schemas.event import SafetyReviewDecidedData

    async def on_decided(envelope: EventEnvelope) -> None:
        try:
            data = SafetyReviewDecidedData.model_validate(envelope.data)
        except ValidationError:
            return
        svc = get_service()
        if svc.holds.get(data.review_id) is None:
            return  # 개발 AI가 이어서 할 보류 건이 아니다
        # 마감 만료는 안전 관문이 `system` 이름으로 자동 거절한다
        expired = data.decision == "rejected" and data.decided_by == "system"
        decision = "expired" if expired else data.decision
        await decide(svc, data.review_id, decision, data.decided_by, "event")

    try:
        events.subscribe([EventType.SAFETY_REVIEW_DECIDED], on_decided)
    except NotImplementedError:  # EventBusUnavailable
        log.warning(
            "REDIS_URL이 없어 safety.review_decided를 못 받음 → 보류 승인은 폴링으로만 잇는다"
        )
        return False
    return True
