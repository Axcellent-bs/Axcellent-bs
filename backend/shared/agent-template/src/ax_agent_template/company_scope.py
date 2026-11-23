"""요청마다 회사 고르기 (여러 회사 5단계 = 요청마다 회사로 저장소 고르기, 2026-11-23).

고객지원(#1602) `company_scope.py`와 같은 함수 다섯 개(`scope_id`·`active`·`visible`·`using`·
`scoped`)를 공용으로 올렸다. 서버(`server.py`)가 skill 요청마다 `meta.tenant_id`로 `using`을
걸어 두므로 도메인 핸들러는 따로 감싸지 않아도 `active()`로 그 요청의 회사를 읽는다.

- 어느 회사인가:
  - skill 요청: 서버가 핸들러를 부르는 동안 `meta.tenant_id`의 정식 id (contextvar. 그 요청에서
    띄운 작업(asyncio task)도 같은 값을 잇는다)
  - 그 밖(스스로 도는 루틴·화면용 HTTP 경로): None. 저장소는 지금까지처럼 본다
  - 별칭 `shop-demo`는 `catbooks`로, 없거나 형식이 맞지 않는 값은 캣북스
- 회사 목록에 없는 값·형식이 틀린 값은 거절하지 않고 경고 로그만 남긴다(`checked`, 값마다 한 번).
  거절(`TENANT_REQUIRED`)은 설계 4장 강제 단계에서 켠다. 그전에는 경고만
- 저장소는 `visible(tenant_id)`로 건마다 거르거나 `active()`로 회사별 저장소를 고른다.
  다른 회사 건은 없는 것처럼 보인다(404와 같은 결과)
"""

from __future__ import annotations

import contextvars
import functools
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ax_agent_template.companies import canonical_company_id, is_company
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

CATBOOKS = TENANT_ID_CATBOOKS
# 경고를 한 번씩만 남길 값의 수 상한 (토큰을 가진 부서만 부르므로 많지 않다)
_WARN_LIMIT = 256

logger = logging.getLogger("ax.company_scope")
_ACTIVE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_company", default=None
)
_warned: set[str] = set()


def scope_id(company_id: str | None) -> str:
    """정식 회사 ID. 없거나 맞지 않으면 캣북스."""
    return canonical_company_id(company_id) or CATBOOKS


def active() -> str | None:
    """지금 요청의 회사. skill 요청 밖(스스로 도는 루틴)이면 None."""
    return _ACTIVE.get()


def visible(tenant_id: str | None) -> bool:
    """이 회사의 건을 지금 보여도 되는가. 요청 밖이면 늘 True."""
    company = _ACTIVE.get()
    return company is None or scope_id(tenant_id) == company


@contextmanager
def using(company_id: str | None) -> Iterator[str]:
    """이 블록 안에서 저장소가 그 회사 건만 보게 한다."""
    token = _ACTIVE.set(scope_id(company_id))
    try:
        yield _ACTIVE.get() or CATBOOKS
    finally:
        _ACTIVE.reset(token)


def scoped(handler: Callable[[Any], Awaitable[Any]]) -> Callable[[Any], Awaitable[Any]]:
    """skill 핸들러 감싸기: 요청 meta.tenant_id를 그 요청의 회사로.

    서버가 이미 걸어 주므로 새 코드는 쓰지 않아도 된다. 고객지원처럼 직접 감싼 곳이 그대로 돌게 둔다
    (같은 값을 한 번 더 거는 것이라 결과가 같다).
    """

    @functools.wraps(handler)
    async def run(request: Any) -> Any:
        meta = getattr(request, "meta", None)
        with using(getattr(meta, "tenant_id", None)):
            return await handler(request)

    return run


def checked(tenant_id: str | None) -> str:
    """요청 회사 값을 정식 id로. 틀린 값·목록에 없는 회사는 경고만 남기고 거절하지 않는다.

    - 형식이 틀리거나 비었으면 캣북스(`scope_id`와 같다)
    - 형식은 맞는데 회사 목록에 없으면 그 id 그대로 쓴다(그 회사 건만 보이니 다른 회사와 섞이지
      않는다). 회사를 목록에 넣기 전에 요청이 먼저 와도 데모가 멈추지 않게 한다
    """
    cid = canonical_company_id(tenant_id)
    if cid is None:
        _warn_once(f"bad:{tenant_id!r}", "요청 회사 값이 맞지 않아 캣북스로 본다: %r", tenant_id)
        return CATBOOKS
    if cid != CATBOOKS and not is_company(cid):
        _warn_once(f"unknown:{cid}", "회사 목록에 없는 요청 회사다(거절하지 않음): %s", cid)
    return cid


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _warned or len(_warned) >= _WARN_LIMIT:
        return
    _warned.add(key)
    logger.warning(message, *args)


def reset_for_tests() -> None:
    _warned.clear()
