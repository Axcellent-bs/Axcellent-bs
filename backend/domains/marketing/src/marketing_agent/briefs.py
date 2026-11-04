"""기획안(brief) 받기·보관·조회 (skill `receive_campaign_brief`, `list_campaign_briefs`,
`get_campaign_brief`). 프레임워크에 의존하지 않는다.

- 기획이 사람이 확정한 제안(`CampaignBriefInput`)을 보내면 마케팅이 받아 둔다. 받기만 하고
  캠페인은 시작하지 않는다(시작은 `start_campaign`, 재무 판단 + 안전 관문).
- `facts`·`target`은 받은 그대로 보관한다. 숫자를 다시 계산하거나 고치지 않는다.
- 같은 `proposal_id`는 한 번만 받는다. 다시 오면 `duplicate`이고 보관본은 바뀌지 않는다.
- `campaign_id = "cmp_" + proposal_id`. 제안 번호에서 정해지므로 재전송·다시 시작해도 같고,
  나중에 `start_campaign`도 같은 `campaign_id`를 쓴다(insight_id → proposal_id → campaign_id).
- 저장소는 메모리다. 서비스를 다시 시작하면 사라진다(영속 저장은 TODO).

거절 이유 코드(`reason_code`, 닫힌 집합):
- `unsupported_schema_version`: 마케팅이 읽을 수 없는 `schema_version`
- `invalid_proposal_id`: `proposal_id`가 비었거나 영문·숫자·`_`·`-`(64자 이하)가 아님
- `empty_title` / `empty_idea` / `empty_why`: 공백뿐이거나 빈 문자열
숫자(`facts`·`target`)는 거절 근거로 쓰지 않는다.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, UtcDatetime
from ax_contracts.schemas.planning import (
    PROPOSAL_SCHEMA_VERSION,
    CampaignBriefInput,
    CampaignBriefOutput,
)

CAMPAIGN_ID_PREFIX = "cmp_"
SUPPORTED_SCHEMA_VERSIONS = frozenset({PROPOSAL_SCHEMA_VERSION})
_PROPOSAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 200


def campaign_id_for(proposal_id: str) -> str:
    """제안 번호에서 캠페인 id를 정한다. 허용 문자만 쓰므로 서로 다른 번호는 겹치지 않는다."""
    return f"{CAMPAIGN_ID_PREFIX}{proposal_id}"


def rejection_reason(brief: CampaignBriefInput) -> str | None:
    """마케팅이 받을 수 없는 이유 코드. 받을 수 있으면 None이다."""
    if brief.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return "unsupported_schema_version"
    if not _PROPOSAL_ID.fullmatch(brief.proposal_id):
        return "invalid_proposal_id"
    for field, code in (
        ("title", "empty_title"),
        ("idea", "empty_idea"),
        ("why", "empty_why"),
    ):
        if not getattr(brief, field).strip():
            return code
    return None


class StoredBrief(AxModel):
    """받은 기획안. `brief`는 받은 그대로다."""

    campaign_id: str
    proposal_id: str
    status: Literal["received"] = "received"
    received_at: UtcDatetime
    brief: CampaignBriefInput


class ListCampaignBriefsInput(AxModel):
    limit: int = Field(default=LIST_LIMIT_DEFAULT, ge=1, le=LIST_LIMIT_MAX)


class CampaignBriefList(AxModel):
    items: list[StoredBrief]
    total: int
    unknown_reason: str | None = None


class GetCampaignBriefInput(AxModel):
    proposal_id: str | None = Field(default=None, min_length=1)
    campaign_id: str | None = Field(default=None, min_length=1)


class CampaignBriefView(AxModel):
    found: bool
    item: StoredBrief | None = None
    unknown_reason: str | None = None


class BriefStore:
    def __init__(self) -> None:
        self._by_proposal: dict[str, StoredBrief] = {}

    def receive(self, brief: CampaignBriefInput, at: datetime) -> CampaignBriefOutput:
        reason = rejection_reason(brief)
        if reason is not None:
            return CampaignBriefOutput(outcome="rejected", reason_code=reason)
        existing = self._by_proposal.get(brief.proposal_id)
        if existing is not None:  # 보관본은 그대로 둔다
            return CampaignBriefOutput(outcome="duplicate", campaign_id=existing.campaign_id)
        stored = StoredBrief(
            campaign_id=campaign_id_for(brief.proposal_id),
            proposal_id=brief.proposal_id,
            received_at=at,
            brief=brief,
        )
        self._by_proposal[brief.proposal_id] = stored
        return CampaignBriefOutput(outcome="accepted", campaign_id=stored.campaign_id)

    def list(self, limit: int) -> tuple[list[StoredBrief], int]:
        """받은 시각이 늦은 순(같으면 나중에 받은 것 먼저)."""
        newest_first = sorted(
            reversed(self._by_proposal.values()), key=lambda s: s.received_at, reverse=True
        )
        return newest_first[:limit], len(newest_first)

    def get(self, proposal_id: str | None, campaign_id: str | None) -> StoredBrief | None:
        if proposal_id is not None:
            found = self._by_proposal.get(proposal_id)
            if found is not None and campaign_id not in (None, found.campaign_id):
                return None
            return found
        return next((s for s in self._by_proposal.values() if s.campaign_id == campaign_id), None)
