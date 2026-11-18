"""받은 기획안을 사람 버튼 없이 SNS 초안 + 미디어 요청 초안까지 자동으로 넘긴다.

끊겼던 곳: `receive_campaign_brief`는 기획안을 저장만 하고(`briefs.py`), 넘기기
(`handoff_brief_to_sns`)는 화면 버튼이 부를 때만 일했다. 이 모듈의 루프가 받은 뒤 아직 안 넘긴
기획안(넘김 기록 없음, 또는 `failed`)을 주기마다 찾아 기존 `handoff_brief_to_sns`를
`media_kind="auto"`로 부른다. 새 로직은 없다: 멱등(`already_handed`)·가격표 fail-closed·`failed`
기록은 모두 기존 핸들러의 것이다.

자동으로 가는 범위 = 초안까지: SNS 글 초안 + 미디어 요청 초안(종류·필드 추천)을 보관한다.
돈이 나가지 않고 외부로 나가지 않는다. 자동으로 하지 않는 일(사람이 한다):
- 미디어 생성(`prepare_brief_media` → `generate_media`, B등급): 안전 관문·건당/월 상한·단가표
  fail-closed 그대로. 이 모듈은 부르지 않는다.
- 게시(`publish_sns`, C등급): 사람 승인 그대로. 이 모듈은 부르지 않는다.

실패: 핸들러가 `failed`(이유 코드)로 기록한 건은 `RETRY_S` 뒤 다시 하고, 실패 `MAX_ATTEMPTS`번이면
포기한다(자동으로는 더 안 함, 기록은 남는다. 화면 버튼으로는 계속 넘길 수 있다). 시도 횟수는
`brief_auto_handoff` 표(기획안 DB 안)에 남아 재시작해도 한도가 이어진다.

환경변수(`MARKETING_BRIEF_AUTO_HANDOFF`, **기본 꺼짐**; 켜기는 `1`·`true`·`on`):
- `MARKETING_BRIEF_AUTO_HANDOFF_POLL_S`(기본 60초, 0이면 루프를 켜지 않음)
- `MARKETING_BRIEF_AUTO_HANDOFF_RETRY_S`(기본 300초), `..._MAX_ATTEMPTS`(기본 3)
환경변수는 매 주기 다시 읽어 재시작 없이 끄고 켠다. 켠 직후에는 쌓인 미넘김분이 한꺼번에(한
주기 `BATCH`건씩) 간다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from marketing_agent.briefs import BriefStore

_log = logging.getLogger(__name__)

ENABLE_ENV = "MARKETING_BRIEF_AUTO_HANDOFF"
POLL_ENV = "MARKETING_BRIEF_AUTO_HANDOFF_POLL_S"
RETRY_ENV = "MARKETING_BRIEF_AUTO_HANDOFF_RETRY_S"
ATTEMPTS_ENV = "MARKETING_BRIEF_AUTO_HANDOFF_MAX_ATTEMPTS"
BATCH = 20
CALL_DEADLINE_S = 60
_OK = frozenset({"handed", "already_handed"})

HandoffFn = Callable[[AgentRequest], Awaitable[AgentResult]]


def _env_num(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class AutoHandoffConfig:
    enabled: bool
    poll_s: float
    retry_s: float
    max_attempts: int

    @classmethod
    def from_env(cls) -> AutoHandoffConfig:
        raw = os.getenv(ENABLE_ENV, "").strip().lower()
        return cls(
            enabled=raw in {"1", "true", "yes", "on"},
            poll_s=_env_num(POLL_ENV, 60),
            retry_s=_env_num(RETRY_ENV, 300),
            max_attempts=max(1, int(_env_num(ATTEMPTS_ENV, 3))),
        )


@dataclass
class Runtime:
    """테스트용 바꿔치기 지점(`campaign_auto.RT`와 같은 방식)."""

    sleep: Callable[[float], Awaitable[None]] = staticmethod(asyncio.sleep)


RT = Runtime()


def _request(proposal_id: str, now: datetime) -> AgentRequest:
    meta = RequestMeta(
        context_id=f"auto_brief_{proposal_id}",
        token="internal:marketing-auto",
        deadline=now + timedelta(seconds=CALL_DEADLINE_S),
    )
    return AgentRequest(
        meta=meta,
        skill="handoff_brief_to_sns",
        input={"proposal_id": proposal_id, "media_kind": "auto"},
    )


async def auto_handoff_once(
    briefs: BriefStore,
    handoff: HandoffFn,
    cfg: AutoHandoffConfig | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """한 주기. 꺼져 있으면 아무것도 하지 않는다. 결과 건수(handed·failed·skipped)를 돌려준다."""
    cfg = cfg or AutoHandoffConfig.from_env()
    counts = {"handed": 0, "failed": 0, "skipped": 0}
    if not cfg.enabled:
        return counts
    now = now or datetime.now(UTC)
    for pid in briefs.pending_handoff_ids(BATCH):
        attempts, last_at = briefs.auto_attempts(pid)
        if attempts >= cfg.max_attempts or (
            last_at is not None and now < last_at + timedelta(seconds=cfg.retry_s)
        ):
            counts["skipped"] += 1  # 한도에 닿아 포기했거나 재시도 간격이 안 지났다
            continue
        reason: str | None
        try:
            result = await handoff(_request(pid, now))
            output = result.output or {}
            outcome = str(output.get("outcome") or result.outcome or "")
            handoff_state = output.get("sns_handoff") or {}
            reason = handoff_state.get("reason_code") or outcome or "unknown"
            ok = result.status == TaskStatus.COMPLETED and outcome in _OK
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - 한 건이 막혀도 다음 건은 한다
            _log.exception("기획안 %s 자동 넘김 실패", pid)
            ok, reason = False, type(error).__name__
        if ok:
            counts["handed"] += 1
            continue
        counts["failed"] += 1
        total = briefs.record_auto_attempt(pid, now, reason)
        if total >= cfg.max_attempts:
            _log.warning(
                "기획안 %s 자동 넘김 %s번 실패(%s), 사람이 확인해야 함", pid, total, reason
            )
    return counts


async def auto_handoff_loop(get_briefs: Callable[[], BriefStore], handoff: HandoffFn) -> None:
    while True:
        cfg = AutoHandoffConfig.from_env()  # 매 주기 다시 읽어 재시작 없이 끄고 켠다
        try:
            await auto_handoff_once(get_briefs(), handoff, cfg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _log.exception("기획안 자동 넘김 주기 실패, 다음 주기에 다시 한다")
        await RT.sleep(cfg.poll_s or 60)
