"""dashboard-api 요청마다 회사 고르기 (여러 회사 설계 6.2·6.3, 2026-11-23).

회사 연결·변환기 저장소(`company_connection.read_connection`·`adapter_dir`)는 회사를 안 주면
`company_scope.active()`로 고른다. 이 미들웨어가 요청마다 그 값을 걸어 둔다.

- 변환기 `/site-adapter/<회사 ID>/api/...`·`/site-adapter/<회사 ID>/health`: 경로의 회사.
  경로에서 회사를 떼고(`/site-adapter/api/...`) 같은 라우터로 넘긴다. AI들은
  `config.agent_url("shop-api")`로 자기 요청 회사(meta.tenant_id)의 경로를 받는다
  (`company_connection.adapter_path`)
- 변환기 `/site-adapter/api/...`(회사 없음): 캣북스. 예전 주소 그대로
  (5단계 "거절 없이 경고만" 동안 회사 ID 없는 요청은 캣북스로 본다는 `company_scope` 규칙)
- 화면 `/api/dashboard/company-connection`·`/api/dashboard/site-adapter`: 로그인 세션의 회사.
  세션이 아닌 사람 토큰(공용 토큰)은 캣북스(예전 그대로)
- 판매 서버를 읽는 화면(회원·주문·집계 `live_views`, 판매 서버 중계 `shop_relay`): 같은 규칙.
  `agent_url("shop-api")`가 세션 회사의 연결을 따른다(다른 회사 관리자가 캣북스 주문을 보지 않게)
- 그 밖의 경로는 건드리지 않는다(회사 AI 키 등은 여러 회사 4단계에서 따로 고친다.
  격리 시험의 xfail이 그 자리를 지킨다)

`SessionCompanyMiddleware` 안쪽에서 돌아야 세션 회사를 읽는다(`main.py`에서 그보다 먼저 더한다.
Starlette는 나중에 더한 미들웨어가 바깥이다).
"""

from __future__ import annotations

import json
from typing import Any

from ax_agent_template import company_scope
from ax_agent_template.companies import canonical_company_id
from ax_agent_template.company_connection import CALLER_COMPANY_HEADER
from ax_dashboard_api.envelope import session_company

ADAPTER_PREFIX = "/site-adapter"
# `/site-adapter/` 바로 뒤에 와도 회사 ID가 아닌 이름 (예전 캣북스 경로 `api/...`·`health`)
RESERVED_SEGMENTS = frozenset({"api", "health"})
SCREEN_PREFIXES = (
    "/api/dashboard/company-connection",
    "/api/dashboard/site-adapter",
    # 판매 서버 읽기 화면 (`routes.py`의 live_views 경로, `shop_relay`)
    "/api/dashboard/users",
    "/api/dashboard/orders",
    "/api/dashboard/metrics",
    "/api/dashboard/shop",
    # 도구 연결 보관함(GitHub 앱·배포·DejaFix 등, 11/25 회사 분리): 회사를 안 걸면 캣북스
    # 파일을 읽어 누비투어 화면에 캣북스 GitHub 앱·배포 레포가 보였다
    "/api/dashboard/tool-connections",
)


def adapter_company(path: str) -> tuple[str, str] | None:
    """`/site-adapter/<회사 ID>/<나머지>`면 (정식 회사 ID, 회사를 뗀 경로). 아니면 None.

    회사 자리 다음이 `api/...` 또는 `health`일 때만 회사로 본다
    (`/site-adapter/api/shop/...`·`/site-adapter/health`는 예전 캣북스 주소).
    """
    if not path.startswith(ADAPTER_PREFIX + "/"):
        return None
    segment, _, rest = path[len(ADAPTER_PREFIX) + 1 :].partition("/")
    if segment in RESERVED_SEGMENTS:
        return None  # 예전 캣북스 주소의 자리 이름은 회사 ID로 읽지 않는다
    if not (rest == "health" or rest.startswith("api/")):
        return None
    cid = canonical_company_id(segment)
    if cid is None:
        return None
    return cid, f"{ADAPTER_PREFIX}/{rest}"


def _screen(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in SCREEN_PREFIXES)


def _caller_company(scope: Any) -> str | None:
    """호출 회사 머리글(`X-AX-Company`). 없으면 None(예전 호출은 그대로 통과)."""
    want = CALLER_COMPANY_HEADER.lower().encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key.lower() == want:
            return canonical_company_id(value.decode("latin-1").strip()) or "invalid"
    return None


async def _refuse(send: Any) -> None:
    body = json.dumps(
        {"error": {"code": "COMPANY_MISMATCH", "message": "호출 회사와 경로 회사가 달라요"}},
        ensure_ascii=False,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class CompanyRouteMiddleware:
    """요청의 회사를 `company_scope.using`으로 걸어 둔다 (변환기 경로·회사 연결 화면만)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        found = adapter_company(path)
        if found is not None:
            cid, rest = found
            caller = _caller_company(scope)
            if caller is not None and caller != cid:
                await _refuse(send)  # 경로 회사 ≠ 호출 회사(#1649)
                return
            inner = {**scope, "path": rest, "raw_path": rest.encode("utf-8")}
            with company_scope.using(cid):
                await self.app(inner, receive, send)
            return
        if path.startswith(ADAPTER_PREFIX + "/"):
            # 예전 캣북스 주소(`/site-adapter/api/...`)를 다른 회사가 부르면 막는다(#1649)
            caller = _caller_company(scope)
            if caller is not None and caller != company_scope.CATBOOKS:
                await _refuse(send)
                return
        if _screen(path):
            with company_scope.using(session_company()):
                await self.app(scope, receive, send)
            return
        # 그 밖의 화면 경로(주문·회원·사이트 자료 중계 등): 다른 회사 세션이면 그 회사로 읽는다.
        # 걸지 않으면 연결·사이트 주소가 설치 회사(캣북스) 것으로 골라졌다(11/24 누비투어 연결).
        # 캣북스 세션·공용 토큰·세션 없음은 예전 그대로(걸지 않음)
        company = session_company()
        if company and company_scope.scope_id(company) != company_scope.CATBOOKS:
            with company_scope.using(company):
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)
