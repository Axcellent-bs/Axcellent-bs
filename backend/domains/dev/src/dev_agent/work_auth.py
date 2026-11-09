"""작업 대기열(`/work/*`·MCP) 전용 토큰: `DEV_WORK_TOKENS="이름:토큰,이름:토큰"`.

개발자가 `claude mcp add --header ...`로 넣은 토큰은 개발자 PC의 Claude Code 설정
(`~/.claude.json`)에 평문으로 남는다. 같은 PC의 다른 앱이 이 파일을 읽거나 고칠 수 있다
(예: 2026-11 Postman 데스크톱이 Claude Code 설정에 몰래 훅을 넣은 사건). 그래서 MCP에는
사람 결정(관문 승인·머지·재무) 권한이 있는 사람 토큰 대신 **작업 대기열만 쓸 수 있는 토큰**을 쓴다.

- 이 토큰은 작업 목록·가져가기·진행·PR 알리기·내려놓기만 한다. 다른 API(`check_human`·
  `check_admin`)는 이 토큰을 모르는 토큰(401)으로 본다.
- 토큰 이름이 가져간 사람이 된다 (사람 토큰과 같은 이름을 쓰면 화면의 "내 Claude"와 이어진다).
- 예전처럼 사람 토큰도 한동안 받는다. `DEV_WORK_ALLOW_HUMAN=0`이면 작업 토큰만 받는다.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header

from ax_workflow.auth import AuthError, check_human


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
