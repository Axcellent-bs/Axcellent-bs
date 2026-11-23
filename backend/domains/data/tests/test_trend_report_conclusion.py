"""분석 보고서 추천 결론: 의뢰의 나이대·길이 숫자 때문에 결론이 버려지지 않게 (11/23, TR-0A49).

- 프롬프트 데이터의 의뢰 숫자는 말로 ("남성 이십대·삼십대", "삼십 초 이내")
- 결론 검사: 그 의뢰의 나이대·초 표현만 허용, 조회수·배수·%는 지금처럼 버림
- 그래도 버려지면 결론만 한 번 다시 묻는다(숫자 없이 + 버린 이유). 또 실패하면 unknown
실제 LLM은 부르지 않는다(가짜).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

from data_agent import number_words as nw
from data_agent.llm_budget import LLMDailyCapReached
from data_agent.skills import trend_report as tr


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers_conclusion", "test_trend_report.py")


class Scripted:
    """추천 답(결론·재료)과 다시 묻기 답(결론)을 차례로. 부른 프롬프트·스키마·데이터를 남긴다."""

    def __init__(self, first: str, again: str | Exception | None = None) -> None:
        self.first, self.again = first, again
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append((schema.__name__, prompt, dict(untrusted or {})))
        if schema.__name__ == "_Conclusion":
            if isinstance(self.again, Exception):
                raise self.again
            return schema.model_validate({"conclusion": self.again or ""})
        ids = [k.removeprefix("근거_") for k in (untrusted or {}) if k.startswith("근거_")]
        return schema.model_validate({"conclusion": self.first, "materials": [
            {"kind": "hashtag", "text": "해시태그를 쓴다", "evidence": ids[:1]}]})  # fmt: skip


def _run(tmp_path: Path, llm: Scripted):  # noqa: ANN202
    report = H._filled(tmp_path)  # noqa: SLF001
    brief = report.brief.model_copy(update={
        "target_gender": "m", "target_ages": ["20", "30"], "format": "30초 이내 쇼츠",
        "format_parts": ["30초 이내", "쇼츠"]})  # fmt: skip
    report = report.model_copy(update={"brief": brief})
    return asyncio.run(tr.recommendation_section(report, llm))


# ---------------------------------------------------------------- 숫자 → 말
def test_number_words() -> None:
    assert [nw.sino(n) for n in (1, 10, 11, 20, 30, 45, 100, 105, 120, 180)] == [
        "일", "십", "십일", "이십", "삼십", "사십오", "백", "백오", "백이십", "백팔십"]  # fmt: skip
    assert [nw.age_word(a) for a in ("10", "20", "60", 70, "25")] == [
        "십대", "이십대", "육십대", "70대", "25대"]  # 10~60대(10 단위)만 말로
    assert nw.target_words("남성", ["20", "30"]) == "남성 이십대·삼십대"
    assert nw.target_words(None, []) == "전체"
    assert nw.seconds_to_words("30초 이내 쇼츠") == "삼십 초 이내 쇼츠"
    assert nw.seconds_to_words("15초·180초·200초") == "십오 초·백팔십 초·200초"  # 1~180만
    assert nw.seconds_to_words(None) == ""
    assert nw.seconds_in("30초 이내, 9:16, 200초") == [30]


def test_strip_only_the_brief_numbers() -> None:
    ages, secs = ["20", "30"], [30]
    for ok in ("20·30대 남성에게", "20대·30대 남성", "20, 30대", "30대 남성", "30초 안팎 쇼츠"):
        assert not any(ch.isdigit() for ch in nw.strip_brief_numbers(ok, ages, secs)), ok
    for bad in ("조회수 3배", "40대 남성", "15초 쇼츠", "좋아요 120%", "200대"):
        assert any(ch.isdigit() for ch in nw.strip_brief_numbers(bad, ages, secs)), bad


# ---------------------------------------------------------------- 결론
def test_prompt_data_has_no_brief_digits(tmp_path: Path) -> None:
    llm = Scripted("이십대 남성에게 북토크 쇼츠")
    _run(tmp_path, llm)
    data = llm.calls[0][2]
    assert data["의뢰_타깃"] == "남성 이십대·삼십대"
    assert data["의뢰_형식"] == "삼십 초 이내 쇼츠"


def test_brief_age_numbers_in_the_conclusion_pass(tmp_path: Path) -> None:
    llm = Scripted("20·30대 남성에게 북토크 쇼츠")
    section, calls, limits = _run(tmp_path, llm)
    assert section.status == "known" and section.conclusion == "20·30대 남성에게 북토크 쇼츠"
    assert calls == 1 and len(llm.calls) == 1  # 다시 묻지 않는다
    assert not any("결론" in line for line in limits)


def test_other_numbers_are_dropped_then_asked_again(tmp_path: Path) -> None:
    llm = Scripted("조회수 3배를 노리는 북토크 쇼츠", again="책 읽는 일상을 짧게 보여 주는 쇼츠")
    section, calls, limits = _run(tmp_path, llm)
    assert section.status == "known" and section.conclusion == "책 읽는 일상을 짧게 보여 주는 쇼츠"
    assert calls == 2 and [c[0] for c in llm.calls] == ["_Recommendation", "_Conclusion"]
    retry_prompt = llm.calls[1][1]
    assert "숫자(0~9) 없이 다시 써라" in retry_prompt and "숫자가 들어 있어" in retry_prompt
    assert any("다시 받음" in line for line in limits)


def test_retry_that_fails_again_stays_unknown(tmp_path: Path) -> None:
    llm = Scripted("조회수 3배", again="좋아요 120% 늘리기")
    section, calls, limits = _run(tmp_path, llm)
    assert section.status == "unknown" and section.conclusion is None
    assert calls == 2
    assert any("결론 한 줄을 버림" in line for line in limits)


def test_retry_blocked_by_the_daily_cap_stays_unknown(tmp_path: Path) -> None:
    llm = Scripted("조회수 3배", again=LLMDailyCapReached("하루 상한"))
    section, calls, _limits = _run(tmp_path, llm)
    assert section.status == "unknown" and section.conclusion is None
    assert calls == 2 and section.reason and "다시 묻지 못함" in section.reason


def test_materials_rule_is_unchanged() -> None:
    """재료는 지금 규칙 그대로: 의뢰 나이대라도 숫자가 든 재료는 버린다."""
    assert tr.check_material("20대에게 해시태그")[0] is None
