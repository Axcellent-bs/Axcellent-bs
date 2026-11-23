"""OAuth 연결의 `state`: 어느 회사의 누가 시작한 연결인지 묶는다 (2026-11-23).

유튜브(#1586)와 앞으로 붙일 플랫폼 앱 연결(카페24·스마트스토어 등, 연결 유형 B)이 같이 쓴다.

왜 필요한가
- 플랫폼이 돌려보내는 콜백 주소는 로그인 없이 열려 있다(플랫폼이 Authorization을 붙이지 않는다).
  `state`에 회사만 적으면, 회사 X의 관리자가 받은 동의 화면 주소를 다른 회사·다른 사람에게 보내
  그 사람이 동의하게 만들 수 있다. 그러면 그 사람의 채널·쇼핑몰 토큰이 회사 X로 들어간다
- 그래서 콜백은 토큰을 바꾸지 않고 화면으로 돌려보내기만 한다. 화면이 로그인한 사람의 세션으로
  `complete`를 부르고, 서버가 "연결을 시작한 계정·회사 = 지금 로그인한 계정·회사"일 때만
  코드를 토큰으로 바꿔 그 회사 키 저장소에 넣는다

state 안: 공급자(`p`)·회사 ID(`c`)·시작한 계정 ID(`a`)·만료(`e`, 10분)·임의값(`n`).
서버 비밀(`AX_SESSION_SECRET`)로 HMAC 서명한다. 값(코드·토큰)은 state에 넣지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from ax_agent_template import human_auth
from ax_dashboard_api.envelope import ApiError

STATE_TTL = 600


@dataclass(frozen=True)
class LinkState:
    provider: str
    company_id: str
    account_id: str


def _secret() -> bytes:
    raw = os.getenv(human_auth.SESSION_SECRET_ENV, "")
    if len(raw) < 32:
        raise ApiError(
            409, "SESSION_SECRET_MISSING", "서버 로그인 설정이 없어 연결을 시작할 수 없어요"
        )
    return raw.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_state(provider: str, company_id: str, account_id: str, now: float | None = None) -> str:
    body = json.dumps(
        {
            "p": provider,
            "c": company_id,
            "a": account_id,
            "e": int((now or time.time()) + STATE_TTL),
            "n": secrets.token_hex(8),
        },
        separators=(",", ":"),
    )
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return f"{_b64(body.encode())}.{_b64(sig)}"


def read_state(state: str, provider: str, now: float | None = None) -> LinkState | None:
    """서명·만료·공급자가 맞으면 묶인 회사·계정, 아니면 None."""
    try:
        body_b64, sig_b64 = state.split(".", 1)
        body = _unb64(body_b64)
        want = hmac.new(_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(sig_b64)):
            return None
        data = json.loads(body)
    except (ValueError, ApiError):
        return None
    if not isinstance(data, dict) or int(data.get("e", 0)) < int(now or time.time()):
        return None
    p, c, a = data.get("p"), data.get("c"), data.get("a")
    if p != provider or not all(isinstance(v, str) and v for v in (c, a)):
        return None
    return LinkState(provider=provider, company_id=str(c), account_id=str(a))


def require_owner(
    state: str, provider: str, human: human_auth.Human, company_id: str
) -> LinkState:
    """`complete`에서: 연결을 시작한 계정·회사가 지금 로그인한 계정·회사와 같아야 한다."""
    found = read_state(state, provider)
    if found is None:
        raise ApiError(
            400, "BAD_STATE", "연결 정보가 맞지 않거나 10분이 지났어요. 다시 연결해 주세요"
        )
    if found.account_id != human.account_id or found.company_id != company_id:
        raise ApiError(
            403,
            "NOT_LINK_OWNER",
            "연결을 시작한 계정으로 로그인한 상태에서만 마칠 수 있어요. "
            "처음부터 다시 연결해 주세요",
        )
    return found
