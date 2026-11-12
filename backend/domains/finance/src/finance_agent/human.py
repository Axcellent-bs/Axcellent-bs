"""사람 토큰 읽기 (결산 확정·대사 메모처럼 사람이 하는 결정용).

형식은 안전 관문과 같다: `Authorization: Bearer <토큰>`.
- 설정된 토큰: `FINANCE_HUMAN_TOKENS="이름:토큰,이름:토큰"`(없으면 `SAFETY_HUMAN_TOKENS`를 읽는다.
  재무 화면은 안전 관문과 같은 사람 토큰 `VITE_SAFETY_HUMAN_TOKEN`을 쓴다)
- 목 모드에서는 `human:<이름>`도 받는다. 에이전트 토큰(`mock:`)은 항상 거부한다.

부서끼리 import하지 않으므로 safety의 `auth.py`를 쓰지 않고 같은 규칙을 여기 작게 다시 쓴다.

누가 했나(`by_label`): 지금은 모든 화면이 사람 토큰 하나("공용")를 같이 쓴다. 그래서 토큰 이름이
공용이면 화면이 보낸 로그인 계정 이름(`X-Ax-Actor-Name`, UTF-8 퍼센트 인코딩)과 접속 IP를 붙여
"관리자(192.168.0.22)"처럼 남긴다. 권한은 여전히 토큰으로만 정하고, 이 이름은 기록 표시용이다
(화면이 보낸 값이라 증명이 아니다). `by`는 그대로 둔다.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
from typing import Any
from urllib.parse import unquote

from ax_agent_template.config import is_mock


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _configured() -> dict[str, str]:
    raw = os.getenv("FINANCE_HUMAN_TOKENS") or os.getenv("SAFETY_HUMAN_TOKENS") or ""
    pairs: dict[str, str] = {}
    for item in raw.split(","):
        name, sep, token = item.strip().partition(":")
        if sep and name and token:
            pairs[token] = name
    return pairs


def human_from_authorization(authorization: str | None) -> str:
    """사람 이름. 토큰이 없거나 사람 토큰이 아니면 PermissionError."""
    token = bearer(authorization)
    if not token or token.startswith("mock:"):
        raise PermissionError("사람 토큰이 필요하다 (Authorization: Bearer human:<이름>)")
    for known, name in _configured().items():
        if hmac.compare_digest(known, token):
            return name
    if is_mock("finance") and token.startswith("human:") and len(token) > len("human:"):
        return token.removeprefix("human:")
    raise PermissionError("사람 토큰을 검증할 수 없다")


SHARED_HUMAN = "공용"  # 모든 화면이 같이 쓰는 사람 토큰의 이름
ACTOR_HEADER = "X-Ax-Actor-Name"
ACTOR_NAME_MAX = 40
_UNSAFE = re.compile(r"[\x00-\x1f\x7f()<>]+")


def actor_name(raw: str | None) -> str | None:
    """화면이 보낸 로그인 계정 이름. 퍼센트 인코딩을 풀고 제어 문자·괄호를 빼고 40자로 자른다."""
    if not raw:
        return None
    name = " ".join(_UNSAFE.sub(" ", unquote(raw.strip(), errors="replace")).split())
    return name[:ACTOR_NAME_MAX].strip() or None


def _ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Any) -> str | None:
    """접속 IP. nginx가 넘긴 `X-Real-IP`, 없으면 `X-Forwarded-For` 첫 칸, 없으면 바로 붙은 주소."""
    headers = request.headers
    forwarded = (headers.get("x-forwarded-for") or "").split(",")[0]
    client = getattr(request, "client", None)
    for value in (headers.get("x-real-ip"), forwarded, getattr(client, "host", None)):
        if ip := _ip(value):
            return ip
    return None


def actor_label(person: str, request: Any) -> str | None:
    """공용 토큰이면 "<화면 계정 이름>(<IP>)", 이름이 없으면 "공용(<IP>)". 사람별 토큰이면 None."""
    if person != SHARED_HUMAN:
        return None
    name = actor_name(request.headers.get(ACTOR_HEADER)) or SHARED_HUMAN
    return f"{name}({client_ip(request) or 'IP 모름'})"


def label_fields(person: str, request: Any, key: str = "by_label") -> dict[str, str]:
    """기록에 더할 `{key: 표시 이름}`. 붙일 이름이 없으면 빈 dict라 예전 기록 모양 그대로다."""
    label = actor_label(person, request)
    return {key: label} if label else {}
