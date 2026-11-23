"""세션 모드에서 작업·PR 조작을 작업 주인과 관리자로 좁힌다 (#1556 작업 API와 같은 규칙).

`/actions`(PR 만들기·재검수·머지·레포 승인·기각)와 레포 작업 공간(`/repo/*`)의 AI PR 쓰기가 쓴다.
세션 설정이 꺼진 로컬·시험 모드는 예전처럼 확인하지 않는다 (작업 API의 `?account=` 데모 규칙은
작업 API에만 있다).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from ax_workflow.auth import (
    AuthError,
    check_human,
    scoped_account,
    session_admin,
    session_mode,
)
from dev_agent import dev_chat

if TYPE_CHECKING:
    from dev_agent.service import DevService

# 작업·PR 하나를 조작하는 `/actions` 액션 (snake_case)
TASK_ACTIONS = frozenset({"generate_prs", "review_pr", "merge_pr", "approve_repo", "reject_repo"})


def _person(authorization: str | None) -> str | None:
    try:
        return check_human(authorization)
    except AuthError:
        return None


class BadAction(Exception):
    """액션 본문이 그 액션의 입력 모델에 맞지 않는다."""


def tasks_of_action(svc: DevService, name: str, body: dict) -> list[str]:
    """액션이 건드리는 작업 id. dispatch와 같은 입력 모델로 확인한 값으로 찾는다 (`77.0` 같은 값도
    dispatch가 보는 PR로). 모델에 맞지 않으면 `BadAction`, 모르는 기획서·PR이면 빈 목록."""
    model = svc.ACTIONS[name][1]
    try:
        params = model.model_validate({k: v for k, v in body.items() if k != "type"})
    except ValidationError as exc:
        raise BadAction(str(exc)) from exc
    s = svc.state
    if name == "generate_prs":
        return [t.id for t in s.tasks if t.spec == params.spec]
    p = svc._find_pr(s, params.pr)
    return [p.task] if p is not None else []


def check_session(authorization: str | None) -> None:
    """세션 모드면 세션 토큰이 있어야 한다 (없으면 401, 공용·에이전트 토큰이면 403)."""
    scoped_account(authorization, None)


def may_operate_tasks(svc: DevService, task_ids: list[str], authorization: str | None) -> bool:
    """세션 모드에서 이 토큰의 계정이 작업들을 조작해도 되나. 세션 모드가 아니면 늘 True.

    세션 토큰이 없으면 `scoped_account`가 401·403을 던진다. 관리자는 누구 작업이든 된다.
    대화가 낸 요청이 아닌 작업은 `dev_chat.may_operate`대로 누구나 된다."""
    if not session_mode():
        return True
    mine = scoped_account(authorization, None)
    admin = session_admin(authorization)
    who = _person(authorization)
    return all(dev_chat.may_operate(svc, tid, mine, who, admin) for tid in task_ids)
