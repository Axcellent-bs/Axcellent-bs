"""기획안(brief) 받기·보관·조회 (skill `receive_campaign_brief`, `list_campaign_briefs`,
`get_campaign_brief`). 프레임워크에 의존하지 않는다.

- 기획이 사람이 확정한 제안(`CampaignBriefInput`)을 보내면 마케팅이 받아 둔다. 받기만 하고
  캠페인은 시작하지 않는다(시작은 `start_campaign`, 재무 판단 + 안전 관문).
- `facts`·`target`은 받은 그대로 보관한다. 숫자를 다시 계산하거나 고치지 않는다.
- 같은 `proposal_id`는 한 번만 받는다. 다시 오면 `duplicate`이고 보관본은 바뀌지 않는다.
- `campaign_id = "cmp_" + proposal_id`. 제안 번호에서 정해지므로 재전송·다시 시작해도 같고,
  나중에 `start_campaign`도 같은 `campaign_id`를 쓴다(insight_id → proposal_id → campaign_id).
- 저장은 안전 관문(`execute("receive_campaign_brief")`)이 `executed`를 돌려준 뒤에만 한다
  (핸들러 `handlers.receive_campaign_brief`). 검증·중복 확인은 관문 앞에서 하고, 중복은
  관문을 부르지 않는다.
- 저장소는 SQLite다. 환경변수 `MARKETING_BRIEFS_DB`가 가리키는 파일에 두면 서비스를 다시 시작해도
  받은 기획안이 남는다. 비어 있으면 메모리(`:memory:`)라 다시 시작할 때 사라진다(테스트·로컬 기본).
  기획 쪽은 이미 넘겼다고(`handoff.ok`) 남기므로, 파일 없이 다시 시작하면 마케팅 목록과 어긋난다.
  저장은 마케팅 전용 파일이고 다른 부서 DB는 쓰지 않는다. 값은 받은 그대로(JSON) 넣고 고치지 않는다.

- SNS·미디어로 넘긴 상태(`sns_handoff`)는 같은 파일의 별도 표 `brief_sns_handoffs`에 둔다
  (기존 표·기존 파일 그대로 호환: `CREATE TABLE IF NOT EXISTS`만 추가).
  받은 기획안 본문은 넘겨도 바뀌지 않는다.
  넘기는 일은 `brief_handoff.py`가 한다.

거절 이유 코드(`reason_code`, 닫힌 집합):
- `unsupported_schema_version`: 마케팅이 읽을 수 없는 `schema_version`
- `invalid_proposal_id`: `proposal_id`가 비었거나 영문·숫자·`_`·`-`(64자 이하)가 아님
- `empty_title` / `empty_idea` / `empty_why`: 공백뿐이거나 빈 문자열
숫자(`facts`·`target`)는 거절 근거로 쓰지 않는다.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from datetime import datetime
from typing import Literal

from pydantic import Field, ValidationError

from ax_contracts.schemas.common import AxModel, UtcDatetime
from ax_contracts.schemas.planning import (
    PROPOSAL_SCHEMA_VERSION,
    CampaignBriefInput,
    CampaignBriefOutput,
)

DB_ENV = "MARKETING_BRIEFS_DB"
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


HandoffStatus = Literal["not_handed", "handed", "failed"]
DraftState = Literal["caption_only", "media_attached", "missing"]


class SnsHandoffRecord(AxModel):
    """SNS·미디어로 넘긴 기록(저장본). 초안·미디어 요청 초안 번호만 담는다."""

    proposal_id: str
    status: Literal["handed", "failed"]
    draft_id: str | None = None
    media_request: dict | None = None
    reason_code: str | None = None
    handed_at: UtcDatetime | None = None  # 처음 넘긴 시각. 실패 기록은 없다
    updated_at: UtcDatetime


class SnsHandoffState(AxModel):
    """화면·호출자가 보는 넘김 상태. 기록이 없으면 `not_handed`다(지어낸 값 없음)."""

    status: HandoffStatus = "not_handed"
    draft_id: str | None = None
    media_request: dict | None = None
    reason_code: str | None = None
    handed_at: UtcDatetime | None = None
    draft_state: DraftState | None = None  # 초안이 SNS 초안 저장소에 있는지·미디어가 붙었는지


class StoredBriefView(StoredBrief):
    """받은 기획안 + 넘김 상태. 목록·조회 출력에 쓴다(저장본 `StoredBrief`는 바뀌지 않는다)."""

    sns_handoff: SnsHandoffState = Field(default_factory=SnsHandoffState)


class ListCampaignBriefsInput(AxModel):
    limit: int = Field(default=LIST_LIMIT_DEFAULT, ge=1, le=LIST_LIMIT_MAX)


class CampaignBriefList(AxModel):
    items: list[StoredBriefView]
    total: int
    unknown_reason: str | None = None


class GetCampaignBriefInput(AxModel):
    proposal_id: str | None = Field(default=None, min_length=1)
    campaign_id: str | None = Field(default=None, min_length=1)


class CampaignBriefView(AxModel):
    found: bool
    item: StoredBriefView | None = None
    unknown_reason: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign_briefs (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL UNIQUE,
    campaign_id TEXT NOT NULL UNIQUE,
    received_at TEXT NOT NULL,
    payload     TEXT NOT NULL
);
"""
_HANDOFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS brief_sns_handoffs (
    proposal_id TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
);
"""
_log = logging.getLogger(__name__)


class BriefStorageError(Exception):
    """저장소를 읽거나 쓰지 못했다(디스크·권한·잠금). 받은 기획안은 저장되지 않았다."""


class BriefStore:
    """받은 기획안 저장소. `path`가 없으면 환경변수 `MARKETING_BRIEFS_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute(_SCHEMA)
                self._conn.execute(_HANDOFF_SCHEMA)
        except sqlite3.Error as error:
            raise BriefStorageError(
                f"기획안 저장소를 열 수 없다: {type(error).__name__}"
            ) from error

    def close(self) -> None:
        self._conn.close()

    def _rows(self, where: str = "", args: tuple[str, ...] = ()) -> list[StoredBrief]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT proposal_id, payload FROM campaign_briefs {where} ORDER BY seq",  # noqa: S608
                    args,
                ).fetchall()
        except sqlite3.Error as error:
            raise BriefStorageError(f"기획안을 읽을 수 없다: {type(error).__name__}") from error
        found: list[StoredBrief] = []
        for proposal_id, payload in rows:
            try:
                found.append(StoredBrief.model_validate_json(payload))
            except ValidationError:  # 계약이 바뀌어 읽을 수 없는 줄은 건너뛰고 번호만 남긴다
                _log.warning("받은 기획안 %s를 읽을 수 없어 건너뜀", proposal_id)
        return found

    def existing(self, proposal_id: str) -> StoredBrief | None:
        found = self._rows("WHERE proposal_id = ?", (proposal_id,))
        return found[0] if found else None

    def add(self, brief: CampaignBriefInput, at: datetime) -> CampaignBriefOutput:
        """관문이 `executed`를 돌려준 뒤에만 부른다. 이미 있으면 보관본을 그대로 두고 duplicate."""
        existing = self.existing(brief.proposal_id)
        if existing is not None:
            return CampaignBriefOutput(outcome="duplicate", campaign_id=existing.campaign_id)
        stored = StoredBrief(
            campaign_id=campaign_id_for(brief.proposal_id),
            proposal_id=brief.proposal_id,
            received_at=at,
            brief=brief,
        )
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO campaign_briefs (proposal_id, campaign_id, received_at, payload)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        stored.proposal_id,
                        stored.campaign_id,
                        stored.received_at.isoformat(),
                        stored.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError:  # 동시에 같은 기획안이 들어왔다. 먼저 들어간 것이 이긴다
            return CampaignBriefOutput(outcome="duplicate", campaign_id=stored.campaign_id)
        except sqlite3.Error as error:
            raise BriefStorageError(f"기획안을 저장할 수 없다: {type(error).__name__}") from error
        return CampaignBriefOutput(outcome="accepted", campaign_id=stored.campaign_id)

    def list(self, limit: int) -> tuple[list[StoredBrief], int]:
        """받은 시각이 늦은 순(같으면 나중에 받은 것 먼저)."""
        newest_first = sorted(reversed(self._rows()), key=lambda s: s.received_at, reverse=True)
        return newest_first[:limit], len(newest_first)

    def get(self, proposal_id: str | None, campaign_id: str | None) -> StoredBrief | None:
        if proposal_id is not None:
            found = self.existing(proposal_id)
            if found is not None and campaign_id not in (None, found.campaign_id):
                return None
            return found
        found_list = self._rows("WHERE campaign_id = ?", (campaign_id or "",))
        return found_list[0] if found_list else None

    # --- SNS·미디어 넘김 기록 -------------------------------------------------------

    def handoff(self, proposal_id: str) -> SnsHandoffRecord | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT payload FROM brief_sns_handoffs WHERE proposal_id = ?", (proposal_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise BriefStorageError(f"넘김 기록을 읽을 수 없다: {type(error).__name__}") from error
        if row is None:
            return None
        try:
            return SnsHandoffRecord.model_validate_json(row[0])
        except ValidationError:  # 읽을 수 없는 줄은 없는 것으로 본다(다시 넘기면 덮어쓴다)
            return None

    def save_handoff(self, record: SnsHandoffRecord) -> None:
        """기획안당 한 줄. 다시 저장하면 덮어쓴다(실패 기록을 성공으로 바꾸는 경우)."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO brief_sns_handoffs"
                    " (proposal_id, status, updated_at, payload) VALUES (?, ?, ?, ?)",
                    (
                        record.proposal_id,
                        record.status,
                        record.updated_at.isoformat(),
                        record.model_dump_json(),
                    ),
                )
        except sqlite3.Error as error:
            raise BriefStorageError(
                f"넘김 기록을 저장할 수 없다: {type(error).__name__}"
            ) from error
