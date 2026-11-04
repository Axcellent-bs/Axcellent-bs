"""domain_profile: 회사 소개 + 카탈로그 → 업종·상품군·고객 축·질문·근거 있는 씨앗 키워드.

실제 LLM·shop-api를 부르지 않는다 (가짜 LLM·테스트용 카탈로그).
- 목록 밖 업종·고객 축, 카탈로그에 없는 카테고리 코드는 버린다. 상품 수는 코드가 센다
- 근거 문장이 원문에 없거나 키워드가 근거에 없으면 씨앗 키워드를 버리고 이유를 남긴다
- 사람 덮어쓰기가 먼저, 저장 재사용(카탈로그가 바뀌면 다시)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ax_agent_template.llm import LLMNotConfiguredError, MockLLMClient
from ax_contracts.schemas.data import DomainProfileRequest
from data_agent.connectors import CatalogProduct
from data_agent.skills import domain_profile as dp
from data_agent.skills.product_link import CatalogRead

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)

CATALOG = CatalogRead(
    products=[
        CatalogProduct(product_id="p1", name="고양이 그림책", category_code="book"),
        CatalogProduct(product_id="p2", name="파이썬 입문서", category_code="book"),
        CatalogProduct(product_id="p3", name="무지 노트 3권 세트", category_code="stat",
                       description="줄 없는 무지 노트"),
    ],
    categories={"book": "도서", "stat": "문구"},
)  # fmt: skip


class ProfileLLM(MockLLMClient):
    def __init__(self, answer: dp._Profile | None = None, fail: bool = False) -> None:
        super().__init__()
        self.answer = answer
        self.fail = fail
        self.asked = 0
        self.seen: dict[str, str] = {}

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.asked += 1
        self.seen = dict(untrusted or {})
        if self.fail:
            raise LLMNotConfiguredError("data")
        return self.answer


def _answer(**kw) -> dp._Profile:  # noqa: ANN003
    base = dict(
        industry="출판·교육",
        product_groups=[dp._Group(name="도서", category_codes=["book"]),
                        dp._Group(name="문구", category_codes=["stat", "없는코드"]),
                        dp._Group(name="가전", category_codes=["tv"])],
        customer_axes=["연령대", "구매 목적(선물·자기용)", "MBTI"],
        questions=["어떤 연령대가 그림책을 찾나", "작년 대비 30% 오른 상품은"],
        seeds=[dp._Seed(keyword="고양이 그림책", evidence="고양이 그림책"),
               dp._Seed(keyword="무지 노트", evidence="줄 없는 무지 노트"),
               dp._Seed(keyword="서점", evidence="고양이가 지키는 서점"),
               dp._Seed(keyword="캠핑", evidence="캠핑 용품"),
               dp._Seed(keyword="에세이", evidence="고양이 그림책")],
    )  # fmt: skip
    return dp._Profile(**(base | kw))


def _env(tmp_path: Path, override: str = "") -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "tenant.toml"
    cfg.write_text(
        'name = "테스트서점"\n[profile]\nabout = "책과 문구를 파는 쇼핑몰. 고양이가 지키는 서점."\n'
        "[profile.override]\n" + override,
        encoding="utf-8",
    )
    return {"DATA_TENANT_CONFIG": str(cfg), "DATA_DOMAIN_PROFILE_DB": str(tmp_path / "p.db")}


def _run(tmp_path: Path, llm: ProfileLLM, *, catalog: CatalogRead = CATALOG, override: str = "",
         regenerate: bool = False):  # noqa: ANN202  # fmt: skip
    return asyncio.run(
        dp.make_profile(
            DomainProfileRequest(regenerate=regenerate),
            llm,
            env=_env(tmp_path, override),
            catalog=catalog,
            now=NOW,
        )  # fmt: skip
    ).output


def test_profile_keeps_only_listed_values_and_counts_products(tmp_path: Path) -> None:
    out = _run(tmp_path, ProfileLLM(_answer()))
    assert out.status == "known" and out.industry == "출판·교육"
    assert out.industry_tags == ["출판·교육"] and out.tenant == "테스트서점"
    groups = {g.name: g for g in out.product_groups}
    assert set(groups) == {"도서", "문구"}  # 카탈로그에 없는 코드만 있는 상품군은 버림
    assert groups["도서"].product_count == 2 and groups["문구"].category_codes == ["stat"]
    assert out.customer_axes == ["연령대", "구매 목적(선물·자기용)"]  # 목록 밖(MBTI) 버림
    assert out.questions == ["어떤 연령대가 그림책을 찾나"]  # 숫자 든 질문은 버림
    assert out.products_seen == 3


def test_seed_keywords_need_evidence_in_the_source_text(tmp_path: Path) -> None:
    out = _run(tmp_path, ProfileLLM(_answer()))
    seeds = {s.keyword: s for s in out.seed_keywords}
    assert seeds["고양이 그림책"].evidence_from == "product"
    assert seeds["고양이 그림책"].product_ids == ["p1"]
    assert seeds["무지 노트"].evidence_from in ("product", "description")
    assert seeds["서점"].evidence_from == "about"
    dropped = {d.keyword: d.reason for d in out.dropped_keywords}
    assert "캠핑" in dropped  # 근거 문장이 원문에 없음
    assert "에세이" in dropped  # 키워드가 근거에 없음


def test_override_wins_and_is_recorded(tmp_path: Path) -> None:
    override = (
        'industry = "여가·취미"\nseed_keywords = ["독서 모임"]\ndrop_keywords = ["서점"]\n'
        'customer_axes = ["성별"]\n'
    )
    out = _run(tmp_path, ProfileLLM(_answer()), override=override)
    assert out.industry == "여가·취미" and out.customer_axes == ["성별"]
    names = [s.keyword for s in out.seed_keywords]
    assert "서점" not in names and "독서 모임" in names
    extra = next(s for s in out.seed_keywords if s.keyword == "독서 모임")
    assert extra.evidence_from == "override"
    assert set(out.overridden) == {"industry", "customer_axes", "seed_keywords", "drop_keywords"}


def test_profile_is_reused_until_the_catalog_changes(tmp_path: Path) -> None:
    llm = ProfileLLM(_answer())
    first = _run(tmp_path, llm)
    again = _run(tmp_path, llm)
    assert again.cached and llm.asked == 1 and again.catalog_hash == first.catalog_hash
    more = CatalogRead(
        products=[*CATALOG.products, CatalogProduct(product_id="p4", name="고양이 달력",
                                                    category_code="stat")],
        categories=CATALOG.categories,
    )  # fmt: skip
    changed = _run(tmp_path, llm, catalog=more)
    assert not changed.cached and llm.asked == 2
    fresh = _run(tmp_path, llm, regenerate=True)
    assert not fresh.cached and llm.asked == 3


def test_llm_failure_or_no_input_says_why_without_inventing(tmp_path: Path) -> None:
    out = _run(tmp_path, ProfileLLM(fail=True))
    assert out.status == "unknown" and out.reason == "LLM 설정이 없어 프로필을 만들지 못함"
    assert out.seed_keywords == [] and out.industry is None
    empty = asyncio.run(
        dp.make_profile(DomainProfileRequest(), ProfileLLM(_answer()),
                        env={"DATA_DOMAIN_PROFILE_DB": str(tmp_path / "e.db")},
                        catalog=CatalogRead([], {}, "회사 상품 목록을 주는 커넥터가 없음"), now=NOW)
    ).output  # fmt: skip
    assert empty.status == "unknown" and "회사 소개도 카탈로그도 없음" in (empty.reason or "")


def test_out_of_list_industry_is_dropped(tmp_path: Path) -> None:
    out = _run(tmp_path, ProfileLLM(_answer(industry="반려묘 출판")))
    assert out.industry is None and out.status == "unknown"
    assert any("정해진 목록 밖" in x for x in out.limits)


def test_catalog_and_about_go_only_into_the_data_block(tmp_path: Path) -> None:
    llm = ProfileLLM(_answer())
    _run(tmp_path, llm)
    assert llm.seen["회사 소개"].startswith("책과 문구")
    assert llm.seen["상품3"] == "무지 노트 3권 세트 | 카테고리 stat | 줄 없는 무지 노트"


def test_profile_without_catalog_is_not_stored(tmp_path: Path) -> None:
    llm = ProfileLLM(_answer(seeds=[dp._Seed(keyword="서점", evidence="고양이가 지키는 서점")]))
    no_catalog = CatalogRead([], {}, "회사 상품 목록을 읽지 못함 [UNAVAILABLE]")
    first = _run(tmp_path, llm, catalog=no_catalog)
    assert first.status == "known" and any("카탈로그를 읽지 못해" in x for x in first.limits)
    again = _run(tmp_path, llm, catalog=no_catalog)
    assert not again.cached and llm.asked == 2  # 카탈로그가 돌아오면 다시 만들게 저장하지 않음


def test_evidence_copied_as_a_data_block_line_still_matches_the_source(tmp_path: Path) -> None:
    """실제 LLM은 근거로 데이터 블록 줄("이름 | 카테고리 코드")을 통째로 옮기기도 한다."""
    seeds = [
        dp._Seed(keyword="파이썬 입문서", evidence="파이썬 입문서 | 카테고리 book"),
        dp._Seed(
            keyword="무지 노트", evidence="무지 노트 3권 세트 | 카테고리 stat | 줄 없는 무지 노트"
        ),
        dp._Seed(
            keyword="문구 서점", evidence="문구를 파는 쇼핑몰. 고양이가 지키는 서점"
        ),  # 다른 문장
        dp._Seed(keyword="만년필", evidence="만년필 | 카테고리 stat"),  # 카탈로그에 없는 상품
    ]
    out = _run(tmp_path, ProfileLLM(_answer(seeds=seeds)))
    kept = {s.keyword: s for s in out.seed_keywords}
    assert kept["파이썬 입문서"].evidence == "파이썬 입문서" and kept[
        "파이썬 입문서"
    ].product_ids == ["p2"]
    assert kept["무지 노트"].evidence_from == "product" and kept["무지 노트"].product_ids == ["p3"]
    assert {d.keyword for d in out.dropped_keywords} == {"문구 서점", "만년필"}


def test_intro_sentence_with_separated_words_is_evidence(tmp_path: Path) -> None:
    """'고양이 서점'은 소개 문장 '고양이가 지키는 서점'에 낱말이 떨어져 있다 (#581 뒤 원인)."""
    seeds = [
        dp._Seed(keyword="고양이 서점", evidence="고양이가 지키는 서점"),
        dp._Seed(keyword="고양이 문구", evidence="고양이가 지키는 서점"),  # 문구는 그 문장에 없음
    ]
    out = _run(tmp_path, ProfileLLM(_answer(seeds=seeds)))
    kept = {s.keyword: s for s in out.seed_keywords}
    assert kept["고양이 서점"].evidence_from == "about"
    assert kept["고양이 서점"].evidence == "고양이가 지키는 서점"
    assert {d.keyword for d in out.dropped_keywords} == {"고양이 문구"}


def test_subtopic_axes_come_from_the_llm_and_are_checked(tmp_path: Path) -> None:
    axes = [
        "활용 방식",
        "같이 묶이는 관심사",
        "기타",
        "2026 트렌드",
        "아주아주아주긴축이름입니다",
        "활용 방식",
    ]
    out = _run(tmp_path, ProfileLLM(_answer(subtopic_axes=axes)))
    assert out.subtopic_axes == ["활용 방식", "같이 묶이는 관심사"]  # 기타·숫자·긴 이름·중복 뺌
    one = _run(tmp_path / "b", ProfileLLM(_answer(subtopic_axes=["활용 방식"])))
    assert any("하위 주제 축이 1개" in x for x in one.limits)


def _about(tmp_path: Path, about: str) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "tenant.toml"
    cfg.write_text(f'[profile]\nabout = "{about}"\n', encoding="utf-8")
    return {"DATA_TENANT_CONFIG": str(cfg), "DATA_DOMAIN_PROFILE_DB": str(tmp_path / "p.db")}


def _profile(env: dict[str, str], llm: ProfileLLM):  # noqa: ANN202
    return asyncio.run(
        dp.make_profile(DomainProfileRequest(), llm, env=env, catalog=CATALOG, now=NOW)
    ).output


def test_profile_is_rebuilt_when_the_intro_changes(tmp_path: Path) -> None:
    llm = ProfileLLM(_answer())
    first = _profile(_about(tmp_path, "책과 문구를 파는 쇼핑몰."), llm)
    again = _profile(_about(tmp_path, "책과 문구를 파는 쇼핑몰."), llm)
    assert again.cached and llm.asked == 1
    changed = _profile(_about(tmp_path, "책을 파는 출판사다."), llm)  # 같은 저장소, 소개만 바뀜
    assert not changed.cached and llm.asked == 2 and changed.catalog_hash != first.catalog_hash


def test_groups_missing_from_the_intro_are_warned_with_their_products(tmp_path: Path) -> None:
    answer = _answer(groups_not_in_intro=["문구", "가전", "도서"])
    out = _profile(
        _about(tmp_path, "책을 파는 출판사다. 소설과 IT 학습서를 판다."), ProfileLLM(answer)
    )
    # 가전: 검증된 상품군이 아님 → 버림. 도서: LLM이 골랐지만 '도서'가 소개에 없어 경고 대상
    # (소개에 '책'만 있음, 원문 그대로가 아니면 LLM 판단을 따른다)
    warned = {m.group: m for m in out.intro_mismatch}
    assert set(warned) == {"문구", "도서"}
    assert [p.name for p in warned["문구"].products] == ["무지 노트 3권 세트"]
    assert warned["문구"].category_codes == ["stat"] and "카탈로그에 1개" in warned["문구"].reason
    # 카탈로그 상품은 경고 목록에 그대로, 그 상품의 씨앗 키워드만 뺀다 (#594 → 씨앗 빼기)
    assert "무지 노트" not in {s.keyword for s in out.seed_keywords}
    assert any(x.startswith("소개와 카탈로그가 어긋남") for x in out.limits)


def test_mismatch_seeds_are_skipped_and_human_override_brings_them_back(tmp_path: Path) -> None:
    seeds = [
        dp._Seed(keyword="무지 노트", evidence="무지 노트 3권 세트"),  # 경고 상품군(문구) 상품
        dp._Seed(keyword="문구", evidence="문구"),  # 경고 상품군의 카테고리 이름
        dp._Seed(keyword="고양이 그림책", evidence="고양이 그림책"),  # 도서: 경고 아님
        dp._Seed(keyword="출판사", evidence="책을 파는 출판사다"),  # 소개가 근거라 남김
    ]
    answer = _answer(seeds=seeds, groups_not_in_intro=["문구"])
    out = _profile(_about(tmp_path, "책을 파는 출판사다."), ProfileLLM(answer))
    assert {s.keyword for s in out.seed_keywords} == {"고양이 그림책", "출판사"}
    skipped = {d.keyword: d.reason for d in out.mismatch_skipped}
    assert set(skipped) == {"무지 노트", "문구"} and "문구 상품군" in skipped["무지 노트"]
    assert [p.name for p in out.intro_mismatch[0].products] == [
        "무지 노트 3권 세트"
    ]  # 카탈로그 그대로
    env = _about(tmp_path / "o", "책을 파는 출판사다.")
    Path(env["DATA_TENANT_CONFIG"]).write_text(
        '[profile]\nabout = "책을 파는 출판사다."\n'
        '[profile.override]\nseed_keywords = ["무지 노트"]\n',
        encoding="utf-8",
    )
    back = _profile(env, ProfileLLM(answer))
    assert "무지 노트" in {s.keyword for s in back.seed_keywords}  # 사람이 넣으면 다시 씀
    assert {d.keyword for d in back.mismatch_skipped} == {"문구"}
    named = _profile(
        _about(tmp_path / "b", "책과 문구를 파는 쇼핑몰."),
        ProfileLLM(_answer(groups_not_in_intro=["문구"])),
    )
    assert named.intro_mismatch == []  # 상품군 이름이 소개에 그대로 있으면 경고하지 않음
    no_intro = _profile({"DATA_DOMAIN_PROFILE_DB": str(tmp_path / "c.db")},
                        ProfileLLM(_answer(groups_not_in_intro=["문구"])))  # fmt: skip
    assert no_intro.intro_mismatch == []  # 소개가 없으면 보지 않음


def test_profile_is_rebuilt_when_the_code_changes(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    llm = ProfileLLM(_answer())
    first = _run(tmp_path, llm)
    assert _run(tmp_path, llm).cached and llm.asked == 1
    monkeypatch.setattr(dp, "CODE_VERSION", "changed")  # 코드가 바뀐 것과 같다 (소스 해시)
    again = _run(tmp_path, llm)
    assert not again.cached and llm.asked == 2 and again.catalog_hash != first.catalog_hash


def test_each_profile_field_has_evidence_checked_against_the_source(tmp_path: Path) -> None:
    ev = [
        dp._FieldEvidence(field="industry", value="출판·교육", evidence="책과 문구를 파는 쇼핑몰"),
        dp._FieldEvidence(field="customer_axis", value="연령대", evidence="고양이 그림책"),
        dp._FieldEvidence(field="customer_axis", value="구매 목적(선물·자기용)",
                          evidence="선물 포장 서비스"),  # 원문에 없음 → 버림
        dp._FieldEvidence(field="customer_axis", value="MBTI", evidence="고양이 그림책"),  # 칸 밖
        dp._FieldEvidence(field="question", value="어떤 연령대가 그림책을 찾나",
                          evidence="상품3 | 무지 노트 3권 세트 | 줄 없는 무지 노트"),
    ]  # fmt: skip
    out = _run(tmp_path, ProfileLLM(_answer(evidence=ev)))
    by = {(e.field, e.value): e for e in out.evidence}
    assert by[("industry", "출판·교육")].evidence_from == "about"
    assert by[("industry", "출판·교육")].evidence == "책과 문구를 파는 쇼핑몰"  # 원문 문장
    assert by[("customer_axis", "연령대")].evidence_from == "product"
    assert ("customer_axis", "구매 목적(선물·자기용)") not in by
    assert ("customer_axis", "MBTI") not in by
    assert by[("question", "어떤 연령대가 그림책을 찾나")].evidence_from == "product"
    group = by[("product_group", "도서")]  # 상품군은 코드가 카테고리 이름으로
    assert group.evidence == "도서" and group.evidence_from == "category"
