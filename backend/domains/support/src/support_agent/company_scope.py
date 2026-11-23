"""고객지원 저장소의 회사 나누기 (여러 회사 설계 7.2의 1·6단계, 2026-11-23).

data(#1589)·마케팅(#1557)의 `company_scope.py`와 같은 모양이다. domains끼리 import하지 않으므로
고객지원에 필요한 만큼만 옮겼다(문의·클레임 저장소는 메모리 + 기록 파일이라
SQLite 칸 도우미는 없다).

- 어느 회사인가:
  - skill 요청: 고객지원 핸들러 입구(`scoped`)가 `request.meta.tenant_id`를 그 요청 동안 정한다
    (contextvar. 그 요청에서 띄운 작업(asyncio task)도 같은 값을 잇는다)
  - 그 밖(문의 가져오기·보관 기간 정리·자동 정산 같은 스스로 도는 루틴): 정하지 않는다. 저장소는
    회사로 거르지 않고 전부 본다 (보관 기간 정리·개인정보 삭제는 모든 회사에 해야 한다)
  - 별칭 `shop-demo`는 `catbooks`로, 없거나 맞지 않는 값은 캣북스
- 저장소는 `visible(tenant_id)`로 건마다 거른다. 다른 회사 건은 없는 것처럼 보인다(404와 같은 결과)
"""

from __future__ import annotations

import contextvars
import functools
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ax_agent_template.companies import canonical_company_id
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

CATBOOKS = TENANT_ID_CATBOOKS
_ACTIVE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "support_company", default=None
)


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
    """고객지원 skill 핸들러 감싸기: 요청 meta.tenant_id를 그 요청의 회사로."""

    @functools.wraps(handler)
    async def run(request: Any) -> Any:
        meta = getattr(request, "meta", None)
        with using(getattr(meta, "tenant_id", None)):
            return await handler(request)

    return run
