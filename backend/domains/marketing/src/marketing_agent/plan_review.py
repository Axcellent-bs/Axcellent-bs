"""마케팅 제작안 확인 → 기획에 A2A 고치기 요청 (11/26 마케팅↔기획 재협업).

기획 자체 검사(인물 수·동작 수·소품 수)는 기획 안의 검사다. 영상 모델의 한계와 가진 참조 이미지는
마케팅이 안다. 그래서 영상 초안 준비(`prepare_video_draft_from_plan`)가 제작안을 받으면 스토리보드·
대본을 만들기 전에 마케팅 기준만 코드로 확인한다(LLM 아님, 기획 검사와 겹치지 않는다):

- duration: 장면 길이(end_s − start_s, 배속 전)가 영상 모델 최소(`MARKETING_PLAN_MIN_SCENE_S`, 기본
  2초) 미만. 엔드카드 장면과 첫 훅 장면(0초 시작, 2초 이하)은 보지 않는다
- refs: 회사 캐릭터를 골랐을 때, 회사 캐릭터 말고 다른 사람이 클로즈업·미디엄으로 나온다(참조는 회사
  캐릭터 한 사람뿐이다. 원본과 추가 참조는 같은 사람). 배경 군중·관객은 괜찮다(장면 글 낱말로 본다)
- text: 장면 prompt에 글자·간판·화면 속 문구를 그리라는 말(금칙어 표 `text_request_words` 그대로)

걸리면 기획 `revise_video_plan`을 `call_agent`로 1회 부르고(제한 시간
`MARKETING_PLAN_REVISE_TIMEOUT_S`, 기본 180초), 받은 2판을 다시 확인한다. ✓면 2판으로 준비를
계속하고, ✕면 더 부르지 않고 2판으로 계속하되 "사람 확인 필요"를 남긴다. 시간 초과·실패면
1판으로 계속하고 ✕를 남긴다. 문제 없는 제작안은 호출 0.
총괄 의뢰 기록(넘겨받은 값)은 고치지 않는다. 기록은 의뢰 제작 기록 render.plan_review에 남긴다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template import config as template_config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.planning import REVISE_VIDEO_PLAN_SKILL
from marketing_agent import pose_board
from marketing_agent.media import prompts

logger = logging.getLogger("marketing.plan_review")

PLANNING = "planning"
REVIEW_KEY = "plan_review"  # 의뢰 제작 기록 render 칸
# 11/26 L: 기본 3초 → 2초. 3초면 재미 먼저 흐름의 0~2초 훅이 늘 걸려 기획이 장면을 고르게 다시 나눠
# "첫 2초 훅" 설계가 사라졌다
MIN_SCENE_ENV, MIN_SCENE_S = "MARKETING_PLAN_MIN_SCENE_S", 2.0
# 훅 장면: 첫 장면이 0초에 시작하고 이 길이 이하 (기획 재미 먼저·광고 fun_timings의 첫 구간 0~2초).
# 엔드카드처럼 길이 검사에서 뺀다 (도메인끼리 import 안 해 값을 옮김)
HOOK_MAX_S = 2.0
TIMEOUT_ENV, TIMEOUT_S = "MARKETING_PLAN_REVISE_TIMEOUT_S", 180.0
MAX_VERSION = 2  # 의뢰당 고치기 1회 (1판 → 2판)
# 회사 캐릭터 말고 다른 사람을 뜻하는 낱말 / 배경 군중이라 괜찮은 낱말 / 넓은 샷 낱말
OTHER_PERSON_WORDS = (
    "친구", "동료", "다른 사람", "다른 인물", "손님", "점원", "직원", "상대", "파트너", "엄마",
    "아빠", "아이가", "두 사람", "둘이", "셋이", "세 사람", "a friend", "friends", "another person",
    "two people", "a man", "a boy", "a girl", "partner", "coworker", "customer",
)  # fmt: skip
CROWD_WORDS = ("군중", "관객", "행인", "배경", "멀리", "crowd", "audience", "background")
WIDE_WORDS = ("wide", "full shot", "long shot", "establishing", "와이드", "전신", "멀리서")

Caller = Callable[[str, AgentRequest], Awaitable[AgentResult]]


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _has(text: str, words: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(w in low for w in words)


def is_hook(i: int, scene: dict[str, Any]) -> bool:
    """첫 장면이 0초에 시작하는 짧은 훅(0~2초)인가. 훅은 짧은 것이 설계라 길이 검사에서 뺀다."""
    start, end = _num(scene.get("start_s")), _num(scene.get("end_s"))
    return i == 1 and start == 0 and end is not None and 0 < end <= HOOK_MAX_S


def check(plan: dict[str, Any], has_character: bool) -> list[dict[str, Any]]:
    """마케팅 확인 문제 {kind, scene, detail_ko} (계약 CheckProblem 모양). 문제 없으면 []."""
    min_s = _env_float(MIN_SCENE_ENV, MIN_SCENE_S)
    out: list[dict[str, Any]] = []
    for i, s in enumerate(plan.get("scenes") or [], start=1):
        if not isinstance(s, dict):
            continue
        n = s.get("n") if isinstance(s.get("n"), int) else i
        visual = str(s.get("visual") or "")
        if pose_board.is_end_card(visual):
            continue
        start, end = _num(s.get("start_s")), _num(s.get("end_s"))
        if start is not None and end is not None and end - start < min_s and not is_hook(i, s):
            sec = f"{end - start:g}"
            detail = f"장면 길이 {sec}초 (영상 모델 최소 {min_s:g}초)"
            out.append({"kind": "duration", "scene": n, "detail_ko": detail})
        body = " ".join(str(s.get(k) or "") for k in ("visual", "start_frame", "end_frame"))
        camera = f"{s.get('camera_en') or ''} {visual}"
        if (has_character and _has(body, OTHER_PERSON_WORDS) and not _has(body, CROWD_WORDS)
                and not _has(camera, WIDE_WORDS)):  # fmt: skip
            out.append({"kind": "refs", "scene": n,
                        "detail_ko": "회사 캐릭터 말고 다른 인물이 가까이 나와요 (참조 이미지는 "
                                     "회사 캐릭터 1명뿐)"})  # fmt: skip
        if prompts.asks_for_text(str(s.get("prompt") or "")):
            detail = "장면 프롬프트가 화면 속 글자·간판을 그리라고 해요"
            out.append({"kind": "text", "scene": n, "detail_ko": detail})
    return out[:12]


def summary_ko(problems: list[dict[str, Any]]) -> str:
    """대화방·카드 요약 "장면 2 장면 길이 2초 외 1건"."""
    if not problems:
        return ""
    head = problems[0]
    where = f"장면 {head['scene']} " if head.get("scene") else ""
    more = f" 외 {len(problems) - 1}건" if len(problems) > 1 else ""
    return f"{where}{head['detail_ko']}{more}"


async def review(
    request: AgentRequest, caller: Caller | None = None
) -> tuple[AgentRequest, dict[str, Any] | None]:
    """(준비에 쓸 요청, 확인 기록). 문제 없으면 (그대로, None) — 기획을 부르지 않는다."""
    data = request.input or {}
    plan = data.get("video_plan")
    pid = data.get("proposal_id")
    if not isinstance(plan, dict) or not isinstance(pid, str):
        return request, None
    has_character = bool(data.get("brand_character_record_id"))
    version = plan.get("version") if isinstance(plan.get("version"), int) else 1
    problems = check(plan, has_character)
    if not problems:
        return request, None
    log: dict[str, Any] = {
        "versions": [{"version": version, "passed": False, "problems": problems}],
        "requested": False, "outcome": "", "needs_human": True,
        "summary_ko": summary_ko(problems), "at": datetime.now(UTC).isoformat(),
    }  # fmt: skip
    if version >= MAX_VERSION:  # 이미 고친 판이면 더 부르지 않는다 (의뢰당 1회)
        log["outcome"] = "revise_limit"
        return request, log
    timeout = _env_float(TIMEOUT_ENV, TIMEOUT_S)
    meta = request.meta.model_copy(update={
        "deadline": datetime.now(UTC) + timedelta(seconds=timeout),
        "idempotency_key": f"{request.meta.context_id}:{REVISE_VIDEO_PLAN_SKILL}:{pid}:v{version}",
    })  # fmt: skip
    sent = AgentRequest(meta=meta, skill=REVISE_VIDEO_PLAN_SKILL, input={
        "proposal_id": pid, "from_version": version, "problems": problems,
        "requested_by": "marketing",
    })  # fmt: skip
    if caller is None and template_config.is_mock("marketing"):  # 목 모드(시험)는 실제 A2A 안 함
        log["outcome"] = "planning_not_connected"
        return request, log
    log["requested"] = True
    try:
        answer = await asyncio.wait_for((caller or call_agent)(PLANNING, sent), timeout)
    except TimeoutError:
        log["outcome"] = "planning_timeout"
        logger.warning("제작안 고치기 요청 시간 초과: %s v%d", pid, version)
        return request, log
    except Exception as exc:  # noqa: BLE001 - 고치기 실패는 1판으로 계속한다
        log["outcome"] = "planning_error"
        logger.warning("제작안 고치기 요청 실패: %s (%s)", pid, type(exc).__name__)
        return request, log
    new = (answer.output or {}).get("video_plan")
    if answer.status != TaskStatus.COMPLETED or not isinstance(new, dict):
        log["outcome"] = f"planning_{answer.outcome or answer.status.value}"[:60]
        logger.info("제작안 고치기 못 받음 → 1판으로: %s %s", pid, log["outcome"])
        return request, log
    again = check(new, has_character)
    new_version = new.get("version") if isinstance(new.get("version"), int) else version + 1
    log["versions"].append({"version": new_version, "passed": not again, "problems": again})
    log["outcome"] = "revised" if answer.outcome != "already" else "already"
    log["needs_human"] = bool(again)
    logger.info("제작안 %s %d판 받음: 마케팅 확인 %s", pid, new_version, "✕" if again else "✓")
    return request.model_copy(update={"input": {**data, "video_plan": new}}), log
