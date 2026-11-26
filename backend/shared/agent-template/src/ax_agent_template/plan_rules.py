"""영상 제작안 인물 기준 (기획 제작 검사·마케팅 확인이 같이 쓴다, 11/26, 표준 라이브러리만).

영상 참조 이미지는 회사 캐릭터 한 사람뿐이다(원본과 추가 참조는 같은 사람). 그래서 회사 캐릭터를
고른 제작안에서 다른 사람이 클로즈업·미디엄으로 가까이 나오면 참조 없는 얼굴이 흔들린다. 배경
군중·관객이나 넓은 샷은 괜찮다. 장면 글 낱말로 본다(LLM 아님).

도메인끼리 import하지 않아(기획 plan_checks ↔ 마케팅 plan_review) 기준을 여기 한 곳에 둔다.
"""

from __future__ import annotations

# 회사 캐릭터 말고 다른 사람을 뜻하는 낱말 / 배경 군중이라 괜찮은 낱말 / 넓은 샷 낱말
OTHER_PERSON_WORDS = (
    "친구", "동료", "다른 사람", "다른 인물", "손님", "점원", "직원", "상대", "파트너", "엄마",
    "아빠", "아이가", "두 사람", "둘이", "셋이", "세 사람", "a friend", "friends", "another person",
    "two people", "a man", "a boy", "a girl", "partner", "coworker", "customer",
)  # fmt: skip
# 11/26 E: "배경"·"background"만으로는 군중이 아니다 ("회색 배경 앞"·"background lights"처럼
# 거의 모든 장면 글에 있어 "친구와 마주 보고 대화, 배경은 카페"도 통과됐다)
# → 사람이 뒤에 있다는 말만 군중으로 본다
CROWD_WORDS = (
    "군중", "관객", "행인", "멀리", "배경의 사람들", "뒤쪽 관객", "crowd", "audience",
    "people in the background", "background crowd", "crowd in the background",
)  # fmt: skip
WIDE_WORDS = ("wide", "full shot", "long shot", "establishing", "와이드", "전신", "멀리서")


def has_word(text: str, words: tuple[str, ...]) -> bool:
    """낱말 하나라도 있나 (소문자로 비교)."""
    low = (text or "").lower()
    return any(w in low for w in words)


def close_other_person(body: str, camera: str, people: int = 1) -> bool:
    """회사 캐릭터 말고 다른 인물이 가까이(군중·와이드 아님) 나오나.

    body: 장면 visual·start_frame·end_frame. camera: camera_en과 visual(넓은 샷 낱말을 본다).
    people: 장면에 보이는 인물 수(주인공 포함). 2 이상이면 다른 인물이 있다고 본다(기획 제작안
    LLM이 사실대로 적은 칸·장면 글에서 읽은 수). 모르면 1(낱말만 본다)."""
    other = people >= 2 or has_word(body, OTHER_PERSON_WORDS)
    return other and not has_word(body, CROWD_WORDS) and not has_word(camera, WIDE_WORDS)
