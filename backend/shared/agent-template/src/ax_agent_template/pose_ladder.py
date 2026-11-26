"""콘티 포즈 실패 사다리 (11/26, 기획 storyboard_svg·마케팅 board_svg·storyboard 공용).

목표: 원인이 무엇이든 콘티 칸이 반드시 그려진다. 앞 단계가 성공하면 뒤 단계는 부르지 않는다
(성공 경로의 LLM 호출 수는 1번 그대로, 실패할 때만 늘어난다).

1. 포즈 호출 1번 (전 장면·전 칸)
2. 1단이 `LLMResponseError`(400 포함)·`LLMInputError`(strict 한도 초과)·형식 오류면 →
   장면(칸)별로 쪼개 동시에 부른다 (`asyncio.gather`, 작은 스키마).
   전체 시간 상한은 하나 (호출마다 남은 시간)
3. 모델 env(`PLANNING_SVG_LLM_MODEL`·`MARKETING_SVG_LLM_MODEL`)를 썼는데 400 `_model`이면 2단을
   기본 모델(env 없을 때와 같은 클라이언트)로
4. LLM 없이 늘 성공: 그래도 못 그린 장면은 장면 글 낱말로 코드가 포즈를 정한다(`rule_pose`)

실패 이유는 단계마다 로그 한 줄(예외 종류·HTTP 코드·400 갈래, 공급자 메시지 앞 120자).
요청·프롬프트는 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ax_agent_template.llm import LLMFormatError, LLMInputError, LLMResponseError
from ax_agent_template.pose_render import ArmLLM, HeadLLM, PoseLLM, posture_from_text

log = logging.getLogger("ax.pose_ladder")
PROVIDER_LOG_MAX = 120  # 로그에 남기는 공급자 메시지 길이 (마케팅 character_sheet와 같다)
RULE_POSE_DETAIL = "rule_pose_{n}"  # 계약 svg_detail (규칙으로 그린 장면 수)
# 2단으로 쪼갤 실패 (시간 초과는 남은 시간이 없어 쪼개지 않는다)
SPLIT_ERRORS: tuple[type[BaseException], ...] = (LLMResponseError, LLMInputError, LLMFormatError,
                                                  ValidationError, ValueError)  # fmt: skip

# ---------------------------------------------------------------- 실패 이유 (F #1946에서 옮김)

_CALL_FAILED = re.compile(r"호출 실패: ([A-Za-z][A-Za-z0-9_]*)")
# 400(잘못된 요청)의 공급자 메시지 갈래 (대소문자 무시, 위에서부터)
BAD_REQUEST_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("schema", ("schema", "json_schema", "response_format", "additionalproperties", "required")),
    ("length", ("max_tokens", "too long", "context", "length")),
    ("model", ("model",)),
)


def provider_message(exc: BaseException) -> str:
    """LLMResponseError의 공급자 메시지 (래퍼가 키·토큰 모양을 가린 앞 200자). 없으면 ""."""
    raw = getattr(exc, "provider_message", None)
    return " ".join(raw.split()) if isinstance(raw, str) else ""


def bad_request_kind(message: str) -> str:
    """400 공급자 메시지 → schema·length·model, 모르면 ""."""
    low = (message or "").lower()
    return next((kind for kind, words in BAD_REQUEST_KINDS if any(w in low for w in words)), "")


def failure_detail(exc: BaseException) -> str:
    """계약 svg_detail: 예외 종류 이름. LLMResponseError면 HTTP 코드("LLMResponseError_429"),
    400 갈래("LLMResponseError_400_schema"), httpx 예외 이름("LLMResponseError_ReadTimeout")을
    붙인다 ([A-Za-z0-9_], 40자)."""
    name = type(exc).__name__
    if isinstance(exc, LLMResponseError):
        status = getattr(exc, "status_code", None)
        m = _CALL_FAILED.search(str(exc))
        if isinstance(status, int):
            name = f"{name}_{status}"
            kind = bad_request_kind(provider_message(exc)) if status == 400 else ""
            if kind:
                name = f"{name}_{kind}"
        elif m:
            name = f"{name}_{m.group(1)}"
    return name[:40]


def is_model_error(exc: BaseException) -> bool:
    return failure_detail(exc).endswith("_400_model")


def log_failure(where: str, step: str, exc: BaseException, elapsed: float, n: int) -> None:
    """실패 한 줄: "<where> 포즈 실패(<step>): <detail> <초>s 0/<n> — 공급자 메시지: …"."""
    said = provider_message(exc)[:PROVIDER_LOG_MAX]
    log.warning("%s 포즈 실패(%s): %s %.1fs 0/%d%s", where, step, failure_detail(exc), elapsed, n,
                f" — 공급자 메시지: {said}" if said else "")  # fmt: skip


# ---------------------------------------------------------------- 사다리 (1~3단)


@dataclass
class LadderResult[T]:
    """got: 번호 → 답 (LLM이 준 것만). calls: LLM 호출 수. steps: 지난 단계 이름."""

    got: dict[int, T] = field(default_factory=dict)
    calls: int = 0
    steps: list[str] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)


async def ladder[T](
    ids: Sequence[int],
    whole: Callable[[Any], Awaitable[dict[int, T]]],
    one: Callable[[Any, int], Awaitable[T | None]],
    llm: Any,
    *,
    timeout: float,
    where: str = "콘티",
    base_llm: Callable[[], Any] | None = None,
) -> LadderResult[T]:
    """1~3단. whole(llm) → {번호: 답}, one(llm, 번호) → 답(없으면 None). base_llm: 3단 기본 모델
    클라이언트를 만드는 함수 (모델 env를 안 썼으면 None, 3단에 갈 때만 만든다). 전체 시간 상한은
    timeout 하나."""
    out: LadderResult[T] = LadderResult()
    if llm is None or not ids:
        return out
    started = time.monotonic()

    def left() -> float:
        return max(timeout - (time.monotonic() - started), 0.0)

    out.steps.append("whole")
    out.calls += 1
    try:
        out.got = dict(await asyncio.wait_for(whole(llm), left()))
        return out
    except Exception as exc:  # noqa: BLE001 - 다음 단계로
        out.errors.append(exc)
        log_failure(where, "1단 전체", exc, time.monotonic() - started, len(ids))
        if not isinstance(exc, SPLIT_ERRORS) or left() <= 0:
            return out
    model_error = is_model_error(out.errors[-1])
    await _split(out, ids, one, llm, left, where, started, "2단 장면별")
    model_error = model_error or any(is_model_error(e) for e in out.errors[1:])
    rest = [i for i in ids if i not in out.got]
    base = base_llm() if rest and base_llm is not None and model_error and left() > 0 else None
    if base is not None:
        await _split(out, rest, one, base, left, where, started, "3단 기본 모델")
    return out


async def _split(out: LadderResult[Any], ids: Sequence[int],
                 one: Callable[[Any, int], Awaitable[Any]], llm: Any, left: Callable[[], float],
                 where: str, started: float, step: str) -> None:  # fmt: skip
    out.steps.append(step)
    out.calls += len(ids)

    async def call(i: int) -> Any:
        return await asyncio.wait_for(one(llm, i), left())

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(call(i) for i in ids), return_exceptions=True), left()
        )
    except TimeoutError as exc:
        out.errors.append(exc)
        log_failure(where, step, exc, time.monotonic() - started, len(ids))
        return
    for i, r in zip(ids, results, strict=True):
        if isinstance(r, BaseException):
            out.errors.append(r)
        elif r is not None:
            out.got[i] = r
    failed = [r for r in results if isinstance(r, BaseException)]
    if failed:
        log_failure(where, f"{step} {len(failed)}/{len(ids)}", failed[0],
                    time.monotonic() - started, len(ids))  # fmt: skip


# ---------------------------------------------------------------- 4단 규칙 포즈 (LLM 없음)


def _has(text: str, words: Sequence[str]) -> bool:
    low = text.lower()
    return any(w in low for w in words)


def _arm(raise_: float, bend: float = 0.0) -> ArmLLM:
    return ArmLLM.model_validate({"raise": raise_, "bend": bend})


def rule_pose(text: str, label: str = "") -> PoseLLM:
    """장면 글 낱말로 정한 포즈 (LLM 없이 늘 성공). 모르면 서 있는 기본 포즈.

    범위는 계약 KeyPose 안이고, 그림은 `pose_render.key_pose`(라벨 시각·소품 낱말)로 똑같이 그린다.
    label: 칸 위 한 줄 (비면 장면 글 앞 10자)."""
    t = " ".join((text or "").split())
    low = t.lower()
    shot = "medium"
    if _has(low, ("close-up", "close up", "closeup", "클로즈업", "얼굴 가까이")):
        shot = "close"
    elif _has(low, ("wide", "full shot", "long shot", "전신", "와이드", "멀리서")):
        shot = "full"
    yaw, step, prop = 0.0, 0.0, None
    head = HeadLLM()
    arm_l, arm_r = _arm(0), _arm(0)
    if _has(low, ("돌아보", "돌아본", "turn", "뒤돌아")):
        yaw = 90.0
    if _has(low, ("놀라", "놀란", "깜짝", "surprised", "shock")):
        arm_l, arm_r = _arm(100, 60), _arm(100, 60)
        head = HeadLLM(yaw=0, pitch=-20)
    if _has(low, ("만세", "양팔", "두 팔", "arms up", "both arms", "cheer")):
        arm_l, arm_r = _arm(160, 10), _arm(160, 10)
    elif _has(low, ("손을 흔들", "손 흔들", "흔들며", "흔들고", "wave", "waving")):
        arm_r = _arm(150, 40)
    elif _has(low, ("가리키", "가리킨", "point")):
        arm_r = _arm(90, 0)
    if _has(low, ("마이크", "microphone", "mic")):
        arm_r = _arm(70, 120)
    elif _has(low, ("휴대폰", "스마트폰", "핸드폰", "phone")):
        arm_r, prop = _arm(60, 110), "phone"
    elif _has(low, ("컵", "커피", "cup", "coffee")):
        arm_r, prop = _arm(40, 90), "cup"
    # 11/26 몸 자세: LLM 지시와 같은 낱말 표 (앉·소파·의자 → sit, 무릎 꿇 → kneel, 눕 → lie)
    posture = posture_from_text(low)
    if posture != "stand":
        step = 0.0
        if posture == "sit":
            head = HeadLLM(yaw=head.yaw, pitch=max(head.pitch, 10))
    elif _has(low, ("걷", "뛰", "달리", "walk", "run")):
        step = 0.4
    return PoseLLM(shot=shot, yaw=yaw, head=head, arm_l=arm_l, arm_r=arm_r, step=step,
                   posture=posture, prop=prop, label_ko=(label or t)[:10])  # fmt: skip


def rule_detail(n: int) -> str:
    """규칙 포즈로 그린 장면 수 → 계약 svg_detail ("rule_pose_3"). 0이면 ""."""
    return RULE_POSE_DETAIL.format(n=n) if n else ""
