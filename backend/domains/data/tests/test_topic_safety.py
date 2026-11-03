"""주제 분류 보강: 실존 이름 칸, IT 분류, 저장·재사용, 덮어쓰기 표, promo 경고 기준. 가짜 LLM.

- 인물·선수가 '게임'으로 분류돼도 실존 이름 표시(real_entity·entity_names)가 붙는다
- LLM이 준 이름 중 입력에 없는 것은 버린다
- 저장된 분류(backend/.data 대신 임시 파일)를 다른 프로세스(새 저장소 객체)에서도 다시 쓴다
- 덮어쓰기 표가 있으면 LLM에 묻지 않는다
- promo의 "상표·초상권 확인 필요"는 실존 이름 기준이다
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import PromoTrend
from data_agent.connectors import CatalogProduct
from data_agent.skills.promo_concepts import _Concept, validate_concepts
from data_agent.skills.topic_category import (
    TOPIC_CATEGORIES,
    CategoryCache,
    TopicInput,
    _Categories,
    _Item,
    cache_from_env,
    classify_topics,
    load_settings,
)


def _llm(*items: _Item) -> MockLLMClient:
    return MockLLMClient(responses={_Categories: _Categories(items=list(items))})


def test_person_is_flagged_even_when_the_topic_is_a_game() -> None:
    llm = _llm(
        _Item(id="1", category="게임", real_entity=True, entity_names=["페이커", "없는이름"])
    )
    topic = TopicInput("페이커", ("faker",), ("페이커 하이라이트 모음",))
    got = asyncio.run(classify_topics([topic], llm))["페이커"]
    assert got.category == "게임"
    assert got.real_entity is True and got.entity_names == ("페이커",)  # 입력에 없는 이름은 버림
    assert "IT" in TOPIC_CATEGORIES


def test_names_alone_mark_a_real_entity_and_none_means_false() -> None:
    llm = _llm(
        _Item(id="1", category="게임", real_entity=False, entity_names=["T1"]),
        _Item(id="2", category="생활·취미"),
    )
    got = asyncio.run(classify_topics([TopicInput("t1"), TopicInput("캠핑")], llm))
    assert got["t1"].real_entity is True and got["t1"].entity_names == ("T1",)
    assert got["캠핑"].real_entity is False and got["캠핑"].entity_names == ()


def test_stored_classification_is_reused_after_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "topics.db"
    llm = _llm(_Item(id="1", category="게임", real_entity=True, entity_names=["페이커"]))
    first = CategoryCache(path)
    asyncio.run(classify_topics([TopicInput("페이커")], llm, cache=first))
    assert len(llm.calls) == 1
    restarted = CategoryCache(path)  # 서버를 다시 켠 것과 같다
    got = asyncio.run(classify_topics([TopicInput("페이커")], llm, cache=restarted))["페이커"]
    assert len(llm.calls) == 1  # 다시 묻지 않았다
    assert (got.category, got.real_entity, got.entity_names, got.source) == (
        "게임",
        True,
        ("페이커",),
        "stored",
    )


def test_override_table_skips_the_llm(tmp_path: Path) -> None:
    config = tmp_path / "trends.toml"
    config.write_text(
        "[topics]\nretention_days = 7\n\n[topics.overrides]\n"
        '"윤영미" = { category = "인물", entity_names = ["윤영미"] }\n'
        '"이상한값" = { category = "없는분류" }\n',
        encoding="utf-8",
    )
    env = {"DATA_TREND_CONFIG": str(config), "DATA_TOPIC_CATEGORY_DB": str(tmp_path / "t.db")}
    settings = load_settings(env)
    assert settings.retention_days == 7 and list(settings.overrides) == ["윤영미"]
    llm = _llm()
    got = asyncio.run(classify_topics([TopicInput("윤영미")], llm, cache=cache_from_env(env)))
    assert llm.calls == []
    assert (got["윤영미"].category, got["윤영미"].real_entity, got["윤영미"].source) == (
        "인물",
        True,
        "override",
    )
    assert load_settings({}).overrides == {}  # 비어 있어도 된다


def test_promo_warning_follows_real_entity_names_not_the_category() -> None:
    products = {"p1": CatalogProduct(product_id="p1", name="독서대", category_code="s")}
    facts: dict[str, int | float | str] = {"콘티": "1컷 2컷 3컷"}
    trends = [
        PromoTrend(
            name="lck",
            kind="popular",
            topic_category="게임",
            ip_risk=True,
            entity_names=["lck", "페이커"],
            detail="x",
        ),  # fmt: skip
        PromoTrend(
            name="모바일게임", kind="popular", topic_category="게임", ip_risk=False, detail="y"
        ),  # fmt: skip
    ]

    def concept(title: str, trend: str) -> _Concept:
        return _Concept(title=title, concept="c", borrowed_trend=trend, product_ids=["p1"],
                        duration_seconds=15, fit_reason="맞는 이유",
                        storyboard=["1컷: a", "2컷: b", "3컷: c"])  # fmt: skip

    kept, _ = validate_concepts(
        [concept("페이커처럼 집중", "lck"), concept("모바일게임 같은 공부", "모바일게임")],
        products,
        {},
        trends,
        facts,
    )
    assert kept[0].warnings == ["상표·초상권 확인 필요: '페이커'이(가) 제목·콘티에 그대로 있음"]
    assert kept[1].warnings == []  # 분류가 게임이어도 실존 이름이 아니면 경고 없음


def test_person_name_topics_are_flagged_and_old_rows_are_asked_again(tmp_path: Path) -> None:
    import sqlite3

    llm = _llm(
        _Item(
            id="1",
            category="연예·스포츠",
            real_entity=True,
            entity_names=["윤영미"],
            is_person=True,
        ),  # fmt: skip
        _Item(id="2", category="게임", real_entity=True, entity_names=["t1"]),
    )
    path = tmp_path / "topics.db"
    cache = CategoryCache(path)
    got = asyncio.run(classify_topics([TopicInput("윤영미"), TopicInput("t1")], llm, cache=cache))
    assert got["윤영미"].is_person is True and got["t1"].is_person is False
    # 사람 이름 칸이 없던 예전 줄은 다시 묻는다
    conn = sqlite3.connect(path)
    conn.execute("UPDATE topic_categories SET is_person = NULL WHERE keyword = 't1'")
    conn.commit()
    conn.close()
    again = CategoryCache(path)
    assert again.get("윤영미") is not None and again.get("t1") is None
