"""자동 게시 스위치. 초안·미디어·자동 넘김은 그대로 두고 자동 게시(`publish_sns`)만 끈다.

계정이 정지돼도 마케팅 전체는 계속 돌고 자동 게시만 사람이 켜고 끈다(수동 게시는 후속 PR).
테이블·컬럼 이름은 `paused`지만 뜻은 "자동 게시 꺼짐"이다. 상태는 게시 대기 DB
(`MARKETING_SNS_PENDING_DB`)의 `sns_publish_control` 표(채널 이름이 키)에 있고
**호출마다** 읽으므로
재시작 없이 반영된다. 사람은 화면 토글(스킬 `set_publish_control`, 안전 관문을 지난다)로 바꾸고,
급할 때는 DB를 직접 고쳐도 된다.

- 행이 없으면 중지 아님(기존 동작 그대로). 환경변수 `MARKETING_SNS_PUBLISH_PAUSED`는 **행이 없을
  때의 초기 기본값**으로만 쓴다(기본 꺼짐). 행이 생기면 행이 우선한다.
- DB를 읽을 수 없으면 게시를 막는다(fail-closed, `publish_control_error`).
- `reason`은 사람이 적는 사유(200자까지)다. 비밀값을 적지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from marketing_agent.sns_publish.pending import PendingStorageError

if TYPE_CHECKING:
    from marketing_agent.sns_publish.pending import PendingStore

DEFAULT_ENV = "MARKETING_SNS_PUBLISH_PAUSED"
DEFAULT_CHANNEL = "instagram"
MAX_REASON = 200
ENV_DEFAULT_REASON = "초기 기본값(MARKETING_SNS_PUBLISH_PAUSED)"
_TRUE = frozenset({"1", "true", "yes", "on"})


class PublishControlError(RuntimeError):
    """중지 설정을 읽을 수 없다. 게시를 막는다(fail-closed)."""


@dataclass(frozen=True)
class ChannelControl:
    paused: bool = False
    reason: str = ""
    updated_by: str | None = None  # None이면 DB 행이 없다(기본값)
    updated_at: str | None = None


def clean_reason(reason: str) -> str:
    return "".join(ch for ch in reason if ch.isprintable()).strip()[:MAX_REASON]


def read_control(store: PendingStore, channel: str = DEFAULT_CHANNEL) -> ChannelControl:
    """`channel`의 중지 상태. 매 호출마다 DB를 읽는다."""
    try:
        row = store.control_get(channel)
    except PendingStorageError:
        raise PublishControlError("publish_control_unreadable") from None
    if row is None:
        if (os.getenv(DEFAULT_ENV) or "").strip().lower() in _TRUE:
            return ChannelControl(paused=True, reason=ENV_DEFAULT_REASON)
        return ChannelControl()
    paused, reason, updated_by, updated_at = row
    return ChannelControl(paused, clean_reason(reason), updated_by, updated_at)
