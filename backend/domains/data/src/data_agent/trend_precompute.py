"""트렌드 감지 탭 미리 계산: 수집기가 새 스냅샷을 저장한 직후 부른다.

급상승 판정(trend_scan·trend_candidates)·지금 인기(trend_popular)·빨리 크는 영상(youtube_velocity)·
숏츠 통계(shorts_stats)를 화면과 같은 입력({})으로 계산해 `response_cache`에 넣는다(trend_scan은
판정판·네이버·설명 문장 캐시만 채운다). 화면 요청은 같은 키로 읽기만 한다.

- 화면과 같은 핸들러를 그대로 부른다(계산 규칙이 하나). 서버와 같은 환경변수여야 같은 키가 된다
  (run_all.sh가 수집기를 서버와 같은 환경으로 켠다. 다르면 키가 달라 서버가 첫 요청에 계산한다)
- 두 번 돈다: 한 skill이 새로 분류해 저장분이 바뀌면 앞 skill의 키도 바뀌므로,
  두 번째는 바뀐 키로 다시 넣는다(이미 분류한 것은 다시 묻지 않아 빠르다).
  같으면 저장분을 읽기만 한다
- 판정판은 서버가 쓰는 파일을 다시 읽어 서버와 같은 내용으로 계산한다
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from data_agent.stage_timing import recording

# 판정(trend_scan·trend_candidates)을 먼저: 판정판에 오늘 판정을 남겨야
# 지금 인기의 배지·키가 화면과 같다
# 브리프는 마지막: 하루 캐시라 그날 처음 한 번만 만들고(26~38초, LLM·쇼핑인사이트),
# 그 뒤엔 저장분을 읽는다
SKILLS = (
    "trend_scan", "trend_candidates", "trend_popular", "youtube_velocity", "shorts_stats",
    "trend_insight_brief",
)  # fmt: skip
CONTEXT_ID = "ctx_trend_precompute"
PASSES = 2


def _request(skill: str) -> AgentRequest:
    meta = RequestMeta(context_id=CONTEXT_ID, token="collector",
                       deadline=datetime.now(UTC) + timedelta(minutes=10))  # fmt: skip
    return AgentRequest(meta=meta, skill=skill, input={})


async def precompute(emit: Callable[[str], None] | None = None) -> list[str]:
    """세 skill을 계산해 저장한다. 실패해도 수집은 그대로 두고 이유만 돌려준다."""
    from data_agent import handlers
    from data_agent.today_board import BOARD

    say = emit or (lambda line: print(line, flush=True))
    problems: list[str] = []
    BOARD.reload()
    run = {
        "trend_scan": handlers.trend_scan,
        "trend_candidates": handlers.trend_candidates,
        "trend_popular": handlers.trend_popular,
        "youtube_velocity": handlers.youtube_velocity,
        "shorts_stats": handlers.shorts_stats,
        "trend_insight_brief": handlers.trend_insight_brief,
    }
    for n in range(1, PASSES + 1):
        for skill in SKILLS:
            try:
                with recording(f"{skill}@precompute{n}", say):
                    result = await run[skill](_request(skill))
                if n == PASSES and result.status != TaskStatus.COMPLETED:
                    problems.append(f"{skill}: {result.outcome or result.status}")
            except Exception as error:  # noqa: BLE001 - 미리 계산 실패가 수집을 막지 않게
                if n == PASSES:
                    problems.append(f"{skill}: {type(error).__name__}")
                print(f"[precompute] {skill} 실패 ({type(error).__name__})", file=sys.stderr)
    return problems
