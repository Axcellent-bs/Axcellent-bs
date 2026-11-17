"""평가가 읽는 이슈 카드를 가져온다.

이슈·결정·사례 카드는 아카이브 AI가 자기 저장소에 쌓는다(결정 기록을 인사에서 분리한 뒤).
실제 모드에서 인사가 자기 저장소(`hr.db`)를 읽으면 새 이슈가 평가에 나타나지 않으므로, 아카이브의
읽기 skill `list_cards`를 A2A로 불러 읽는다. 목 모드(테스트·`HR_MOCK=1`)에서는 예전처럼
인사 저장소를 읽는다.

아카이브에 닿지 않으면 빈 목록으로 얼버무리지 않고 `UpstreamError`를 올려 호출한 쪽이 "연결 문제"로
답하게 한다(없는 이슈를 없다고 하면 평가가 비어 보이는 것과 구분이 안 된다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    AgentRequest,
    Card,
    ListCardsOutput,
    RequestMeta,
    TaskStatus,
)
from hr_agent.clients.errors import UpstreamError
from hr_agent.services import Services

CALL_TIMEOUT_SECONDS = 15
TARGET = "archive"
SKILL = "list_cards"
LIMIT = 200  # 이슈마다 가장 최근 카드 하나를 고르려면 여유 있게 읽는다


async def load_cards(svc: Services, meta: RequestMeta) -> list[Card]:
    injected = svc.extras.get("eval_cards")
    if injected is not None:
        return await injected(meta)  # type: ignore[no-any-return]
    if svc.settings.mock:
        return await svc.memory.list_cards(states=["active"])
    return await _from_archive(meta)


async def _from_archive(meta: RequestMeta) -> list[Card]:
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS))
    sent = AgentRequest(
        meta=RequestMeta(
            context_id=meta.context_id,
            tenant_id=meta.tenant_id,
            token="replaced-by-call_agent",
            # 아카이브가 판정을 저장하다 부른 호출(아카이브 → 인사)에서 다시 아카이브를 읽으므로
            # 경로를 이으면 순환(LOOP_DETECTED)으로 거절된다. 짧은 읽기 조회라 새 경로로 시작한다
            path=[],
            deadline=deadline,
        ),
        skill=SKILL,
        input={"limit": LIMIT},
    )
    try:
        result = await call_agent(TARGET, sent)
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("archive", "이슈 카드를 읽지 못했다") from exc
    if result.status != TaskStatus.COMPLETED:
        raise UpstreamError("archive", "이슈 카드를 읽지 못했다")
    try:
        return ListCardsOutput.model_validate(result.output).cards
    except ValueError as exc:
        raise UpstreamError("archive", "카드 목록 형식 오류") from exc
