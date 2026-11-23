"""작업 대기열(`/work/*`·MCP) 전용 토큰: `DEV_WORK_TOKENS="이름:토큰,이름:토큰"`.

개발자가 `claude mcp add --header ...`로 넣은 토큰은 개발자 PC의 Claude Code 설정
(`~/.claude.json`)에 평문으로 남는다. 같은 PC의 다른 앱이 이 파일을 읽거나 고칠 수 있다
(예: 2026-11 Postman 데스크톱이 Claude Code 설정에 몰래 훅을 넣은 사건). 그래서 MCP에는
사람 결정(관문 승인·머지·재무) 권한이 있는 사람 토큰 대신 **작업 대기열만 쓸 수 있는 토큰**을 쓴다.

- 이 토큰은 작업 목록·가져가기·진행·PR 알리기·내려놓기만 한다. 다른 API(`check_human`·
  `check_admin`)는 이 토큰을 모르는 토큰(401)으로 본다.
- 토큰 이름이 가져간 사람이 된다 (사람 토큰과 같은 이름을 쓰면 화면의 "내 Claude"와 이어진다).
- 예전처럼 사람 토큰도 한동안 받는다. `DEV_WORK_ALLOW_HUMAN=0`이면 작업 토큰만 받는다.
- 개인 MCP 연결 토큰(`axm_…`, 내 연결에서 회원이 발급, 11/20)도 받는다. dashboard-api에 물어
  주인 계정을 받는다(personal_mcp.py). 이름은 `acct:<계정 ID>`, `account_id`는 그 계정(`Caller`).
- 세션 모드(`AX_SESSION_SECRET`)면 계정을 아는 토큰만 받는다: 개인 MCP 연결 토큰과 로그인 세션
  토큰(이름 `acct:<계정 ID>`). 작업 토큰(`DEV_WORK_TOKENS`)·공용 사람 토큰은 이름만 있어 누구
  작업인지 가를 수 없어 403 `SESSION_REQUIRED`. 대기열은 그 계정 작업만 보여 주고 가져가게 한다
  (work_queue `account`).
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

from fastapi import Header

from ax_workflow.auth import SESSION_REQUIRED, AuthError, check_human, session_human, session_mode
from dev_agent import personal_mcp


def _worker(token: str) -> str | None:
    for item in os.getenv("DEV_WORK_TOKENS", "").split(","):
        name, sep, known = item.strip().partition(":")
        if sep and name and known and hmac.compare_digest(known, token):
            return name
    return None


def allow_human() -> bool:
    return os.getenv("DEV_WORK_ALLOW_HUMAN", "1").strip() not in ("0", "false", "no")


def check_worker(authorization: str | None) -> str:
    """작업 토큰이면 이름. 아니면 (허용할 때만) 사람 토큰 규칙 그대로."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token:
        name = _worker(token)
        if name:
            return name
    if allow_human():
        return check_human(authorization)
    if not token:
        raise AuthError(401, "토큰이 필요하다 (Authorization: Bearer 토큰)")
    raise AuthError(401, "작업 대기열 토큰이 아니다 (DEV_WORK_TOKENS)")


def require_worker(authorization: str | None = Header(default=None)) -> str:
    return check_worker(authorization)


@dataclass(frozen=True)
class Caller:
    """작업 대기열·MCP를 부른 사람. 개인 MCP 연결 토큰이면 `account_id`가 그 계정."""

    name: str
    account_id: str | None = None
    admin: bool = False  # 로그인 세션의 회사 관리자 (세션 모드에서 누구 작업이든 가져간다)


async def require_caller(authorization: str | None = Header(default=None)) -> Caller:
    """개인 MCP 연결 토큰이면 dashboard-api 검사, 아니면 작업·사람 토큰 규칙(`check_worker`)."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and personal_mcp.is_personal(token):
        out = await personal_mcp.verify(token)
        if not out.account_id:
            raise AuthError(401, "개인 MCP 연결 토큰의 주인 계정을 알 수 없다")
        # 작업 대기열의 "누구"(claimed_by 비교)는 계정 ID로. 표시 이름이 작업 토큰 이름과 같아도
        # 그 사람 작업을 가져가거나 진행·내려놓을 수 없게 한다(11/20 태윤 님 리뷰). 작업·사람 토큰
        # 이름은 `이름:토큰` 형식이라 `:`을 못 써서 `acct:`와 겹치지 않는다.
        return Caller(name=f"acct:{out.account_id}", account_id=out.account_id)
    if not session_mode():
        return Caller(check_worker(authorization))
    # 세션 모드: 로그인 세션이면 그 계정. 이름만 있는 작업·공용 토큰은 받지 않는다 (계정을 모른다)
    human = session_human(authorization)
    if human is not None and human.account_id:
        admin = human.role == "admin" or human.company_role == "admin"
        return Caller(name=f"acct:{human.account_id}", account_id=human.account_id, admin=admin)
    if not token:
        raise AuthError(401, "토큰이 필요하다 (Authorization: Bearer 토큰)")
    check_worker(authorization)  # 모르는 토큰은 401·에이전트 토큰은 403 그대로
    err = AuthError(
        403,
        "로그인 계정이 정해진 토큰만 받는다 → 내 연결에서 개인 MCP 연결 토큰을 발급해 쓴다",
    )
    err.code = SESSION_REQUIRED
    raise err
