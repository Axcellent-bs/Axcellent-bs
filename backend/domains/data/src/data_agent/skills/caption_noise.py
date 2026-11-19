"""캡션 단위 중 묶음·집계에 의미 없는 것 (순수 함수).

트렌드 보고서 해시태그 섹션·인스타 레이더 묶음이 같이 쓴다.

단위 키는 trend_memes `units_of` 모양("hashtag:광고"·"phrase:수 있습니다", 정규화 뒤 소문자).

- 표기용 태그: 광고·협찬·제작지원·event·ad·sponsored·giveaway 등 (`HIDDEN_TAGS`, 공백 없이 비교)
- 이벤트 상투어: 당첨자 발표·참여 방법·추첨을 통해·지금 바로·댓글로·팔로우하고·이벤트 기간이 든 단위
- 흔한 말: 서술어로 끝나는 2어절 구("수 있습니다"), 영어 불용어만으로 된 구("of the")
"""

from __future__ import annotations

# 표기용 태그 (광고 표시·이벤트 표기). 2026-11-19 보고서 tr_4913cb6a11274238에서 #광고가 1위였다
HIDDEN_TAGS = frozenset({
    "광고", "유료광고", "광고포함", "협찬", "제작지원", "체험단", "이벤트", "경품", "event", "ad",
    "ads", "sponsored", "giveaway", "collab", "partnership", "pr",
})  # fmt: skip
# 이벤트 상투어 (공백 없이 단위 안에 들었으면 뺀다)
EVENT_PHRASES = (
    "당첨자발표", "참여방법", "추첨을통해", "지금바로", "댓글로", "팔로우하고", "이벤트기간",
)  # fmt: skip
# 서술어 끝 (2어절 구의 마지막 어절이 이것으로 끝나면 흔한 말)
PREDICATE_ENDS = (
    "습니다", "합니다", "입니다", "니다", "해요", "어요", "아요", "세요", "예요", "이에요",
    "한다", "된다", "있다", "없다", "했다", "였다",
)  # fmt: skip
EN_STOPWORDS = frozenset({
    "a", "an", "the", "of", "in", "to", "for", "on", "at", "by", "with", "from", "and", "or",
    "you", "your", "i", "me", "my", "we", "our", "it", "its", "is", "are", "be", "this",
    "that", "so", "all", "just", "as", "up", "out", "about", "into", "not", "no",
})  # fmt: skip


def _value(key: str) -> tuple[str, str]:
    kind, _, value = key.partition(":")
    return kind, value


def hidden_tag(key: str) -> bool:
    """표기용 태그인지 (해시태그만이 아니라 같은 말인 구도 본다: "광고 포함")."""
    _, value = _value(key)
    return value.replace(" ", "") in HIDDEN_TAGS


def event_phrase(key: str) -> bool:
    _, value = _value(key)
    flat = value.replace(" ", "")
    return any(p in flat for p in EVENT_PHRASES)


def common_phrase(key: str) -> bool:
    """흔한 말: 서술어("있습니다"·"합니다")가 든 구, 영어 불용어 2어절("of the")이 든 구.

    캡션 n-gram은 문장 경계를 넘어 붙으므로("있습니다 당첨자"·"있습니다 of") 2어절만이 아니라
    서술어·불용어 쌍을 품은 3~4어절 구도 뺀다. 짧은 진짜 구("비건 선크림")는 따로 남는다.
    """
    kind, value = _value(key)
    if kind != "phrase":
        return False
    words = value.split()
    if len(words) < 2:
        return not words
    if any(w.endswith(PREDICATE_ENDS) for w in words):
        return True
    return any(a in EN_STOPWORDS and b in EN_STOPWORDS
               for a, b in zip(words, words[1:], strict=False))  # fmt: skip


def noise(key: str) -> bool:
    """묶음 씨앗·해시태그 집계에서 뺄 단위인지."""
    return hidden_tag(key) or event_phrase(key) or common_phrase(key)
