"""형식 빌리기 인사이트·근거 요약 low_sample·영상 탐색 3일 검색 (추가만, 예전 응답도 읽힌다)."""

from ax_contracts.schemas.data import (
    BorrowFormat,
    EvidenceHighlight,
    ExplorerSearch,
    InsightProduct,
    TrendInsight,
)


def test_format_insight_fields_and_old_payloads() -> None:
    old = TrendInsight.model_validate({"insight_id": "i", "rank": 1, "topic": "t", "line": ""})
    assert old.borrow_format is None and old.borrow_products == [] and old.cautions == []
    new = TrendInsight(
        insight_id="i", rank=1, topic="롤드컵", line="", company_relevance="format",
        borrow_format=BorrowFormat(name="챌린지", median_views=120000, samples=18, source="all"),
        borrow_products=[InsightProduct(product_id="p", name="롤 설정집", reason="예시 아이디어")],
        cautions=["trademark"], caution_note="이름·로고는 쓰지 말고 형식만",
    )  # fmt: skip
    again = TrendInsight.model_validate(new.model_dump(mode="json"))
    assert again.company_relevance == "format" and again.borrow_format.name == "챌린지"


def test_low_sample_and_three_day_window() -> None:
    h = EvidenceHighlight(source="youtube", label="x", value=1, unit="회", sentence="x")
    assert h.low_sample is False and h.sample_note is None
    s = ExplorerSearch(category="게임", length="short", window="3d", status="known")
    assert s.window == "3d"
