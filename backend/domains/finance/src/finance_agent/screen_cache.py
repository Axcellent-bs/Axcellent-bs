"""재무 화면 응답 기억하기: 같은 장부로 만든 화면 응답은 다시 계산하지 않고 바로 준다.

화면은 30초마다 보이는 구역을 모두 다시 부르고, 장부도 30초마다 새로 모인다. 주문이 쌓이면
구역 하나를 계산하는 데 1초 안팎이 걸려(2026-11-06 서버, 주문 18만 건) 화면이 늘 기다렸다.

- 장부에서 나오는 조회(`CACHED`)만 기억한다. 사건·자기 테스트·장부(ledger)·내보내기는 매번 만든다
- 실제 모드에서만 쓴다. 목 모드(시드)는 매번 계산한다
- 기억한 응답이 지금 장부로 만든 것이고 `FRESH_SECONDS` 안이면 그대로 준다
- 장부가 바뀌었거나 오래됐으면 기억한 응답을 바로 주고(`X-Finance-Cache: stale`) 뒤에서 다시
  만든다. `MAX_STALE_SECONDS`가 지난 응답은 주지 않고 새로 만든다. 언제 모은 자료인지는
  응답의 `meta.collected_at`에 그대로 있다
- 새 장부가 모이면 최근 `WARM_WINDOW_SECONDS` 안에 누가 본 화면을 뒤에서 미리 다시 만든다
- GET이 아닌 요청(사람 결정)이 오면 기억한 응답을 모두 버린다. 결정한 뒤 화면은 새로 계산한다
- A2A(`/a2a`)는 재무 기록(`persist.fingerprint`)이 바뀐 경우에만 버린다. 대화방 읽기·보내기·
  조회 skill은 기록을 바꾸지 않아 기억한 화면을 그대로 둔다
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ax_agent_template.config import is_mock
from finance_agent import books as bk

logger = logging.getLogger("ax.finance.screen_cache")

CACHED = frozenset(
    f"/api/finance/{p}"
    for p in (
        "overview",
        "closing",
        "statements",
        "variance",
        "reconciliation",
        "settlements",
        "settlements/deadlines",
        "payables",
        "cashflow",
        "close-checklist",
        "campaigns",
        "insights",
        "tax/vat",
        "tax/seller-report",
        "tax/income-estimate",
        "bank",
    )
)
# 서버를 켰을 때 미리 만들어 두는 화면 (화면이 처음 열 때 보내는 조회와 같은 모양)
FIRST_SCREENS = (
    "/api/finance/overview",
    "/api/finance/close-checklist",
    "/api/finance/closing?period=day",
    "/api/finance/variance",
    "/api/finance/statements?period=month",
    "/api/finance/reconciliation?status=mismatch,missing_in_pg,fee_anomaly",
    "/api/finance/settlements",
    "/api/finance/settlements/deadlines",
    "/api/finance/payables",
    "/api/finance/cashflow",
)
FRESH_SECONDS = 30.0
MAX_STALE_SECONDS = 300.0
WARM_WINDOW_SECONDS = 300.0
REFRESH_HEADER = "x-finance-cache"
_DROP_HEADERS = {"content-length", REFRESH_HEADER}


@dataclass
class Entry:
    body: bytes
    status: int
    headers: dict[str, str]
    books_id: int | None
    version: int
    made: float
    seen: float


class ScreenCache:
    def __init__(self) -> None:
        self.entries: dict[str, Entry] = {}
        self.version = 0
        self.pending: dict[str, None] = {}  # 넣은 순서대로 만든다
        self.building: dict[str, asyncio.Event] = {}  # 지금 만드는 중인 화면
        self.worker: asyncio.Task[None] | None = None
        self.app: FastAPI | None = None

    def clear(self) -> None:
        self.entries.clear()
        self.pending.clear()
        self.version += 1

    # ---- 뒤에서 다시 만들기 (한 번에 하나씩: 계산끼리 서로 느려지지 않게)
    def schedule(self, keys: list[str]) -> None:
        self.pending.update(dict.fromkeys(keys))
        if self.app is not None and (self.worker is None or self.worker.done()):
            self.worker = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        assert self.app is not None
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://finance") as client:
            while self.pending:
                key = next(iter(self.pending))
                del self.pending[key]
                try:
                    await client.get(key, headers={REFRESH_HEADER: "refresh"}, timeout=120)
                except Exception as exc:  # 다음 요청이 다시 만든다
                    logger.warning("화면 응답을 다시 만들지 못함 %s: %s", key, type(exc).__name__)

    def on_new_books(self) -> None:
        """새 장부가 모였다. 최근에 누가 본 화면을 미리 다시 만든다."""
        now = time.monotonic()
        self.schedule([k for k, e in self.entries.items() if now - e.seen < WARM_WINDOW_SECONDS])


CACHE = ScreenCache()


async def prime() -> None:
    """서버를 켜면 장부를 한 번 모으고 첫 화면들을 미리 만든다. 첫 손님이 기다리지 않게."""
    try:
        await bk.load_books()
    except Exception as exc:  # 다음 요청이 다시 모은다
        logger.warning("처음 장부를 모으지 못함: %s", type(exc).__name__)
        return
    CACHE.schedule(list(FIRST_SCREENS))


def enabled() -> bool:
    return not is_mock("finance")


def _books_id() -> int | None:
    cached = bk._live_cache
    return id(cached[1]) if cached else None


def _records_fingerprint() -> str:
    from finance_agent import persist
    from finance_agent.handlers import state

    return persist.fingerprint(state())


def _key(request: Request) -> str:
    query = sorted(request.query_params.multi_items())
    return request.url.path + ("?" + "&".join(f"{k}={v}" for k, v in query) if query else "")


def _reply(entry: Entry, state: str) -> Response:
    return Response(
        content=entry.body,
        status_code=entry.status,
        headers=entry.headers | {"X-Finance-Cache": state},
    )


async def _make(request: Request, call_next: Any, key: str, version: int) -> Response:
    """새로 만든다. 같은 화면을 만드는 중인 요청이 있으면 끝날 때까지 기다리게 한다."""
    done = CACHE.building.setdefault(key, asyncio.Event())
    try:
        return await _make_now(request, call_next, key, version)
    finally:
        if CACHE.building.get(key) is done:
            del CACHE.building[key]
        done.set()


async def _make_now(request: Request, call_next: Any, key: str, version: int) -> Response:
    response = await call_next(request)
    body = b"".join([chunk async for chunk in response.body_iterator])
    headers = {k: v for k, v in response.headers.items() if k.lower() not in _DROP_HEADERS}
    if response.status_code == 200 and version == CACHE.version:
        now = time.monotonic()
        old = CACHE.entries.get(key)
        CACHE.entries[key] = Entry(
            body, 200, headers, _books_id(), version, now, old.seen if old else now
        )
    return Response(content=body, status_code=response.status_code, headers=headers)


def add_screen_cache(app: FastAPI) -> None:
    CACHE.app = app
    if CACHE.on_new_books not in bk.ON_NEW_BOOKS:
        bk.ON_NEW_BOOKS.append(CACHE.on_new_books)

    @app.middleware("http")
    async def screen_cache(request: Request, call_next: Any) -> Response:
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.url.path == "/a2a" and enabled():
                # A2A는 대화 읽기·보내기처럼 기록을 바꾸지 않는 요청이 대부분이다(대화방은 2초마다
                # 대화를 다시 읽는다). 재무 기록이 실제로 바뀐 경우에만 버린다
                before = _records_fingerprint()
                response = await call_next(request)
                if _records_fingerprint() != before:
                    CACHE.clear()
                return response
            response = await call_next(request)
            CACHE.clear()  # 사람 결정 뒤 화면은 새로 계산한다
            return response
        if request.url.path not in CACHED or not enabled():
            return await call_next(request)
        key, version = _key(request), CACHE.version
        if request.headers.get(REFRESH_HEADER) == "refresh":
            return await _make(request, call_next, key, version)
        if (
            key in CACHE.building
        ):  # 만드는 중이면 같은 결과를 기다린다(같은 계산을 두 번 하지 않는다)
            await CACHE.building[key].wait()
        entry = CACHE.entries.get(key)
        now = time.monotonic()
        if entry is not None and entry.version == version:
            entry.seen = now
            age = now - entry.made
            if entry.books_id == _books_id() and age < FRESH_SECONDS:
                return _reply(entry, "hit")
            if age < MAX_STALE_SECONDS:
                CACHE.schedule([key])
                return _reply(entry, "stale")
        return await _make(request, call_next, key, version)
