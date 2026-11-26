"""배포 이력 미리 받기: 연결된 배포 도구의 최근 실행을 주기적으로 `DeploymentHistory`에 넣는다.

- 서버를 켤 때 한 번, 그 뒤 `INFRA_DEPLOY_POLL_SECONDS`(기본 120초)마다 받는다.
  진단은 받아 둔 이력을 바로 쓴다 (장애 순간에 외부 호출을 기다리지 않는다).
- 연결이 있는 서비스는 그 서비스의 시연 이력(`deployments.json`)을 도구 이력으로 바꾼다.
  연결이 없는 서비스는 시연 이력을 그대로 쓴다.
- 받기 실패(도구 오류, 보관함이 깨지거나 사라짐)는 로그만 남기고 이전 이력을 그대로 둔다.
  그래서 연결을 끊어도 받아 둔 도구 이력은 재시작 전까지 남는다.
- 목 모드에서는 켜지 않는다 (`main` lifespan). 주기 실행이라 LLM을 부르지 않는다.
- 회사마다 돈다(11/26 회사 분리): 캣북스(예전 그대로) + 회사 연결이 켜진 다른 회사.
  회사마다 그 회사 보관함의 배포 도구만 읽어 그 회사 배포 이력에 넣는다(`company_jobs`).
  한 회사가 실패해도 다음 회사는 돈다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from ax_agent_template import company_jobs
from ax_agent_template.tool_connections import ToolConnectionError
from infra_agent import deploy_tools
from infra_agent.deploy_tools import DeployTool
from infra_agent.deployments import DeploymentHistory

if TYPE_CHECKING:
    from infra_agent.runtime import Runtime

logger = logging.getLogger("ax.infra.deploy_poll")

POLL_ENV = "INFRA_DEPLOY_POLL_SECONDS"
DEFAULT_SECONDS = 120
MIN_SECONDS = 10
RECENT_LIMIT = 20


def interval_seconds(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get(POLL_ENV, "").strip()
    try:
        return max(MIN_SECONDS, int(raw))
    except ValueError:
        return DEFAULT_SECONDS


async def poll_once(history: DeploymentHistory, tools: list[DeployTool]) -> None:
    """도구마다 최근 실행을 받아 그 서비스 이력을 바꾼다. 실패한 도구는 이전 이력을 둔다."""
    for tool in tools:
        try:
            runs = await tool.recent(RECENT_LIMIT)
        except ToolConnectionError as exc:  # 문구에 비밀 값이 없다
            logger.warning(
                "배포 이력을 받지 못해 이전 이력을 쓴다: %s %s (%s)",
                tool.service,
                tool.tool,
                exc.message,
            )
            continue
        except Exception as exc:  # noqa: BLE001 - 한 도구가 안 돼도 다른 도구·진단은 이어 간다
            # 예외 문구는 남기지 않는다 (바깥 응답·주소가 섞일 수 있다)
            logger.warning(
                "배포 이력을 받지 못해 이전 이력을 쓴다: %s %s (%s)",
                tool.service,
                tool.tool,
                type(exc).__name__,
            )
            continue
        history.replace_service(tool.service, runs, tool=tool.tool, components=tool.components)


async def poll_company(runtime: Runtime, company: str) -> None:
    """한 회사 한 회차: 그 회사 보관함의 배포 도구로 그 회사 이력을 바꾼다.

    `company_jobs.for_each_company` 안에서 부른다
    (캣북스는 요청 회사 없이, 다른 회사는 그 회사로)."""
    # 보관함 파일 읽기가 이벤트 루프를 막지 않게 한다 (to_thread는 지금 회사를 이어 받는다)
    tools = await asyncio.to_thread(deploy_tools.all_tools)
    await poll_once(runtime.history_for(company), tools)


async def poll_all(runtime: Runtime) -> None:
    """한 회차: 회사마다 `poll_company`. 한 회사 실패가 다른 회사를 막지 않는다."""
    await company_jobs.for_each_company(
        lambda cid: poll_company(runtime, cid), label="배포 이력 받기"
    )


async def run_forever(runtime: Callable[[], Runtime]) -> None:
    """켤 때 바로, 그 뒤 주기마다 `poll_all`. 보관함은 회차마다 다시 읽는다(재시작 없이 반영)."""
    while True:
        try:
            await poll_all(runtime())
        except Exception as exc:  # noqa: BLE001 - 받기가 멈추면 이력이 낡는다: 다음 회차에 다시
            logger.warning("배포 이력 받기 회차 실패: %s", type(exc).__name__)
        await asyncio.sleep(interval_seconds())
