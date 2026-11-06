"""인사 AI와 실제로 주고받는다 (인사 연동 기획안 1·2·3단계, 2026-11-02).

기획·개발 서비스가 같이 쓴다. 개발은 PR 머지(`pr_merged`)·요청 완료(`request_closed`)를 보낸다.

입구(`record`): 결정 추출·기획서 확정 → 인사 `record_case`.
출구(`check_plan`): 기획서 초안 → 인사 `check_plan`, 기각된 안·확정 결정과 겹치는지 묻는다.
판정은 기획 쪽 자동 확정 규칙이 한다. 인사 답은 참고 정보(경고·근거 카드)다.

결정 추출(`decision_extracted`)·기획서 확정(`spec_confirmed`) 때 인사 `record_case`를 A2A로
부른다. 인사가 업무 결정 카드로 쌓고, 같은 결정이면 인사 쪽 중복 병합이 정리한다.

- `cross_check.py`와 같은 방식: 호출자(`caller`)를 바꿔 끼울 수 있고, 목 모드 기본값
  (`default_client`)은 인사를 부르지 않는다 (`None`).
- 30초 안에 답이 없거나 실패하면 지어내지 않고 실패로 돌려준다. 확정·머지 같은 본 작업은
  되돌리지 않는다 (서비스가 "인사 기록 실패"만 남긴다).
- 멱등 키는 기획 쪽 `<id>:<종류>`. 인사는 `context_id·종류·첫 근거`로 다시 거르므로 결정 번호를
  첫 근거로 보낸다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ax_agent_template import config as template_config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    TENANT_ID_DEFAULT,
    AgentRequest,
    AgentResult,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.hr import CheckPlanInput, CheckPlanOutput, PlanDraft, RecordCaseOutput
from ax_contracts.schemas.record import RecordCase

HR_TIMEOUT_SECONDS = 30.0

AgentCaller = Callable[[str, AgentRequest], Awaitable[AgentResult]]


@dataclass
class HrRecordResult:
    """인사 기록 결과. `ok`가 False면 `reason`에 못 한 까닭을 적는다."""

    ok: bool
    outcome: str | None = None  # card_created · candidate · record_only · duplicate
    card_id: str | None = None
    reason: str | None = None

    @property
    def text(self) -> str:
        if not self.ok:
            return f"인사 기록 실패: {self.reason}"
        label = {
            "card_created": "카드 생성",
            "candidate": "후보 카드",
            "record_only": "기록만",
            "duplicate": "이미 기록됨",
        }.get(self.outcome or "", self.outcome or "기록됨")
        return f"인사 {label}" + (f" {self.card_id}" if self.card_id else "")


@dataclass
class PlanCheck:
    """`check_plan` 결과. `verdict`가 unknown이면 확인 못 함 (자동 확정하지 않는다)."""

    verdict: str  # ok · warn · unknown
    card: str | None = None  # 첫 근거 카드
    message: str | None = None  # 경고 문구 또는 못 한 까닭

    @property
    def text(self) -> str:
        if self.verdict == "ok":
            return "② 인사 기각 이력 확인 → 없음" + (f" ({self.message})" if self.message else "")
        if self.verdict == "warn":
            return "② 인사 기각 이력 확인 → 비슷한 기각 이력 있음" + (
                f"\n{self.message}" if self.message else ""
            )
        return f"② 인사 기각 이력 확인 → 확인 못 함\n{self.message}"


class HrClient:
    """인사 `record_case`를 부른다. `caller`는 `call_agent`와 같은 모양 (시험에서 바꿔 끼운다)."""

    def __init__(
        self,
        caller: AgentCaller | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        timeout: float = HR_TIMEOUT_SECONDS,
    ):
        self._call = caller or (lambda target, req: call_agent(target, req))
        self.tenant_id = tenant_id
        self.timeout = timeout

    def _meta(self, ctx: str, idempotency_key: str | None = None) -> RequestMeta:
        return RequestMeta(
            context_id=ctx,
            tenant_id=self.tenant_id,
            token="replaced-by-call_agent",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=self.timeout),
            idempotency_key=idempotency_key,
        )

    async def _ask(self, req: AgentRequest) -> AgentResult | str:
        """인사에 묻는다. 못 받으면 까닭(문자열)을 돌려준다."""
        try:
            out = await asyncio.wait_for(self._call("hr", req), self.timeout)
        except TimeoutError:
            return f"{int(self.timeout)}초 안에 응답 없음"
        except Exception as exc:  # 서비스 꺼짐·연결 실패
            return f"호출 실패 ({type(exc).__name__})"
        if out.status != TaskStatus.COMPLETED:
            return f"{out.status.value} {out.outcome or ''}".strip()[:200]
        return out

    async def record(self, record: RecordCase, idempotency_key: str) -> HrRecordResult:
        meta = self._meta(record.context_id, idempotency_key)
        req = AgentRequest(meta=meta, skill="record_case", input=record.model_dump(mode="json"))
        out = await self._ask(req)
        if isinstance(out, str):
            return HrRecordResult(False, reason=out)
        try:
            parsed = RecordCaseOutput.model_validate(out.output or {})
        except ValidationError:
            # 출력 전환 전의 인사라도 완료면 기록된 것으로 본다 (카드 번호만 모름)
            return HrRecordResult(True, outcome=out.outcome)
        return HrRecordResult(True, outcome=parsed.outcome, card_id=parsed.card_id)

    async def check_plan(self, ctx: str, draft: PlanDraft, untrusted: dict[str, str]) -> PlanCheck:
        """초안을 인사 `check_plan`에 보낸다. 초안 속 외부 문구는 `untrusted`로 따로 보낸다."""
        inp = CheckPlanInput(draft=draft).model_dump(mode="json")
        req = AgentRequest(meta=self._meta(ctx), skill="check_plan", input=inp, untrusted=untrusted)
        out = await self._ask(req)
        if isinstance(out, str):
            return PlanCheck("unknown", message=out)
        try:
            parsed = CheckPlanOutput.model_validate(out.output or {})
        except ValidationError:
            return PlanCheck("unknown", message="check_plan 출력이 계약과 다름")
        if parsed.no_related_cards:
            return PlanCheck("ok", message="기록에 없음")
        if parsed.verdict == "ok":
            return PlanCheck("ok")
        warns = [f for f in parsed.findings if f.level == "warn"] or parsed.findings
        first = warns[0] if warns else None
        card = next((c for f in warns for c in f.card_ids), None)
        if card is None and parsed.similar_cards:
            card = parsed.similar_cards[0].card_id
        return PlanCheck("warn", card=card, message=first.message if first else None)


def default_client(agent: str = "planning") -> HrClient | None:
    """목 모드면 인사를 부르지 않는다 (None → 예전처럼 관문·활동 기록만).

    기획(`planning`)·개발(`dev`)이 같이 쓴다 (분리 뒤 ax_workflow로 옮김)."""
    if template_config.is_mock(agent):
        return None
    return HrClient()
