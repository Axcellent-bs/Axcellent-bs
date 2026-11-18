"""판매 서버(shop-api) 관리 읽기 중계: 화면이 판매 서버를 직접 부르지 않고 이 서버만 부르게 해요.

예전에는 화면이 빌드에 넣은 주소(`VITE_SHOP_ADMIN_API_URL`)와 토큰으로
판매 서버를 바로 불렀어요.
그러면 회사 연결에서 바꾼 주소를 따르지 못하고, 판매 서버 토큰이 모든 브라우저에 실려요.
이제는 이 서버가 `agent_url("shop-api")`(회사 연결 파일 → `AX_SHOP_API_URL` → 기본 주소)로
서버 쪽 열람 토큰을 붙여 불러요.

- 읽기(GET)만 중계해요. 정해 둔 경로만 열고, 아무 경로나 넘기는 중계는 하지 않아요.
- 토큰은 판매 서버의 읽기 전용 열람 토큰(`SHOP_VIEWER_TOKEN`, 판매 서버와 같은 값)이에요.
  열람 토큰은 총괄 사건·부서 읽기, 받은 기획안 읽기, 장애 상태 읽기만 통하고 쓰기는 모두 403이에요.
  목 모드에서 값이 없으면 `mock:viewer`를 써요.
  실제 모드에서 없으면 503 `SHOP_VIEWER_TOKEN_NOT_SET`이에요.
- 장애 넣기·빼기(POST·DELETE)는 중계하지 않아요. 운영자 명령(`scripts/shop_fault.sh`)이에요.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query

from ax_agent_template.config import agent_url
from ax_dashboard_api.auth import require_human_for_writes
from ax_dashboard_api.envelope import ApiError, is_mock, ok

TIMEOUT_SECONDS = 10.0
# 테스트에서 httpx.MockTransport로 바꿔 끼워요
TRANSPORT: httpx.AsyncBaseTransport | None = None
VIEWER_TOKEN_ENV = "SHOP_VIEWER_TOKEN"
# shop-api `VIEWER_TOKEN_MIN_LENGTH`와 같아요. 더 짧으면 판매 서버가 열람 토큰을 꺼 둬요
VIEWER_TOKEN_MIN_LENGTH = 24
# 판매 서버 목록 상한과 같아요 (shop-api `marketing_client.LIST_LIMIT_MAX`)
LIST_LIMIT_MAX = 100

# 화면 경로(`/api/dashboard/shop` 아래) → 판매 서버 경로. 이 밖의 경로는 중계하지 않아요
UPSTREAM = {
    "orchestrator_incidents": "/api/shop/admin/orchestrator/incidents",
    "orchestrator_departments": "/api/shop/admin/orchestrator/departments",
    "marketing_briefs": "/api/shop/admin/marketing/briefs",
    "marketing_outage_sns_drafts": "/api/shop/admin/marketing/outage-sns-drafts",
    "banners": "/api/shop/banners",
    "faults": "/api/shop/admin/faults",
}

router = APIRouter(prefix="/api/dashboard/shop", dependencies=[Depends(require_human_for_writes)])


def _token() -> str:
    token = os.getenv(VIEWER_TOKEN_ENV, "").strip()
    if token and len(token) < VIEWER_TOKEN_MIN_LENGTH:
        # 값은 문구에 넣지 않아요
        raise ApiError(
            503,
            "SHOP_VIEWER_TOKEN_TOO_SHORT",
            f"{VIEWER_TOKEN_ENV}가 {VIEWER_TOKEN_MIN_LENGTH}자보다 짧아 판매 서버가 받지 않아요",
        )
    if token:
        return token
    if is_mock():
        return "mock:viewer"
    raise ApiError(
        503,
        "SHOP_VIEWER_TOKEN_NOT_SET",
        "판매 서버 열람 토큰(SHOP_VIEWER_TOKEN)이 화면 중계 서버에 설정되지 않았어요",
    )


def not_connected_error(res: httpx.Response) -> ApiError | None:
    """사이트 변환기의 503 NOT_CONFIGURED(회사 사이트 연결 전)는 그 뜻 그대로 화면에 넘긴다."""
    if res.status_code != 503:
        return None
    try:
        error = res.json().get("error") or {}
    except (ValueError, AttributeError):
        return None
    if not isinstance(error, dict) or error.get("code") != "NOT_CONFIGURED":
        return None
    message = error.get("message") or "회사 사이트가 아직 연결돼 있지 않아요"
    return ApiError(503, "NOT_CONFIGURED", str(message))


def _upstream_error(res: httpx.Response) -> ApiError:
    """판매 서버의 실패 응답을 화면 오류로 바꿔요.

    판매 서버 문장은 판매 서버 중계(총괄·마케팅)가 준 502·504일 때만 그대로 써요.
    """
    status = res.status_code
    if (not_connected := not_connected_error(res)) is not None:
        return not_connected
    if status in (401, 403):
        return ApiError(
            503,
            "UPSTREAM_AUTH",
            "판매 서버가 화면 중계 서버의 열람 토큰을 받지 않았어요 (SHOP_VIEWER_TOKEN 확인)",
        )
    if status == 404:
        return ApiError(
            404, "UPSTREAM_NOT_FOUND", "판매 서버에 이 경로가 없어요 (판매 서버 버전 확인)"
        )
    if status in (502, 504):  # 판매 서버가 총괄·마케팅에 닿지 못한 경우
        try:
            error = res.json().get("error") or {}
        except (ValueError, AttributeError):
            error = {}
        message = error.get("message") if isinstance(error, dict) else None
        return ApiError(status, "UPSTREAM_RELAY", message or f"판매 서버 중계 실패 (HTTP {status})")
    return ApiError(502, "UPSTREAM_ERROR", f"판매 서버에서 오류가 났어요 (HTTP {status})")


async def relay_get(key: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """정해 둔 판매 서버 경로를 읽어 `{data, meta}`로 돌려줘요. 판매 서버의 meta는 그대로 실어요."""
    path = UPSTREAM[key]  # 정해 둔 키만 받아요 (KeyError는 코드 실수)
    headers = {"Authorization": f"Bearer {_token()}"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=TRANSPORT) as client:
            res = await client.get(f"{agent_url('shop-api')}{path}", params=params, headers=headers)
    except httpx.TimeoutException as error:
        raise ApiError(
            504, "UPSTREAM_TIMEOUT", f"판매 서버가 {TIMEOUT_SECONDS:g}초 안에 응답하지 않았어요"
        ) from error
    except httpx.HTTPError as error:
        raise ApiError(
            502,
            "UPSTREAM_UNAVAILABLE",
            "판매 서버에 연결하지 못했어요(꺼져 있거나 회사 연결의 주소가 다를 수 있어요)",
        ) from error
    if res.status_code >= 400:
        raise _upstream_error(res)
    try:
        body = res.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or "data" not in body:
        raise ApiError(502, "UPSTREAM_ERROR", "판매 서버가 {data} 모양이 아닌 응답을 보냈어요")
    out = ok(body["data"])
    upstream_meta = body.get("meta")
    if isinstance(upstream_meta, dict):
        out["meta"] = {**upstream_meta, **out["meta"]}
    return out


@router.get("/orchestrator/incidents")
async def orchestrator_incidents() -> dict:
    """총괄 사건 목록 (판매 서버 총괄 읽기 중계)."""
    return await relay_get("orchestrator_incidents")


@router.get("/orchestrator/departments")
async def orchestrator_departments() -> dict:
    """부서 카드·사용량 (판매 서버 총괄 읽기 중계)."""
    return await relay_get("orchestrator_departments")


@router.get("/marketing/briefs")
async def marketing_briefs(limit: int = Query(50, ge=1, le=LIST_LIMIT_MAX)) -> dict:
    """마케팅이 받은 기획안 목록."""
    return await relay_get("marketing_briefs", {"limit": limit})


@router.get("/marketing/outage-sns-drafts")
async def marketing_outage_sns_drafts(
    limit: int = Query(20, ge=1, le=LIST_LIMIT_MAX), include_merged: bool = False
) -> dict:
    """장애 사과·보상 SNS 글 초안 목록. 초안만 읽고 게시하지 않아요."""
    params: dict[str, Any] = {"limit": limit}
    if include_merged:
        params["include_merged"] = "true"
    return await relay_get("marketing_outage_sns_drafts", params)


@router.get("/banners")
async def banners() -> dict:
    """쇼핑몰에 게시 중인 공지 배너."""
    return await relay_get("banners")


@router.get("/faults")
async def faults() -> dict:
    """결제 장애 상태(읽기만). 넣기·빼기는 중계하지 않아요."""
    return await relay_get("faults")
