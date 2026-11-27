"""스스로 도는 주기 작업을 회사마다 돌리는 도우미 (회사 분리, 11/26).

요청 밖에서 도는 작업(교대 인수인계 타이머·매일 결산·마감 알림·기획안 자동 넘김·캠페인 자동 기록)은
요청 회사가 없어 늘 캣북스 기록만 보고 썼다. 그래서 누비투어 같은 새 회사는 이 작업이 아예 돌지
않았다. 이 도우미로 한 회차마다 회사 목록을 돈다.

- 돌 회사: 캣북스(예전 그대로) + 회사 연결이 켜진 다른 회사. 판매 수집(#1878)·변화 감지기(#1749)와
  같은 기준(회사 목록 → 그 회사 연결 파일이 켜짐)
- 캣북스 차례도 `company_scope.using(캣북스)`로 돈다(11/27: 회사 없음 = 모든 회사 통과를 막음)
- 다른 회사는 `company_scope.using(회사)` 안에서 돈다. 저장소가 그 회사 칸·파일만 읽고 쓴다
- 한 회사가 실패해도(예외) 다음 회사는 돈다. 취소(`CancelledError`)만 그대로 올린다
- 작업이 다른 에이전트를 부르는 요청을 만들면 `meta_tenant_id()`를 `RequestMeta.tenant_id`에 넣는다.
  안 넣으면 받는 쪽이 기본 회사(캣북스)로 처리한다
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ax_agent_template import companies, company_connection, company_scope
from ax_contracts.schemas.common import TENANT_ID_DEFAULT

logger = logging.getLogger(__name__)


def job_companies() -> list[str]:
    """이번 회차에 돌 회사: 캣북스 + 회사 연결이 켜진 다른 회사(정식 id, 중복 없음).

    회사 목록을 못 읽거나 한 회사 연결 파일이 깨졌으면 그 회사만 뺀다(캣북스는 늘 돈다).
    """
    out = [company_scope.CATBOOKS]
    try:
        ids = companies.company_ids()
    except Exception:  # noqa: BLE001 - 회사 목록을 못 읽어도 캣북스는 돈다
        logger.warning("회사 목록을 읽지 못해 캣북스만 돈다", exc_info=True)
        return out
    for raw in ids:
        cid = company_scope.scope_id(raw)
        if cid in out:
            continue
        try:
            found = company_connection.read_connection(cid)
        except Exception:  # noqa: BLE001 - 연결 파일이 깨진 회사는 이번 회차에서 뺀다
            continue
        if found is not None and found.enabled:
            out.append(cid)
    return out


def meta_tenant_id(company_id: str | None = None) -> str:
    """주기 작업이 만드는 요청의 `RequestMeta.tenant_id`: 캣북스는 예전 값, 다른 회사는 그 회사 id.

    `company_id`를 안 주면 지금 회사(`company_scope.active()`, 없으면 캣북스).
    """
    cid = company_scope.scope_id(company_id if company_id is not None else company_scope.active())
    return TENANT_ID_DEFAULT if cid == company_scope.CATBOOKS else cid


async def for_each_company[T](
    run: Callable[[str], Awaitable[T]], label: str = "주기 작업"
) -> dict[str, T]:
    """회사마다 `run(회사 id)`를 한 번씩. 회사 → 결과(실패한 회사는 빠짐)."""
    results: dict[str, T] = {}
    for cid in job_companies():
        try:
            # 캣북스 차례도 캣북스를 건다(11/27: 회사 없음 = 모든 회사 통과를 막음)
            with company_scope.using(cid):
                results[cid] = await run(cid)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 한 회사 실패가 다른 회사를 막지 않게
            logger.exception("%s: %s 회사에서 실패, 다음 회사는 그대로 돈다", label, cid)
    return results
