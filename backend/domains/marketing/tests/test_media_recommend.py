"""기획안 → 미디어 종류·필드 추천 규칙(`media_recommend.py`). 순수 계산, 외부 호출 없음."""

from datetime import UTC, datetime

import pytest

from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent.brief_handoff import template_fields_by_kind
from marketing_agent.media.prompts import check_field_value
from marketing_agent.media_recommend import recommend, recommend_fields, recommend_kind

NOVEMBER = datetime(2026, 11, 13, 3, 0, tzinfo=UTC)
FIELDS = ("season", "color_theme", "category")


def _brief(**over) -> CampaignBriefInput:  # noqa: ANN003
    base = {
        "proposal_id": "PROP-5",
        "title": "기획전",
        "idea": "신간을 알린다",
        "why": "검색이 늘었다",
        "approved_by": "예진",
    }
    return CampaignBriefInput.model_validate(base | over)


@pytest.mark.parametrize(
    ("over", "kind", "rule"),
    [
        # 1. format이 영상·캐러셀을 말한다
        ({"format": "숏폼 15초"}, "video", "format_video"),
        ({"format": "숏폼 30초", "idea": "카드뉴스처럼 정리"}, "video", "format_video"),
        ({"format": "캐러셀"}, "carousel", "format_carousel"),
        # 3. 글의 영상 신호
        ({"idea": "릴스로 신간을 알린다"}, "video", "video_keyword"),
        ({"idea": "고양이가 책을 꺼내는 움직임을 보여 준다"}, "video", "video_keyword"),
        ({"title": "Shorts 캠페인"}, "video", "video_keyword"),
        ({"note": "짧은 영상으로"}, "video", "video_keyword"),
        ({"body_md": "## 채널\n틱톡 숏폼"}, "video", "video_keyword"),
        ({"format": "배너", "idea": "릴스로 알린다"}, "video", "video_keyword"),
        # 4. 캐러셀 신호
        ({"idea": "카드뉴스로 정리한다"}, "carousel", "carousel_keyword"),
        ({"idea": "여러 장으로 넘겨 보게 한다"}, "carousel", "carousel_keyword"),
        # 5. 나열 신호: 상품 수로 갈린다
        (
            {"idea": "가을 카탈로그를 만든다", "products": ["A", "B", "C"]},
            "carousel",
            "listing_products",
        ),
        ({"idea": "가을 카탈로그를 만든다", "products": ["A"]}, "image", "listing_single"),
        ({"idea": "정보 카드로 알린다"}, "image", "listing_single"),
        # 6. 10대 타깃 + SNS 채널
        ({"idea": "10대에게 인스타그램으로 알린다"}, "video", "teen_shortform"),
        ({"why": "청소년 검색이 늘었다", "note": "SNS 위주"}, "video", "teen_shortform"),
        # 7. 이미지 신호·정지 형식
        ({"idea": "포스터 한 장으로 알린다"}, "image", "image_keyword"),
        ({"format": "배너"}, "image", "format_still"),
        ({"format": "공지"}, "image", "format_still"),
        # 8. 신호 없음 → 가장 싼 기본값
        ({}, "image", "default_image"),
        ({"idea": "10대에게 알린다"}, "image", "default_image"),  # 채널 언급이 없다
    ],
)
def test_kind_rules(over: dict, kind: str, rule: str) -> None:
    got_kind, got_rule, reason, _ = recommend_kind(_brief(**over))
    assert (got_kind, got_rule) == (kind, rule)
    assert reason and "\n" not in reason  # 근거는 한 줄


@pytest.mark.parametrize(
    "idea",
    [
        "영상 없이 이미지로만 알린다",
        "영상 대신 포스터",
        "릴스는 제외하고 이미지로 진행",
        "영상은 말고 사진",
    ],
)
def test_negated_video_signal_is_not_counted(idea: str) -> None:
    kind, rule, _, _ = recommend_kind(_brief(idea=idea))
    assert kind == "image" and rule == "image_keyword"


def test_english_keywords_use_word_boundaries() -> None:
    assert recommend_kind(_brief(idea="a videography note"))[1] == "default_image"
    assert recommend_kind(_brief(idea="post a reel"))[0] == "video"


def test_video_and_carousel_together_falls_back_to_image_with_low_confidence() -> None:
    kind, rule, reason, confidence = recommend_kind(_brief(idea="릴스와 카드뉴스를 같이"))
    assert (kind, rule, confidence) == ("image", "conflict_video_carousel", "low")
    assert "릴스" in reason and "카드뉴스" in reason


