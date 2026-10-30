"""고객 메시지의 욕설·모욕·위협 표시: 코드가 단어·구절 규칙으로 정하고 LLM은 쓰지 않는다 (1단계).

상담원에게는 표현을 가린 `display_text`를 기본으로 보여 주고, 원문(`Message.text`)은 그대로 둔다.
클레임 판정과 분쟁에서 증거가 될 수 있어 고치거나 지우지 않는다.

- 단계: 위협(threat) > 모욕(insult) > 비속어(profanity) > 없음(none).
- "신고하겠다"·"고소"·"소비자원" 같은 정당한 권리 행사는 위협으로 보지 않는다.
- 기호로 비튼 표현("씨 발", "시1발")은 잡지 못한다. 2단계(LLM 순화)에서 보완한다.
"""

from __future__ import annotations

import re
from typing import Literal

AbuseLevel = Literal["none", "profanity", "insult", "threat"]
LEVEL_ORDER: dict[str, int] = {"none": 0, "profanity": 1, "insult": 2, "threat": 3}

PROFANITY = (
    "씨발",
    "시발",
    "ㅅㅂ",
    "ㅆㅂ",
    "병신",
    "ㅂㅅ",
    "좆",
    "개새",
    "지랄",
    "ㅈㄹ",
    "존나",
    "썅",
)
INSULT = (
    "미친놈",
    "미친년",
    "꺼져",
    "닥쳐",
    "닥치",
    "한심",
    "쓰레기",
    "죽어라",
    "뒤져",
    "등신",
    "멍청",
)
THREAT = (
    "죽여버",
    "죽이겠",
    "죽여 버",
    "가만 안",
    "가만두지",
    "가만 두지",
    "찾아가",
    "불 질러",
    "불질러",
)

# 욕설처럼 보이지만 일반 낱말인 것은 먼저 빼고 찾는다
EXCEPTIONS = ("시발점", "시발역", "시발택시", "시발 전", "쓰레기통", "쓰레기봉투", "분리수거")

APOLOGY = "불편을 드려 죄송합니다."  # 순화문 맨 앞에 코드가 붙이는 한 문장 (미리보기에서는 뺀다)
LABELS: dict[str, str] = {"profanity": "[비속어]", "insult": "[모욕 표현]", "threat": "[위협 표현]"}

MESSAGES: dict[AbuseLevel, str] = {
    "none": "",
    "profanity": "비속어가 있어 가려서 보여 준다",
    "insult": "모욕 표현이 있어 가려서 보여 준다",
    "threat": "위협 표현이 있다 (가려서 보여 주고, 담당자 확인을 권한다)",
}


def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)))


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("threat", _pattern(THREAT)),
    ("insult", _pattern(INSULT)),
    ("profanity", _pattern(PROFANITY)),
)
_EXCEPTION = _pattern(EXCEPTIONS)
# 글자 사이에 숫자·기호를 끼운 표기("시1발", "씨.발"). 띄어쓰기는 "김씨 발송" 같은 낱말과 섞여 뺀다
_GAP = r"[^\s가-힣A-Za-z]{1,2}"
_EVASIVE = re.compile(
    "|".join(
        _GAP.join(re.escape(c) for c in word)
        for word in ("씨발", "시발", "병신", "ㅅㅂ", "ㅂㅅ", "지랄", "존나")
    )
)


def assess(text: str) -> tuple[str, AbuseLevel, list[str]]:
    """(가린 글, 가장 높은 단계, 찾은 종류 목록)을 돌려준다. 찾은 낱말 자체는 담지 않는다."""
    protected: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    shown = _EXCEPTION.sub(_hold, text)
    level: AbuseLevel = "none"
    kinds: list[str] = []
    shown = _EVASIVE.sub(LABELS["profanity"], shown) if _EVASIVE.search(shown) else shown
    if LABELS["profanity"] in shown and "profanity" not in kinds:
        kinds.append("profanity")
        if LEVEL_ORDER["profanity"] > LEVEL_ORDER[level]:
            level = "profanity"
    for kind, pattern in _PATTERNS:
        if pattern.search(shown):
            kinds.append(kind)
            shown = pattern.sub(LABELS[kind], shown)
            if LEVEL_ORDER[kind] > LEVEL_ORDER[level]:
                level = kind  # type: ignore[assignment]
    for index, original in enumerate(protected):
        shown = shown.replace(f"\x00{index}\x00", original)
    return shown, level, kinds
