"""회사 연결 API: 시스템 관리 > 회사 연결에서 고객사 판매 서버(shop-api 규격) 주소를 정한다.

- `GET    /api/dashboard/company-connection`: 지금 쓰는 주소와 어디서 정했는지
  (`connection`·`env`·`default`)
- `POST   /api/dashboard/company-connection/test`: 저장하지 않고 연결 확인만
- `PUT    /api/dashboard/company-connection`: 다시 확인하고, 모두 통과하면 저장
  (모든 서비스가 재시작 없이 따라감)
- `DELETE /api/dashboard/company-connection`: 연결을 지우고 원래 주소(`AX_SHOP_API_URL`·기본값)로

쓰기(POST·PUT·DELETE)는 사람 토큰만 된다(`require_human_for_writes`).
확인은 서버가 그 주소로 직접 GET을 보내므로 아무 주소나 부를 수 있다.
사내 설치·사람 토큰 전제이고, 계정이 든 주소·http(s) 밖의 주소는 받지 않는다.
연결 확인은 토큰 없이 읽히는 경로(상태·상품·구매자)만 본다. 숫자는 받은 그대로만 쓴다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ax_agent_template import company_connection as store
from ax_agent_template.config import agent_url
from ax_dashboard_api.auth import require_human_for_writes
from ax_dashboard_api.envelope import ApiError, ok

TIMEOUT_SECONDS = 5.0
# 테스트에서 httpx.MockTransport로 바꿔 끼운다
TRANSPORT: httpx.AsyncBaseTransport | None = None

logger = logging.getLogger("ax.dashboard.company_connection")

router = APIRouter(
    prefix="/api/dashboard/company-connection",
    dependencies=[Depends(require_human_for_writes)],
)


class ConnectionIn(BaseModel):
    url: str = Field(min_length=1, max_length=store.MAX_URL_LENGTH)


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "ok": self.ok, "detail": self.detail}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _valid(url: str) -> str:
    try:
        return store.validate_shop_url(url)
    except ValueError as exc:
        raise ApiError(422, "INVALID_URL", str(exc)) from exc


async def _get(client: httpx.AsyncClient, url: str) -> tuple[Any | None, str | None]:
    """JSON 본문 또는 실패 이유."""
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        return None, f"{TIMEOUT_SECONDS:g}초 안에 답이 없음"
    except httpx.HTTPError as exc:
        return None, f"연결할 수 없음 ({type(exc).__name__})"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    try:
        return response.json(), None
    except ValueError:
        return None, "JSON 응답이 아님"


CheckKey = Literal["health", "products", "users"]
# 목록 확인: 항목, 이름, 경로, 줄마다 있어야 하는 칸
LIST_CHECKS: tuple[tuple[CheckKey, str, str, tuple[str, ...]], ...] = (
    ("products", "상품", "/api/shop/products", ("product_id", "name", "price")),
    ("users", "구매자", "/api/shop/users", ("buyer_id",)),
)


def _list_problem(body: Any, need: tuple[str, ...]) -> tuple[int, str | None]:
    """목록 줄 수와 모양 문제(없으면 None)."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return 0, "응답의 data가 목록이 아님"
    for row in data:
        missing = [k for k in need if not isinstance(row, dict) or k not in row]
        if missing:
            return len(data), f"{', '.join(missing)} 칸이 없음"
    return len(data), None


async def run_checks(url: str) -> list[Check]:
    """판매 서버 상태·상품·구매자 읽기를 확인한다. 항목마다 통과 여부와 이유."""
    checks: list[Check] = []
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=TRANSPORT
    ) as client:
        body, error = await _get(client, f"{url}/health")
        data = body.get("data") if isinstance(body, dict) else None
        status = data.get("status") if isinstance(data, dict) else None
        if error:
            checks.append(Check("health", "서버 응답", False, error))
        elif status != "ok":
            checks.append(Check("health", "서버 응답", False, "상태가 ok가 아님"))
        else:
            checks.append(Check("health", "서버 응답", True, "응답 정상"))
        for key, name, path, need in LIST_CHECKS:
            label = f"{name} 읽기"
            body, error = await _get(client, f"{url}{path}")
            if error:
                checks.append(Check(key, label, False, error))
                continue
            count, problem = _list_problem(body, need)
            if problem:
                checks.append(Check(key, label, False, problem))
            else:
                checks.append(Check(key, label, True, f"{name} {count:,}건 읽음"))
    return checks


def _state() -> dict[str, Any]:
    found = store.read_connection()
    if found:
        source = "connection"
    elif os.getenv("AX_SHOP_API_URL"):
        source = "env"
    else:
        source = "default"
    return {
        "shop_api_url": agent_url("shop-api"),
        "source": source,
        "connection": found.as_dict() if found else None,
        "editable": bool(store.connection_file()),
    }


@router.get("")
def get_connection() -> dict:
    return ok(_state())


@router.post("/test")
async def test_connection(body: ConnectionIn) -> dict:
    url = _valid(body.url)
    checks = await run_checks(url)
    return ok(
        {
            "url": url,
            "ok": all(c.ok for c in checks),
            "checks": [c.as_dict() for c in checks],
            "checked_at": _now(),
        }
    )


@router.put("")
async def save_connection(body: ConnectionIn, request: Request) -> dict:
    if not store.connection_file():
        raise ApiError(
            409, "CONNECTION_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다"
        )
    url = _valid(body.url)
    checks = await run_checks(url)
    failed = [c for c in checks if not c.ok]
    if failed:
        reasons = "; ".join(f"{c.label}: {c.detail}" for c in failed)
        raise ApiError(
            422, "CONNECTION_CHECK_FAILED", f"연결 확인에 실패해 저장하지 않았다 ({reasons})"
        )
    human = getattr(request.state, "human", "unknown")
    store.save_connection(url, verified_at=_now(), updated_by=human)
    logger.info("회사 연결 저장: %s (by %s)", url, human)
    return ok({**_state(), "checks": [c.as_dict() for c in checks]})


@router.delete("")
def delete_connection(request: Request) -> dict:
    removed = store.clear_connection()
    if removed:
        logger.info("회사 연결 해제 (by %s)", getattr(request.state, "human", "unknown"))
    return ok(_state())