def test_confidence_marks_inferred_and_default_choices() -> None:
    assert recommend_kind(_brief())[3] == "low"
    assert recommend_kind(_brief(idea="10대에게 틱톡 계정으로 알린다"))[3] == "high"  # 틱톡 = 영상
    assert recommend_kind(_brief(idea="10대에게 인스타그램으로"))[3] == "low"
    assert recommend_kind(_brief(format="숏폼 15초"))[3] == "high"


def test_fact_values_are_not_read_as_signals() -> None:
    brief = _brief(
        facts=[{"label": "검색량", "value": "영상 조회 3월 1.6배", "source": "t", "link": None}]
    )
    rec = recommend(brief, NOVEMBER, template_fields_by_kind())
    assert rec.rule == "default_image"
    assert {n.field: n.source for n in rec.field_notes}["season"] == "default"


def _notes(**over) -> dict:  # noqa: ANN003
    return {n.field: n for n in recommend_fields(_brief(**over), NOVEMBER, FIELDS)}


@pytest.mark.parametrize(
    ("over", "season"),
    [
        ({"idea": "11월 독서 키워드를 묶는다"}, "autumn"),
        ({"idea": "3월 신학기 준비"}, "spring"),
        ({"title": "여름 휴가 책"}, "summer"),
        ({"idea": "크리스마스 선물"}, "winter"),
        ({"idea": "봄맞이 정리"}, "spring"),
        ({"idea": "12월과 1월 사이"}, "winter"),
        ({"title": "겨울 기획전", "idea": "7월부터 준비"}, "winter"),  # 제목이 먼저다
    ],
)
def test_season_comes_from_brief(over: dict, season: str) -> None:
    note = _notes(**over)["season"]
    assert (note.value, note.source) == (season, "brief")


def test_season_ignores_lookalikes_and_defaults_to_received_date() -> None:
    note = _notes(idea="돌봄 도서를 3개월간 1.6배로")["season"]
    assert (note.value, note.source) == ("autumn", "default")
    assert "11월" in note.reason
    january = datetime(2027, 1, 2, tzinfo=UTC)
    assert recommend_fields(_brief(), january, FIELDS)[0].value == "winter"
    # 받은 시각은 한국 시간으로 본다: UTC 2월 28일 20시 = 한국 3월 1일
    edge = datetime(2027, 2, 28, 20, 0, tzinfo=UTC)
    assert recommend_fields(_brief(), edge, FIELDS)[0].value == "spring"


def test_color_from_brief_or_season_default() -> None:
    assert _notes(idea="따뜻한 분위기로")["color_theme"].value == "warm cream"
    assert _notes(idea="따뜻한 분위기로")["color_theme"].source == "brief"
    assert _notes(idea="파스텔 톤")["color_theme"].value == "soft pastel"
    fallback = _notes()["color_theme"]
    assert (fallback.value, fallback.source) == ("sunset orange", "default")  # 11월 = 가을
    assert "색 언급이 없어" in fallback.reason
    assert _notes(idea="여름 바다 책")["color_theme"].value == "deep blue"


def test_category_is_never_invented() -> None:
    missing = _notes()["category"]
    assert missing.value is None and missing.source == "undecided"
    assert "미정" in missing.reason
    assert _notes(idea="그림책 신간을 알린다")["category"].value == "그림책"
    assert _notes(products=["어린이 과학 전집"])["category"].value == "과학"
    assert _notes(title="에세이 기획전", idea="소설도 함께")["category"].value == "에세이"


def test_recommended_values_pass_the_scene_template_whitelist() -> None:
    briefs = [
        _brief(),
        _brief(idea="3월 그림책 파스텔"),
        _brief(idea="크리스마스 자기계발 숲"),
        _brief(title="여름 만화", idea="노을"),
    ]
    for brief in briefs:
        rec = recommend(brief, NOVEMBER, template_fields_by_kind())
        for name, value in rec.fields.items():
            assert check_field_value(name, value) == value
        assert set(rec.fields) | {n.field for n in rec.field_notes if n.value is None} == set(
            FIELDS
        )


def test_unknown_template_field_stays_undecided() -> None:
    notes = recommend_fields(_brief(), NOVEMBER, ("season", "bgm_mood"))
    assert notes[1].field == "bgm_mood" and notes[1].value is None
    assert notes[1].source == "undecided"


def test_recommendation_is_deterministic_and_marks_method() -> None:
    brief = _brief(idea="10대에게 릴스로 그림책을 알린다", format="숏폼 15초")
    first = recommend(brief, NOVEMBER, template_fields_by_kind())
    assert first == recommend(brief, NOVEMBER, template_fields_by_kind())
    assert first.method == "rule" and first.media_kind == "video"
    assert first.fields == {
        "season": "autumn",
        "color_theme": "sunset orange",
        "category": "그림책",
    }
