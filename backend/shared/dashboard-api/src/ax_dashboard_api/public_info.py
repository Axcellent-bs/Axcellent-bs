"""로그인 전 화면이 읽는 공개 정보 (사람 토큰 없이 열린 경로, `auth.PUBLIC_ROUTES`).

dashboard-api는 읽기에도 사람 토큰을 요구한다. 그런데 로그인·회원가입 화면은 로그인 전이라
토큰이 없고, "어느 회사에 설치됐는지"를 보고 일반회원 로그인을 받을지
(회사 연결 전에는 관리자만) 정한다 (화면 `demo/LoginPage.tsx`·`demo/session.tsx`의 `canOpen`).
그래서 회사 이름만 여기서 따로 연다.

- `GET /api/dashboard/auth/company` → `{company: {id, name, site_url} | null}`
  연결 키로 연결한 회사가 먼저, 없으면 켜져 있는 사이트 연결의 회사 설정
  연결 키(가린 값 포함)·메모·업종·사내망·판매 서버 주소·연결 기록은 넣지 않는다
"""

from __future__ import annotations

from fastapi import APIRouter

from ax_dashboard_api import company_connection, connection_keys
from ax_dashboard_api.envelope import ok

router = APIRouter(prefix="/api/dashboard/auth")


@router.get("/company")
def public_company() -> dict:
    found = connection_keys.public_company() or company_connection.public_site_company()
    return ok({"company": found})
