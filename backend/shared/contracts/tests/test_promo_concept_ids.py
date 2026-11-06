"""예시 아이디어 id 칸 (추가만, 예전 응답도 읽힌다)."""

from ax_contracts.schemas.data import PromoConcept


def test_promo_concept_id_fields_and_old_payloads() -> None:
    old = PromoConcept.model_validate({"concept_id": "pc_1", "title": "t", "borrowed_trend": "x"})
    assert old.legacy_concept_id is None and old.source_insight_id is None
    new = PromoConcept(
        concept_id="pc_0123456789abcdef", title="t", borrowed_trend="롤드컵",
        legacy_concept_id="pc_aaaaaaaaaa", source_insight_id="ins_0123456789abcdef",
    )  # fmt: skip
    again = PromoConcept.model_validate(new.model_dump(mode="json"))
    assert again.legacy_concept_id == "pc_aaaaaaaaaa"
    assert again.source_insight_id == "ins_0123456789abcdef"
