"""홍보 영상 의뢰(대화방)의 대본 (2026-11-25).

총괄 홍보 영상 흐름에서 영상 초안을 준비할 때(`prepare_video_draft_from_plan`) 의뢰용 제작 기록에
대본을 써 붙이고, 스토리보드 칸에 대본의 대사·말하는 사람·자막·시간을 붙인다. 그래서 대화방
의뢰 하나로 만든 영상에도 대사가 들어가고, 제작 기록에서 바로 대본을 보고 고칠 수 있다.

- 기획 제작안에는 대사 칸이 없고 기획 LLM은 의뢰문을 받지 않는다. 의뢰문(`request_text`)은
  총괄이 모든 단계에 데이터 칸(untrusted)으로 넘기므로 여기서 대본 작가에게 데이터로만 준다.
- 대본: 기획 장면(화면) + 의뢰문을 줄거리로 `creative_script.write_script`(LLM). LLM이 없거나
  실패하면 기획 장면으로 만든 대본(대사 없음)을 쓴다(의뢰문 문장을 대사로 옮기지 않는다).
- 코드 보장: 의뢰문에 `첫 대사는 "…"`처럼 따옴표로 적힌 첫 대사가 있으면 1장면 대사로,
  "여자 목소리"·"남자 목소리"가 있으면 대사 있는 장면의 말하는 사람으로 둔다.
- 장면 수 = 스토리보드 칸 수(기획 장면, 최대 8). 이미 대본이 있는 기록(사람이 고쳤거나 다시
  준비)은 건드리지 않는다. 실패해도 영상 준비를 막지 않는다(호출부가 예외를 삼킨다).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from ax_contracts.schemas.marketing_script import (
    MAX_SCRIPT_CAPTION,
    MAX_SCRIPT_LINE,
    MAX_SCRIPT_SCENES,
    MAX_SCRIPT_SPEAKER,
    MAX_SCRIPT_VISUAL,
    CreativeScript,
    ScriptScene,
)
from marketing_agent import creative_records as _creative
from marketing_agent import creative_script as _script
from marketing_agent import storyboard as _storyboard

logger = logging.getLogger("marketing.request_script")

MAX_PLAN_SCENES = 8  # 스토리보드 칸(request_creative.MAX_SCENES)과 같다
MAX_REQUEST = 600  # 줄거리에 붙이는 의뢰문 길이
_OPEN = "\"'“‘「『"
_CLOSE = "\"'”’」』"
FIRST_LINE = re.compile(
    rf"첫\s*(?:대사|마디|말)[^{_OPEN}\n]{{0,24}}[{_OPEN}]([^{_CLOSE}\n]{{1,{MAX_SCRIPT_LINE}}})[{_CLOSE}]"
)
VOICE = re.compile(r"(여자|여성|남자|남성)\s*(?:의\s*)?목소리")
_VOICE_NAME = {"여자": "여자", "여성": "여자", "남자": "남자", "남성": "남자"}
NARRATION = ("내레이션", "나레이션")


def _cut(text: Any, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


def first_line(request_text: str | None) -> str | None:
    """의뢰문에 따옴표로 적힌 첫 대사. 없으면 None."""
    m = FIRST_LINE.search(request_text or "")
    return (_cut(m.group(1), MAX_SCRIPT_LINE) or None) if m else None


def voice_speaker(request_text: str | None) -> str:
    """의뢰문의 목소리(여자·남자). 없으면 빈 문자열."""
    m = VOICE.search(request_text or "")
    return _VOICE_NAME[m.group(1)] if m else ""


def _plan_scenes(plan: Any) -> list[Any]:
    """스토리보드 칸과 같은 장면(영상 프롬프트가 있는 장면, 최대 8)."""
    scenes = getattr(plan, "scenes", None) or []
    return [s for s in scenes if _cut(getattr(s, "prompt", ""), 10)][:MAX_PLAN_SCENES]


def _visual(s: Any) -> str:
    return _cut(getattr(s, "visual", None) or getattr(s, "prompt", ""), MAX_SCRIPT_VISUAL)


def _sec(s: Any, key: str) -> float | None:
    """장면의 시각(초). 계약 모델 속성·느슨한 dict·추가 칸(model_extra) 어느 쪽이든."""
    if isinstance(s, dict):
        v = s.get(key)
    else:
        v = getattr(s, key, None)
        if v is None:
            v = (getattr(s, "model_extra", None) or {}).get(key)
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v)


def plan_times(plan: Any) -> list[tuple[float, float]] | None:
    """기획 장면 시간 (11/27 운영 PROP-44: 기획 0~2/2~7/7~12/12~15초인데 대본·칸은 고르게 나눈
    0~4/4~7.5…였다). 모든 장면에 start_s·end_s가 있고 0초부터 앞뒤로 맞으면 그 값, 아니면 None
    (그때는 지금처럼 고르게 나눈다)."""
    out: list[tuple[float, float]] = []
    at = 0.0
    for s in _plan_scenes(plan)[:MAX_SCRIPT_SCENES]:
        a, b = _sec(s, "start_s"), _sec(s, "end_s")
        if a is None or b is None or b <= a or abs(a - at) > 1e-6 or b > 600:
            return None
        out.append((a, b))
        at = b
    return out or None


def _times_fixed(script: CreativeScript, times: list[tuple[float, float]],
                 base: CreativeScript) -> CreativeScript:  # fmt: skip
    """대본 장면 수·시간을 기획 장면에 맞춘다: 많으면 자르고, 모자라면 기획 장면(빈 대사)으로
    채우고, 시간은 기획 값 그대로 (LLM에게 맡기지 않는다)."""
    rows = list(script.scenes[: len(times)])
    rows += [base.scenes[i] for i in range(len(rows), len(times))]
    fixed = [s.model_copy(update={"no": i, "start_s": a, "end_s": b})
             for i, (s, (a, b)) in enumerate(zip(rows, times, strict=True), start=1)]  # fmt: skip
    return script.model_copy(update={"scenes": fixed, "total_seconds": base.total_seconds})


def plan_script(plan: Any) -> CreativeScript | None:
    """기획 장면으로 만든 대본(화면·자막·시간, 대사 없음). 장면이 없으면 None.

    11/27: 기획 장면 시간이 있으면 그 시간, 없으면 지금처럼 고르게 나눈다."""
    scenes = _plan_scenes(plan)[:MAX_SCRIPT_SCENES]
    if not scenes:
        return None
    seconds = max(1, int(getattr(plan, "duration_s", 0) or 15))
    fixed = plan_times(plan)
    if fixed:
        seconds = max(seconds, int(-(-fixed[-1][1] // 1)))  # 끝 장면이 넘으면 올림
    times = fixed or _script.split_times([1.0] * len(scenes), seconds)
    rows = [
        ScriptScene(
            no=i,
            start_s=start,
            end_s=end,
            visual_ko=_visual(s) or f"장면 {i}",
            caption_ko=_cut(getattr(s, "caption", ""), MAX_SCRIPT_CAPTION),
        )
        for i, (s, (start, end)) in enumerate(zip(scenes, times, strict=True), start=1)
    ]
    title = _cut(getattr(plan, "concept", ""), 80) or "홍보 영상"
    return CreativeScript(
        title_ko=title, total_seconds=seconds, scenes=rows, source="rule", at=_script._now()
    )


REAL_PERSON_NOTE = (
    "인물은 실사 사람(AI 가상 인물)이라 화면에 일러스트·만화·캐릭터 그림 표현을 쓰지 않는다."
)


def request_story(plan: Any, request_text: str | None, real_person: bool = False) -> str:
    """대본 작가에게 줄 줄거리: 의뢰문(화면 연출·대사·목소리는 이것을 먼저) + 기획 장면(참고).

    11/25 기획 장면을 앞에 두니 작가가 기획 화면을 그대로 옮겨 의뢰 연출(첫 장면 클로즈업 등)이
    빠졌다. 의뢰문은 데이터로만 간다."""
    lines = [f"{i}. {_visual(s)}" for i, s in enumerate(_plan_scenes(plan), start=1)]
    parts = []
    asked = _cut(request_text, MAX_REQUEST)
    if asked:
        parts.append(f"의뢰자 요청(장면 화면 연출·대사·목소리는 이것을 먼저 따른다): {asked}")
    if real_person:
        parts.append(REAL_PERSON_NOTE)
    parts.append(f"기획 장면(참고, 장면 수와 순서는 이대로): {' '.join(lines)}")
    return _cut(" / ".join(parts), _script.MAX_STORY)


def apply_request(script: CreativeScript, request_text: str | None) -> CreativeScript:
    """의뢰문의 첫 대사·목소리를 코드로 지킨다."""
    line = first_line(request_text)
    voice = voice_speaker(request_text)
    rows = []
    for s in script.scenes:
        update: dict[str, Any] = {}
        if s.no == 1 and line:
            update["line_ko"] = line
        said = update.get("line_ko") or s.line_ko
        if voice and said:
            # 11/25 "직장인"처럼 역할만 적히면 목소리가 빠진다: "여자 직장인"으로 붙인다
            who = s.speaker_ko
            if not who or who in NARRATION:
                update["speaker_ko"] = _cut(voice, MAX_SCRIPT_SPEAKER)
            elif voice not in who:
                update["speaker_ko"] = _cut(f"{voice} {who}", MAX_SCRIPT_SPEAKER)
        rows.append(s.model_copy(update=update) if update else s)
    return script.model_copy(update={"scenes": rows})


def _bare(text: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "")).lower()


def plan_quotes(plan: Any) -> list[str]:
    """기획 제작안 의뢰 지시(request_rules, 11/26) 중 대사 문장(quote). 예전 제작안은 []."""
    out = []
    for r in getattr(plan, "request_rules", None) or []:
        kind = r.get("kind") if isinstance(r, dict) else getattr(r, "kind", None)
        text = r.get("text_ko") if isinstance(r, dict) else getattr(r, "text_ko", None)
        if kind == "quote" and _cut(text, MAX_SCRIPT_LINE):
            out.append(_cut(text, MAX_SCRIPT_LINE))
    return out


def keep_quotes(script: CreativeScript, quotes: list[str]) -> CreativeScript:
    """의뢰 대사 문장을 대본 대사에 글자 그대로 넣는다(11/26, 작가가 빠뜨리면 코드가 넣는다).

    이미 어떤 대사에 있으면 그대로. 없으면 그 문장이 자막인 장면, 없으면 마지막 장면의 대사로."""
    rows = list(script.scenes)
    for q in quotes:
        if not rows or any(_bare(q) in _bare(s.line_ko) for s in rows):
            continue
        at = next((i for i, s in enumerate(rows) if _bare(q) in _bare(s.caption_ko)), len(rows) - 1)
        rows[at] = rows[at].model_copy(update={"line_ko": q})
    return script.model_copy(update={"scenes": rows})


def missing_quotes(script: CreativeScript, quotes: list[str]) -> list[str]:
    """대본 검사: 대사에 글자 그대로 없는 의뢰 대사 문장."""
    return [q for q in quotes if not any(_bare(q) in _bare(s.line_ko) for s in script.scenes)]


async def write_request_script(
    plan: Any,
    request_text: str | None,
    character: dict[str, Any] | None,
    llm_factory: Callable[[], Any] | None,
    context_id: str | None = None,
) -> CreativeScript | None:
    """의뢰 대본. 기획 장면이 없으면 None."""
    base = plan_script(plan)
    if base is None:
        return None
    script = base
    real = (character or {}).get("kind") == "virtual_human"
    times = plan_times(plan)
    try:
        written = await _script.write_script(
            request_story(plan, request_text, real),
            len(base.scenes),
            base.total_seconds,
            character,
            llm_factory,
            context_id,
            times=times,
        )
        # 고정 규칙 대본은 줄거리(의뢰문) 문장을 대사로 옮기므로 쓰지 않는다.
        # 11/27: 기획 시간이 있으면 장면 수가 달라도 기획 수에 맞춰 쓴다(시간은 기획 값)
        if written.source == "llm" and times:
            script = _times_fixed(written, times, base)
        elif written.source == "llm" and len(written.scenes) == len(base.scenes):
            script = written
    except _script.ScriptRefused:
        pass
    script = apply_request(script, request_text)
    quotes = plan_quotes(plan)
    if missing_quotes(script, quotes):  # 작가가 빠뜨린 의뢰 대사는 코드가 넣는다 (LLM 다시 안 부름)
        logger.info("의뢰 대본: 빠진 의뢰 대사 %d개를 넣음", len(missing_quotes(script, quotes)))
        script = keep_quotes(script, quotes)
    return script


def board_with_script(board: dict[str, Any], script: CreativeScript) -> dict[str, Any]:
    """스토리보드 칸에 대본의 시간·대사·말하는 사람·자막을 붙인다(칸 수가 다르면 그대로)."""
    fields = _storyboard.StoryboardPromptOutput.model_fields
    extra = {k: v for k, v in board.items() if k not in fields}  # 기획 길이 등
    out = _storyboard.StoryboardPromptOutput.model_validate(
        {k: v for k, v in board.items() if k in fields}
    )
    if len(out.shots) != len(script.scenes):
        return board
    scenes = [s.model_dump(mode="json") for s in script.scenes]
    merged = _storyboard.with_script(out, scenes, script.cta_ko).model_dump(mode="json")
    # 11/27: 칸에 기획 시간이 있으면(#1958 B) 시간은 칸 값을 둔다 (대사·말하는 사람·자막만 붙인다)
    before = out.shots
    if all(s.start_s is not None and s.end_s is not None for s in before):
        for shot, old in zip(merged["shots"], before, strict=True):
            shot["start_s"], shot["end_s"] = old.start_s, old.end_s
    if script.source == "llm":
        # 작가가 의뢰 연출을 반영해 쓴 화면을 칸 장면으로 쓴다(영상 프롬프트가 칸 장면을 쓴다)
        for shot, scene in zip(merged["shots"], script.scenes, strict=True):
            shot["scene_ko"] = scene.visual_ko
    return {**merged, **extra}


async def attach(
    record_id: str,
    plan: Any,
    request_text: str | None,
    llm_factory: Callable[[], Any] | None,
    context_id: str | None = None,
) -> bool:
    """의뢰 기록에 대본을 쓰고 스토리보드 칸에 붙인다. 붙였으면 True."""
    store = _creative.store()
    rec = store.get(record_id) or {}
    if rec.get("script") or not rec.get("storyboard"):
        return False  # 이미 대본이 있으면(사람이 고쳤거나 다시 준비) 그대로 둔다
    script = await write_request_script(
        plan, request_text, rec.get("sheet") or {}, llm_factory, context_id
    )
    if script is None:
        return False
    story = request_story(plan, None)
    store.attach_script(record_id, script.model_dump(mode="json"), None)
    board = board_with_script(rec["storyboard"], script)
    store.attach_storyboard(record_id, rec.get("story") or story, len(board["shots"]), board)
    logger.info(
        "의뢰 대본: 기록 %s 장면 %d source=%s 첫대사=%s",
        record_id,
        len(script.scenes),
        script.source,
        "지정" if first_line(request_text) else "없음",
    )
    return True
