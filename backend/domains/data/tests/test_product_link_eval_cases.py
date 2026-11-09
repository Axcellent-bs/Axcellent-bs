"""소재 ↔ 상품 연결 보정셋(`eval/product_link/`)의 약속을 지키는지 본다.

임베딩·피드를 부르지 않는다.

- 정답·나눔은 `catalog.json`·`topics.json`과 seed만으로 다시 만들어진다(커밋된 `cases.json`과 같다)
- 정답은 만든 방식에서 나온다: 이름 질의 = 그 상품, 카테고리 질의 = 그 카테고리 전부, 무관 = 없음
- 질의는 정해진 규칙(핵심어·카테고리 + 꼬리말)으로만 만들었고, 무관 소재는 상품 낱말과 겹치지 않는다
- 남의 글(영상 제목·뉴스 제목)은 커밋하지 않는다(개수만)
- 정의·정답·채점 코드는 네트워크 코드를 import하지 않는다
- 저장된 코사인은 모든 소재 × 상품을 덮는다
holdout은 숫자만 본다. 여기서는 다시 만든 결과와 같은지·개수만 기계로 본다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

LINK = Path(__file__).resolve().parents[1] / "eval" / "product_link"


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(LINK))
    try:
        spec = importlib.util.spec_from_file_location(f"product_link_{name}", LINK / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(LINK))


def _json(name: str) -> dict:
    return json.loads((LINK / name).read_text(encoding="utf-8"))


def test_cases_are_rebuilt_from_saved_inputs_and_seed() -> None:
    make = _load("make_cases")
    catalog = _json("catalog.json")["products"]
    topics = _json("topics.json")["topics"]
    saved = _json("cases.json")
    assert saved["seed"] == make.SEED
    assert make.build(catalog, topics) == saved["cases"]


def test_answers_come_from_how_the_query_was_made() -> None:
    catalog = {p["product_id"]: p for p in _json("catalog.json")["products"]}
    topics = {t["id"]: t for t in _json("topics.json")["topics"]}
    for case in _json("cases.json")["cases"]:
        topic = topics[case["id"]]
        if case["kind"] == "related_name":
            assert case["relevant"] == [topic["source_product"]]
        elif case["kind"] == "related_category":
            assert case["relevant"] == [
                pid for pid, p in catalog.items() if p["category_code"] == topic["source_category"]
            ]
            assert case["relevant"]
        else:
            assert case["kind"] in ("unrelated_trending", "unrelated_youtube")
            assert case["relevant"] == []


def test_queries_follow_the_fixed_rules_and_unrelated_topics_do_not_touch_products() -> None:
    rules = _load("rules")
    catalog = _json("catalog.json")["products"]
    by_id = {p["product_id"]: p for p in catalog}
    labels = {p["category_name"] for p in catalog if p["category_name"]}
    for topic in _json("topics.json")["topics"]:
        if topic["kind"] == "related_name":
            core = rules.name_core(by_id[topic["source_product"]]["name"])
            assert topic["text"] in [t.format(core) for t in rules.NAME_TEMPLATES]
        elif topic["kind"] == "related_category":
            assert topic["text"] in [
                t.format(label) for label in labels for t in rules.CATEGORY_TEMPLATES
            ]
        else:
            assert not rules.overlaps_catalog(topic["text"], catalog)
            assert topic["evidence_count"] <= rules.MAX_EVIDENCE


def test_rule_helpers() -> None:
    rules = _load("rules")
    assert rules.name_core("무지 노트 3권 세트") == "무지 노트"
    assert rules.name_core("젤펜 12색") == "젤펜"
    assert rules.name_core("독서대") == "독서대"
    catalog = [{"name": "만년필 입문 세트", "category_name": "문구"}]
    assert rules.overlaps_catalog("만년필 추천", catalog)  # 상품 낱말이 들었다
    assert rules.overlaps_catalog("문구점", catalog)  # 카테고리 이름이 들었다
    assert not rules.overlaps_catalog("리그오브레전드", catalog)
    assert rules.rich_topic_text("t1", ["a", "b", "c", "d"]) == "t1\na\nb\nc"
    assert rules.rich_product_text({"name": "독서대", "category_name": "문구"}) == "독서대 (문구)"
    assert (
        rules.rich_product_text({"name": "독서대", "category_name": "문구", "description": "각도"})
        == "독서대 (문구)\n각도"
    )


def test_third_party_text_is_not_committed() -> None:
    topics = _json("topics.json")
    for topic in topics["topics"]:
        assert set(topic) <= {
            "id",
            "kind",
            "text",
            "evidence_count",
            "source_product",
            "source_category",
        }
    for path in sorted((LINK / "sims").glob("*.json")):
        assert set(json.loads(path.read_text(encoding="utf-8"))) == {
            "form",
            "model",
            "collected_at",
            "products",
            "sims",
        }


@pytest.mark.parametrize("name", ["rules", "make_cases", "score"])
def test_definition_and_scoring_do_not_touch_network(name: str) -> None:
    source = (LINK / f"{name}.py").read_text(encoding="utf-8")
    for word in ("httpx", "urllib", "requests", "data_agent", "ax_agent_template", "socket"):
        assert word not in source


def test_saved_similarities_cover_every_topic_and_product() -> None:
    products = [p["product_id"] for p in _json("catalog.json")["products"]]
    ids = {t["id"] for t in _json("topics.json")["topics"]}
    for form in ("basic", "rich"):
        sims = _json(f"sims/{form}.json")
        assert sims["form"] == form
        assert sims["products"] == products
        assert set(sims["sims"]) == ids
        for row in sims["sims"].values():
            assert len(row) == len(products)
            assert all(-1.0 <= v <= 1.0 for v in row)


def test_split_has_both_kinds_in_dev_and_holdout() -> None:
    cases = _json("cases.json")["cases"]
    for split in ("dev", "holdout"):
        part = [c for c in cases if c["split"] == split]
        assert any(c["relevant"] for c in part)
        assert any(not c["relevant"] for c in part)


def test_decide_rules() -> None:
    score = _load("score")
    p = score.Params
    sims = [0.70, 0.66, 0.40, 0.30]
    assert score.decide(sims, p("none", 0.6, 0.0)) == [0, 1]
    assert score.decide(sims, p("none", 0.8, 0.0)) == []
    assert score.decide(sims, p("top2", 0.6, 0.0)) == [0]  # 1등만 이을 수 있다
    assert score.decide(sims, p("top2", 0.6, 0.05)) == []  # 0.70 − 0.66 = 0.04 < 0.05
    # mean: 1등 0.70 − (0.66+0.40+0.30)/3 = 0.247, 2등 0.66 − (0.70+0.40+0.30)/3 = 0.193
    assert score.decide(sims, p("mean", 0.6, 0.2)) == [0]
    assert score.decide(sims, p("mean", 0.6, 0.15)) == [0, 1]
    assert score.decide([0.9, 0.9, 0.9, 0.9, 0.9], p("none", 0.5, 0.0)) == [0, 1, 2]  # 최대 3개


def test_evaluate_counts_and_selection_rule() -> None:
    score = _load("score")
    sims = {
        "products": ["a", "b"],
        "sims": {"r1": [0.8, 0.5], "r2": [0.55, 0.7], "u1": [0.62, 0.3]},
    }
    cases = [
        {"id": "r1", "split": "dev", "relevant": ["a"]},
        {"id": "r2", "split": "dev", "relevant": ["b"]},
        {"id": "u1", "split": "dev", "relevant": []},
    ]
    m = score.evaluate(cases, sims, score.Params("none", 0.6, 0.0), "dev")
    assert (m.related, m.related_hit, m.links, m.correct, m.unrelated, m.unrelated_linked) == (
        2,
        2,
        3,
        2,
        1,
        1,
    )
    assert m.false_rate == 1.0
    # 무관 소재(0.62)를 잇지 않으면서 관련 둘(0.8·0.7)을 다 잡는 설정은 0.65·0.70.
    # 재현율이 같으면 유사도 기준이 큰 쪽을 고른다
    chosen = score.select(cases, sims, ("none",))
    assert chosen == score.Params("none", 0.7, 0.0)
    assert score.evaluate(cases, sims, chosen, "dev").recall == 1.0
