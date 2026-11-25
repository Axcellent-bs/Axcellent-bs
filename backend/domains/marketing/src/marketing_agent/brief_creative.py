"""skill `prepare_brief_creative`: 넘어온 기획안의 영상 제작안으로 제작 기록(CR-…)을 만든다.

기획안 영상도 의뢰 영상과 같은 길(제작 기록 → 안전 관문 → Higgsfield)로 만들기 위한 연결이다
(11/25). 입력은 기획안 번호뿐이다. 제작안(`video_plan`)은 마케팅 보관본에서 읽는다.

하는 일:
- 회사 대표 캐릭터(기본)가 있으면 `request_creative.prepare_storyboard`를 그대로 불러 기록을
  만든다(시트 복사 + 장면 스토리보드, LLM 없음).
- 대표 캐릭터가 없으면 캐릭터 칸이 빈 기록을 만들어 같은 스토리보드를 붙인다.
- 스토리보드 그림은 화면의 스토리보드 만들기와 같은 규칙(`creative_render.maybe_start_board`:
  시트 그림이 있고 영상에 필요할 때만, 안전 관문 generate_media B·그림 크레딧 예약)으로 시작한다.

하지 않는 일: 영상 만들기·게시. 영상은 제작 기록의 영상 만들기(C 승인)로 한다.

멱등: 기획안마다 만든 기록 번호를 남긴다. 다시 불러도 같은 기록을 돌려준다(그 사이 대표 캐릭터가
바뀌어도 새로 만들지 않고, 그림도 다시 시작하지 않는다). 기록이 보관 한도로 지워졌으면 다시 만든다.
찾기와 만들기 사이에 `await`가 없어 동시에 두 번 눌러도 하나만 만든다.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.planning import VideoPlan
from marketing_agent import creative_records as _creative
from marketing_agent import creative_render as _render
from marketing_agent import request_creative
from marketing_agent.brief_media import context_id_for
from marketing_agent.briefs import BriefStorageError, BriefStore
from marketing_agent.media.ledger import MediaLedger

logger = logging.getLogger("marketing.brief_creative")

SKILL = "prepare_brief_creative"
NO_CHARACTER = "none"  # 기록 번호를 만들 때 캐릭터 자리에 쓰는 값
# 대표 캐릭터가 없는 기록의 시트. 지어낸 외형·색 없이 화면이 읽는 칸만 비워 둔다
NO_CHARACTER_SHEET: dict[str, Any] = {
    "summary_ko": "대표 캐릭터 없이 만드는 기획안 영상",
    "proportions": "",
    "palette": [],
    "panels": [],
    "consistency_en": "",
    "prompt_en": "",
    "negative_prompt_en": "",
}


class PrepareBriefCreativeInput(AxModel):
    proposal_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class PrepareBriefCreativeOutput(AxModel):
    proposal_id: str
    record_id: str
    character_record_id: str | None = None  # 쓴 대표 캐릭터. 없으면 None
    reused: bool = False  # 이미 만든 기록을 돌려줬다


def _failed(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(
        meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output
    )


def _create(brief_id: str, plan: VideoPlan) -> tuple[str, str | None]:
    """(만든 기록 번호, 쓴 대표 캐릭터 번호). 장면은 부르는 쪽이 먼저 확인한다."""
    store = _creative.store()
    context_id = context_id_for(brief_id)
    brands = store.brand_characters()
    if brands:
        character = str(brands[0]["record_id"])  # 첫 줄이 기본 캐릭터
        record_id, _ = request_creative.prepare_storyboard(character, context_id, plan)
        if record_id is not None:
            return record_id, character
    record_id = store.request_record_id(store.company_id, context_id, NO_CHARACTER)
    store.add_sheet(
        dict(NO_CHARACTER_SHEET), f"기획안 {brief_id} · 캐릭터 없음", None, record_id=record_id
    )
    board = request_creative.plan_storyboard(plan, NO_CHARACTER_SHEET)
    if board is not None:
        store.attach_storyboard(record_id, plan.script, len(board["shots"]), board)
    return record_id, None


async def prepare_brief_creative(
    request: AgentRequest, *, briefs: BriefStore, ledger: MediaLedger
) -> AgentResult:
    try:
        data = PrepareBriefCreativeInput.model_validate(request.input)
    except ValidationError as error:
        return _failed(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    pid = data.proposal_id
    try:
        stored = briefs.existing(pid)
    except BriefStorageError:
        return _failed(request, "storage_error")
    if stored is None:
        return _failed(request, "brief_not_found")
    plan = stored.brief.video_plan
    if plan is None:
        return _failed(request, "no_video_plan")
    try:
        store = _creative.store()
        made = store.brief_record(pid)
        if made is not None and (store.get(made[0]) or {}).get("storyboard"):
            record_id, character, reused = made[0], made[1], True
        else:
            if request_creative.plan_storyboard(plan, {}) is None:
                return _failed(request, "no_scenes")  # 빈 기록을 남기지 않는다
            record_id, character = _create(pid, plan)
            store.set_brief_record(pid, record_id, character)
            reused = False
    except sqlite3.Error:
        logger.exception("기획안 제작 기록을 만들지 못함: %s", pid)
        return _failed(request, "storage_error")
    if not reused:
        meta = request.meta.model_copy(update={"context_id": context_id_for(pid)})
        try:
            _render.maybe_start_board(ledger, meta, record_id)
        except Exception:  # noqa: BLE001 - 그림 시작 실패는 만든 기록을 막지 않는다
            logger.exception("기획안 제작 기록 그림을 시작하지 못함: %s", record_id)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=PrepareBriefCreativeOutput(
            proposal_id=pid,
            record_id=record_id,
            character_record_id=character,
            reused=reused,
        ).model_dump(mode="json"),
        evidence=Evidence(
            queries=[SKILL],
            data_summary=f"기획안 {pid} 제작 기록 {record_id}{' (이미 있음)' if reused else ''}",
            collected_at=datetime.now(UTC),
            ref_ids=[pid, record_id],
        ),
    )
