"""해결된 문의에 대한 고객 평가(후기): 코드가 표시를 붙이고 만족도를 센다.

규칙
- 평가는 해결된 문의에만, 문의당 한 번 남긴다. 별점(1~5)과 "문제가 해결됐는가"는 값이고,
  후기 글은 고객이 쓴 글이라 `untrusted`로만 받는다. 저장할 때 개인정보를 가린다.
- 개인정보·링크/연락 유도·비속어·광고성·반복 문자가 있는 후기는 공개하지 않고 `flagged`(확인 대기)로
  둔다. 상담원이 보고 공개하거나 숨긴다. 표시는 단어·패턴 규칙이고 LLM이 정하지 않는다.
- 낮은 별점(2점 이하)이나 "해결 안 됨" 답은 공개와 별개로 선제 연락 대상(`CS_FOLLOW_UP`)으로 알린다.
- 만족은 4~5점이다. 숫자는 전부 코드가 센 값이고, 숨긴 후기도 만족도에는 센다(공개 여부와 별개).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from support_agent import guard
from support_agent.schemas import Conversation, Feedback, FeedbackFlag

MAX_COMMENT = 300
LOW_RATING = 2  # 이 점수 이하면 선제 연락 대상
SATISFIED_FROM = 4  # 이 점수 이상이면 만족

PROFANITY = ("씨발", "시발", "ㅅㅂ", "병신", "ㅂㅅ", "좆", "개새", "미친놈", "꺼져", "죽어")
PROMOTION = (
    "광고",
    "홍보",
    "할인코드",
    "쿠폰코드",
    "구매링크",
    "오픈채팅",
    "텔레그램",
    "부업",
    "돈 벌",
)
CONTACT_WORDS = ("카톡", "카카오톡", "오픈채팅", "텔레그램", "dm 주세요", "문의 주세요")
REPEATED = re.compile(r"(.)\1{5,}")  # 같은 글자가 6번 이상

FLAG_MESSAGES = {
    "PERSONAL_INFO": "전화번호·이메일 같은 개인정보가 있어 가려서 저장했다",
    "LINK_OR_CONTACT": "외부 링크나 연락 유도 문구가 있다",
    "PROFANITY": "비속어가 있다",
    "PROMOTION": "광고성 문구가 있다",
    "REPEATED_CHARS": "같은 글자가 길게 반복된다",
    "CS_FOLLOW_UP": "낮은 별점이거나 해결되지 않았다고 답해 선제 연락이 필요하다",
}


def assess(comment: str, rating: int, resolved: bool) -> tuple[str, list[FeedbackFlag]]:
    """후기 글에 개인정보를 가리고 표시를 붙인다. 돌려주는 글은 저장해도 되는 글이다."""
    text = comment.strip()[:MAX_COMMENT]
    masked, kinds = guard.mask_pii(text)
    lowered = text.casefold()
    codes: list[str] = []
    if kinds:
        codes.append("PERSONAL_INFO")
    if guard.LINK.search(text) or any(w in lowered for w in CONTACT_WORDS):
        codes.append("LINK_OR_CONTACT")
    if any(w in lowered for w in PROFANITY):
        codes.append("PROFANITY")
    if any(w in lowered for w in PROMOTION):
        codes.append("PROMOTION")
    if REPEATED.search(text):
        codes.append("REPEATED_CHARS")
    flags = [FeedbackFlag(code=c, message=FLAG_MESSAGES[c]) for c in codes]
    if rating <= LOW_RATING or not resolved:
        flags.append(
            FeedbackFlag(code="CS_FOLLOW_UP", message=FLAG_MESSAGES["CS_FOLLOW_UP"], info=True)
        )
    return masked, flags


def initial_status(flags: list[FeedbackFlag]) -> str:
    """정보 표시(선제 연락)만 있으면 공개, 그 밖의 표시가 하나라도 있으면 확인 대기."""
    return "flagged" if any(not f.info for f in flags) else "visible"


def needs_follow_up(feedback: Feedback) -> bool:
    return any(f.code == "CS_FOLLOW_UP" for f in feedback.flags)


def overview(conversations: list[Conversation]) -> dict[str, Any]:
    """만족도 요약. 평가가 없으면 평균·비율은 None(0으로 지어내지 않는다)."""
    resolved_count = sum(1 for c in conversations if c.status == "resolved")
    rated = [c for c in conversations if c.feedback is not None]
    n = len(rated)
    ratings = [c.feedback.rating for c in rated if c.feedback]
    by_category: dict[str, list[int]] = defaultdict(list)
    for c in rated:
        if c.feedback:
            by_category[c.category or "general"].append(c.feedback.rating)

    def avg(values: list[int]) -> float | None:
        return round(sum(values) / len(values), 2) if values else None

    return {
        "resolved_inquiries": resolved_count,
        "rated": n,
        "response_rate": round(n / resolved_count, 3) if resolved_count else None,
        "average_rating": avg(ratings),
        "satisfied_rate": round(sum(1 for r in ratings if r >= SATISFIED_FROM) / n, 3)
        if n
        else None,
        "solved_rate": round(sum(1 for c in rated if c.feedback and c.feedback.resolved) / n, 3)
        if n
        else None,
        "distribution": {str(k): v for k, v in sorted(Counter(ratings).items())}
        | {str(k): 0 for k in range(1, 6) if k not in ratings},
        "by_category": {
            cat: {"count": len(v), "average": avg(v)} for cat, v in sorted(by_category.items())
        },
        "pending_review": sum(1 for c in rated if c.feedback and c.feedback.status == "flagged"),
        "hidden": sum(1 for c in rated if c.feedback and c.feedback.status == "hidden"),
        "follow_up": sum(1 for c in rated if c.feedback and needs_follow_up(c.feedback)),
    }
