"""기획안에서 미디어 종류와 장면 템플릿 필드를 추천한다. 프레임워크 무관, 순수 계산.

- 읽는 것은 보관된 기획안에 이미 있는 글(title·idea·why·note·format·body_md·근거 항목 이름·상품)과
  받은 시각뿐이다. 외부 호출·LLM이 없고 같은 기획안이면 언제 계산해도 같은 답이다.
- 판정은 아래 결정적 규칙이다(위에서부터, 먼저 맞는 것이 이긴다). 추천마다 근거 한 줄(`reason`)과
  규칙 코드(`rule`)를 남긴다.
  1. `format`이 영상(숏폼·릴스 등)·캐러셀을 말한다 → 그 종류
  2. 글에 영상 신호와 캐러셀 신호가 함께 있다 → 이미지(가장 싼 기본값), 확신 낮음(사람 확인)
  3. 글에 영상 신호(숏폼·릴스·영상·움직임 등) → video
  4. 글에 캐러셀 신호(캐러셀·카드뉴스·여러 장) → carousel
  5. 나열 신호(카탈로그·상품 나열·정보 카드 등) → 상품이 2개 이상이면 carousel, 아니면 image
  6. 10대 타깃 + SNS 채널 언급 → video(숏폼), 확신 낮음
  7. 이미지 신호(이미지·포스터·배너 등) 또는 `format`이 배너·공지 → image
  8. 아무 신호도 없다 → image(가장 싼 기본값), 확신 낮음
  "영상 없이"·"영상 대신"처럼 부정하는 말이 바로 뒤에 오면 그 신호는 세지 않는다.
- 필드는 지어내지 않는다. 기획안에 근거가 있으면 `brief`, 없으면 이유를 적은 안전한 기본값
  (`default`: 계절 = 받은 날짜의 계절, 색 = 계절 기본 색) 또는 미정(`undecided`: 책 분류)이다.
  값은 장면 템플릿 화이트리스트 검사(`prompts.check_field_value`)를 통과한 것만 쓴다.
- 여기서 정한 것은 추천일 뿐이다. 관리자가 고른 종류·필드가 있으면 그것이 우선한다
  (`brief_handoff.build_media_request`). 미디어 생성·게시는 하지 않는다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent.media.prompts import PromptRejected, check_field_value

MediaKind = Literal["image", "video", "carousel"]
KIND_KO = {"image": "이미지", "video": "영상", "carousel": "캐러셀"}
_KST = timezone(timedelta(hours=9))

_VIDEO = (
    "숏폼",
    "숏츠",
    "쇼츠",
    "릴스",
    "영상",
    "틱톡",
    "모션",
    "애니메이션",
    "움직이",
    "움직임",
    "shorts",
    "short-form",
    "shortform",
    "reels",
    "reel",
    "video",
    "tiktok",
)
_CAROUSEL = ("캐러셀", "카드뉴스", "카드 뉴스", "여러 장", "슬라이드", "carousel")
_LISTING = ("카탈로그", "상품 나열", "상품 목록", "라인업", "정보 카드", "catalog", "lineup")
_IMAGE = ("이미지", "포스터", "사진", "일러스트", "배너", "한 장", "image", "poster", "banner")
_STILL_FORMAT = ("배너", "공지", "banner", "notice")
_TEEN = ("10대", "십대", "청소년", "중고생", "중학생", "고등학생", "z세대", "teen", "teens")
_SNS = ("인스타", "틱톡", "유튜브", "sns", "소셜", "instagram", "youtube", "tiktok")
# 신호 바로 뒤에 오면 "쓰지 않는다"는 뜻인 말
_NEGATION = re.compile(
    r"\s*(?:은|는|이|가|을|를|도|으로|로)?\s*(?:없이|없는|없음|제외|말고|대신|금지|불가|빼고|아닌|아니라|안\s)"
)

_SEASON_WORDS: tuple[tuple[str, str], ...] = (
    ("spring", "spring"),
    ("summer", "summer"),
    ("autumn", "autumn"),
    ("fall", "autumn"),
    ("winter", "winter"),
    ("여름", "summer"),
    ("가을", "autumn"),
    ("겨울", "winter"),
    ("벚꽃", "spring"),
    ("신학기", "spring"),
    ("입학", "spring"),
    ("바캉스", "summer"),
    ("휴가철", "summer"),
    ("추석", "autumn"),
    ("단풍", "autumn"),
    ("크리스마스", "winter"),
    ("연말", "winter"),
    ("새해", "winter"),
)
_SPRING = re.compile(r"(?<![가-힣])봄")  # "돌봄"의 봄은 세지 않는다
_MONTH = re.compile(r"(?<![\d.])(1[0-2]|[1-9])월")
_SEASON_KO = {"spring": "봄", "summer": "여름", "autumn": "가을", "winter": "겨울"}
_COLOR_WORDS: tuple[tuple[str, str], ...] = (
    ("파스텔", "soft pastel"),
    ("pastel", "soft pastel"),
    ("노을", "sunset orange"),
    ("석양", "sunset orange"),
    ("단풍", "sunset orange"),
    ("주황", "sunset orange"),
    ("오렌지", "sunset orange"),
    ("숲", "forest green"),
    ("초록", "forest green"),
    ("녹색", "forest green"),
    ("자연", "forest green"),
    ("바다", "deep blue"),
    ("파란", "deep blue"),
    ("파랑", "deep blue"),
    ("푸른", "deep blue"),
    ("밤하늘", "deep blue"),
    ("블루", "deep blue"),
    ("따뜻", "warm cream"),
    ("포근", "warm cream"),
    ("아늑", "warm cream"),
    ("크림", "warm cream"),
)
_SEASON_COLOR = {
    "spring": "soft pastel",
    "summer": "deep blue",
    "autumn": "sunset orange",
    "winter": "warm cream",
}
# 책 분류. 긴 말을 먼저 본다(예: "그림책"이 "책"보다 먼저)
_CATEGORIES = (
    "자기계발",
    "그림책",
    "동화책",
    "에세이",
    "참고서",
    "문제집",
    "만화",
    "웹툰",
    "소설",
    "시집",
    "동화",
    "인문",
    "과학",
    "역사",
    "경제",
    "경영",
    "요리",
    "여행",
    "육아",
    "예술",
    "철학",
    "추리",
    "판타지",
    "로맨스",
    "수험서",
    "어린이책",
)


class FieldNote(AxModel):
    """필드 하나의 추천. `value`가 None이면 미정이다(사람이 정한다)."""

    field: str
    value: str | None = None
    source: Literal["brief", "default", "undecided"]
    reason: str


class MediaRecommendation(AxModel):
    """기획안에서 계산한 추천. 저장하지 않고 필요할 때 다시 계산해도 같다."""

    media_kind: MediaKind
    rule: str
    reason: str  # 화면에 보여 주는 근거 한 줄
    confidence: Literal["high", "low"]  # low면 사람이 확인하는 편이 좋다
    method: Literal["rule"] = "rule"  # LLM을 쓰지 않았다
    fields: dict[str, str] = Field(default_factory=dict)
    field_notes: list[FieldNote] = Field(default_factory=list)


def _texts(brief: CampaignBriefInput) -> list[str]:
    """신호를 찾는 글. 앞에 있을수록 먼저 본다. 근거의 값(숫자)은 읽지 않는다."""
    parts = [brief.title, brief.idea, brief.why, brief.note or "", brief.body_md or ""]
    parts.extend(f.label for f in brief.facts)
    if brief.target is not None:
        parts.append(brief.target.label)
    return [p for p in parts if p]


def _find(text: str, word: str) -> int:
    """부정되지 않은 첫 위치. 없으면 -1. 영문은 단어 경계로 찾는다."""
    lowered = text.lower()
    if word.isascii():
        pattern = re.compile(rf"(?<![a-z]){re.escape(word)}(?![a-z])")
    else:
        pattern = re.compile(re.escape(word))
    for match in pattern.finditer(lowered):
        if not _NEGATION.match(lowered, match.end()):
            return match.start()
    return -1


def _hits(texts: list[str], words: tuple[str, ...]) -> list[str]:
    """글에서 찾은 신호 낱말(중복 없이, 낱말 목록 순서)."""
    return [w for w in words if any(_find(t, w) >= 0 for t in texts)]


def _quote(words: list[str]) -> str:
    return "·".join(f'"{w}"' for w in words[:3])


def recommend_kind(brief: CampaignBriefInput) -> tuple[MediaKind, str, str, Literal["high", "low"]]:
    """(종류, 규칙 코드, 근거 한 줄, 확신)."""
    fmt = [brief.format] if brief.format else []
    texts = _texts(brief)
    fmt_quote = _quote([brief.format or ""])
    if fmt and _hits(fmt, _VIDEO):
        return "video", "format_video", f"기획안 형식이 {fmt_quote}이라 영상", "high"
    if fmt and _hits(fmt, _CAROUSEL):
        return (
            "carousel",
            "format_carousel",
            f"기획안 형식이 {fmt_quote}이라 캐러셀",
            "high",
        )
    video, carousel = _hits(texts, _VIDEO), _hits(texts, _CAROUSEL)
    if video and carousel:
        return (
            "image",
            "conflict_video_carousel",
            f"영상({_quote(video)})과 캐러셀({_quote(carousel)}) 언급이 함께 있어 정하지 못함."
            " 가장 싼 이미지 1장을 기본값으로 둠",
            "low",
        )
    if video:
        return "video", "video_keyword", f"기획안에 {_quote(video)} 언급이 있어 영상", "high"
    if carousel:
        return (
            "carousel",
            "carousel_keyword",
            f"기획안에 {_quote(carousel)} 언급이 있어 캐러셀",
            "high",
        )
    if listing := _hits(texts, _LISTING):
        if len(brief.products) >= 2:
            return (
                "carousel",
                "listing_products",
                f"기획안에 {_quote(listing)} 언급이 있고 상품이 {len(brief.products)}개라 캐러셀",
                "high",
            )
        return (
            "image",
            "listing_single",
            f"기획안에 {_quote(listing)} 언급이 있으나 나열할 상품이 2개 미만이라 이미지 1장",
            "high",
        )
    teen, channel = _hits(texts, _TEEN), _hits(texts, _SNS)
    if teen and channel:
        return (
            "video",
            "teen_shortform",
            f"10대 타깃({_quote(teen)})과 SNS 채널({_quote(channel)}) 언급이 있어 숏폼 영상",
            "low",
        )
    if image := _hits(texts, _IMAGE):
        return "image", "image_keyword", f"기획안에 {_quote(image)} 언급이 있어 이미지", "high"
    if fmt and _hits(fmt, _STILL_FORMAT):
        return (
            "image",
            "format_still",
            f"기획안 형식이 {fmt_quote}이라 이미지",
            "high",
        )
    return (
        "image",
        "default_image",
        "기획안에 영상·여러 장을 말하는 내용이 없어 가장 싼 이미지 1장을 기본값으로 둠",
        "low",
    )


def _season_of_month(month: int) -> str:
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def _season(brief: CampaignBriefInput, received_at: datetime) -> FieldNote:
    for text in _texts(brief):
        lowered = text.lower()
        candidates: list[tuple[int, str, str]] = []
        for word, season in _SEASON_WORDS:
            at = _find(text, word)
            if at >= 0:
                candidates.append((at, season, word))
        if (m := _SPRING.search(lowered)) is not None:
            candidates.append((m.start(), "spring", "봄"))
        if (m := _MONTH.search(lowered)) is not None:
            candidates.append((m.start(), _season_of_month(int(m.group(1))), m.group(0)))
        if candidates:
            best = min(candidates)
            return FieldNote(
                field="season",
                value=best[1],
                source="brief",
                reason=f'기획안의 "{best[2]}" 언급',
            )
    month = received_at.astimezone(_KST).month
    season = _season_of_month(month)
    return FieldNote(
        field="season",
        value=season,
        source="default",
        reason=f"기획안에 계절·월 언급이 없어 받은 날짜({month}월) 기준 {_SEASON_KO[season]}",
    )


def _color(brief: CampaignBriefInput, season: str | None) -> FieldNote:
    for text in _texts(brief):
        found = [(_find(text, word), color, word) for word, color in _COLOR_WORDS]
        found = [f for f in found if f[0] >= 0]
        if found:
            _, color, word = min(found)
            return FieldNote(
                field="color_theme", value=color, source="brief", reason=f'기획안의 "{word}" 언급'
            )
    if season in _SEASON_COLOR:
        return FieldNote(
            field="color_theme",
            value=_SEASON_COLOR[season],
            source="default",
            reason=f"기획안에 색 언급이 없어 {_SEASON_KO[season]} 기본 색",
        )
    return FieldNote(
        field="color_theme", source="undecided", reason="기획안에 색·계절 언급이 없어 미정"
    )


def _category(brief: CampaignBriefInput) -> FieldNote:
    texts = [*_texts(brief), *brief.products]
    for text in texts:
        found = [(at, -len(word), word) for word in _CATEGORIES if (at := _find(text, word)) >= 0]
        if found:
            word = min(found)[2]
            return FieldNote(
                field="category", value=word, source="brief", reason=f'기획안의 "{word}" 언급'
            )
    return FieldNote(
        field="category",
        source="undecided",
        reason="기획안에 책 분류 언급이 없어 미정(지어내지 않음)",
    )


def recommend_fields(
    brief: CampaignBriefInput, received_at: datetime, template_fields: tuple[str, ...] | list[str]
) -> list[FieldNote]:
    """템플릿 필드마다 추천 하나. 모르는 필드와 검사에 걸린 값은 미정으로 둔다."""
    season = _season(brief, received_at)
    known = {
        "season": season,
        "color_theme": _color(brief, season.value),
        "category": _category(brief),
    }
    notes: list[FieldNote] = []
    for name in template_fields:
        note = known.get(name)
        if note is None:
            note = FieldNote(field=name, source="undecided", reason="추천 규칙이 없는 필드라 미정")
        elif note.value is not None:
            try:
                check_field_value(name, note.value)
            except PromptRejected as error:
                note = FieldNote(
                    field=name,
                    source="undecided",
                    reason=f"기획안에서 찾은 값이 장면 규칙({error.code})에 걸려 미정",
                )
        notes.append(note)
    return notes


def recommend(
    brief: CampaignBriefInput,
    received_at: datetime,
    template_fields_by_kind: dict[str, tuple[str, ...]],
) -> MediaRecommendation:
    """종류 추천 + 그 종류 템플릿의 필드 추천."""
    kind, rule, reason, confidence = recommend_kind(brief)
    notes = recommend_fields(brief, received_at, template_fields_by_kind.get(kind, ()))
    return MediaRecommendation(
        media_kind=kind,
        rule=rule,
        reason=reason,
        confidence=confidence,
        fields={n.field: n.value for n in notes if n.value is not None},
        field_notes=notes,
    )


__all__ = [
    "KIND_KO",
    "FieldNote",
    "MediaRecommendation",
    "recommend",
    "recommend_fields",
    "recommend_kind",
]
