"""글 질문 → 고르는 칸 값 (`segment_question`, 11/16).

LLM은 가짜(MockLLMClient)로 바꿔 끼운다. 캣북스 카탈로그와 가짜 게임사 설정에서, LLM이 고른 code 중
보기에 있는 것만 남는지, 보기·질문이 프롬프트에 어떻게 들어가는지, 실패하면 unparsed인지 본다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_agent_template.llm import LLMFormatError, MockLLMClient
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import AttributeDef, CatalogSales, SegmentQuestionOutput
from data_agent import catalog, handlers
from data_agent.explain import FORMAT_ERROR, LLM_ERROR, LLM_UNAVAILABLE
from data_agent.skills import segment_question as sq

# 테스트 전용 가짜 게임사 (이름·코드는 지어낸 값)
GAME = CatalogSales(
    item_noun="게임",
    genres={"rpg": "RPG", "puzzle": "퍼즐"},
    attributes=[
        AttributeDef(key="age_band", label="연령대", values={"10s": "10대", "20s": "20대"}),
        AttributeDef(key="gender", label="성별", values={"female": "여성", "male": "남성"}),
    ],
)


def run(coro):
    return asyncio.run(coro)


def catbooks() -> CatalogSales:
    sales = catalog.load_catalog("catbooks").sales
    assert sales is not None
    return sales


def pick(filters: dict[str, str] | list[tuple[str, str]], genre: str | None = None,
         period: str | None = None) -> sq._Pick:  # fmt: skip
    pairs = filters.items() if isinstance(filters, dict) else filters
    return sq._Pick(filters=[sq._Filter(key=k, code=c) for k, c in pairs], genre_code=genre,
                    period=period)  # fmt: skip


def test_catbooks_twenties_women() -> None:
    llm = MockLLMClient(responses={sq._Pick: pick({"age_band": "20s", "gender": "female"})})
    got = run(sq.parse("20대 여성이 좋아하는 책은?", catbooks(), llm))
    out = got.output
    assert out.status == "ok" and got.fallback_reason is None
    assert out.filters == {"age_band": "20s", "gender": "female"}
    assert out.genre_code is None and out.period is None
    assert out.understood_label == "20대 · 여성 · 직업 전체 · 최근 90일"
    prompt = llm.calls[0]
    # 보기는 그 회사 카탈로그 값, 질문은 데이터 블록에만
    assert "20s=20대" in prompt and "female=여성" in prompt and "novel=소설" in prompt
    assert "90d=최근 90일" in prompt
    assert prompt.count("20대 여성이 좋아하는 책은?") == 1


def test_game_company_twenties_men() -> None:
    llm = MockLLMClient(responses={sq._Pick: pick({"age_band": "20s", "gender": "male"},
                                                  genre="rpg", period="30d")})  # fmt: skip
    out = run(sq.parse("20대 남성에게 인기 많은 게임은?", GAME, llm)).output
    assert out.status == "ok" and out.filters == {"age_band": "20s", "gender": "male"}
    assert (out.genre_code, out.period) == ("rpg", "30d")
    assert out.understood_label == "20대 · 남성 · RPG · 최근 30일"
    prompt = llm.calls[0].replace("20대 남성에게 인기 많은 게임은?", "")
    assert "rpg=RPG" in prompt and "male=남성" in prompt
    # 다른 회사 보기(캣북스 장르)는 들어가지 않는다
    assert "소설" not in prompt and "occupation" not in prompt and "책" not in prompt


def test_codes_outside_the_options_are_dropped() -> None:
    noisy = pick([("age_band", "99s"), ("job", "x"), ("gender", "female"), ("gender", "male")],
                 genre="ghost", period="7d")  # fmt: skip
    out = run(sq.parse("여성", GAME, MockLLMClient(responses={sq._Pick: noisy}))).output
    # 없는 값·없는 속성·없는 장르·없는 기간은 버리고, 한 속성에는 처음 고른 값 하나
    assert out.status == "ok" and out.filters == {"gender": "female"}
    assert out.genre_code is None and out.period is None
    assert out.understood_label == "연령대 전체 · 여성 · 최근 90일"
    nothing = pick([("age_band", "99s")], genre="rpg")
    none = run(sq.parse("아무거나", GAME, MockLLMClient(responses={sq._Pick: nothing}))).output
    assert none.status == "unparsed" and none.filters == {} and none.genre_code is None
    assert none.reason == sq.NOT_UNDERSTOOD and none.understood_label == ""


class Failing(MockLLMClient):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def structured(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error


@pytest.mark.parametrize(
    ("llm", "code"),
    [
        (None, LLM_UNAVAILABLE),
        (Failing(LLMFormatError("형식")), FORMAT_ERROR),
        (Failing(TimeoutError()), LLM_ERROR),
    ],
)
def test_llm_failure_is_unparsed(llm: Any, code: str) -> None:
    got = run(sq.parse("20대 여성", GAME, llm))
    assert got.output.status == "unparsed" and got.output.filters == {}
    assert got.fallback_reason == code and got.output.reason == sq.WHY[code]


def test_no_attributes_in_config() -> None:
    llm = MockLLMClient(responses={sq._Pick: pick({"age_band": "20s"})})
    for cfg in (None, CatalogSales()):
        out = run(sq.parse("20대", cfg, llm)).output
        assert out.status == "unparsed" and out.reason == sq.NO_ATTRIBUTES
    assert llm.calls == []  # 보기가 없으면 부르지 않는다


def _request(**inp: Any) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill="segment_question", input=inp)


def test_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    assert handlers.HANDLERS["segment_question"] is handlers.segment_question_skill
    llm = MockLLMClient(responses={sq._Pick: pick({"age_band": "20s", "gender": "female"})})
    monkeypatch.setattr(handlers, "get_llm", lambda: llm)
    catalog.use_catalog(catalog.load_catalog("catbooks"))
    try:
        ok = run(handlers.segment_question_skill(_request(text="20대 여성이 좋아하는 책은?")))
        monkeypatch.setattr(handlers, "get_llm", lambda: None)
        failed = run(handlers.segment_question_skill(_request(text="20대 여성")))
        other = run(handlers.segment_question_skill(_request(text="x", tenant_id="other")))
        empty = run(handlers.segment_question_skill(_request(text="")))
    finally:
        catalog.use_catalog(None)
    assert ok.status == TaskStatus.COMPLETED and ok.evidence and not ok.fallback
    out = SegmentQuestionOutput.model_validate(ok.output)
    assert out.status == "ok" and out.filters == {"age_band": "20s", "gender": "female"}
    assert "카탈로그 catbooks" in ok.evidence.data_summary
    assert failed.status == TaskStatus.COMPLETED and failed.fallback
    assert failed.fallback_reason == LLM_UNAVAILABLE
    assert SegmentQuestionOutput.model_validate(failed.output).status == "unparsed"
    assert other.status == TaskStatus.FAILED and other.outcome == "invalid_input"
    assert empty.status == TaskStatus.FAILED
