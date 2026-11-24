"""광고 근거에서 뺄 욕설·성인 표현 (11/24, TR-0E4C·TR-D262 "fuckjerry" 계정이 광고 근거로 올라옴).

단어 목록은 여기 한 곳에만 둔다. 데이터 AI(보고서 근거)와 대시보드(레이더 계정 표)가 같이 쓴다.
회사 설정으로 더할 수 있다: `trends.toml` `[instagram] blocked_words = ["..."]` (선택, 더하기만).

- 부분 일치: 대소문자를 무시하고, 공백·`.`·`_`·`-`를 지운 뒤 찾는다 ("fuckjerry", "F.U.C.K" 모두)
- 계정 이름·소개·캡션 앞부분처럼 짧은 글에만 쓴다 (긴 본문 검사용이 아니다)
- 화면·로그에는 단어를 그대로 쓰지 않고 `MARK`("광고 근거 제외: 표현")만 쓴다
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

MARK = "광고 근거 제외: 표현"

# 기본 욕설·성인 표현 (소수). 다른 말의 일부로 자주 나오는 짧은 말(예: sex → sussex)은 넣지 않는다
BLOCKED_WORDS: tuple[str, ...] = (
    "fuck", "shit", "bitch", "porn", "nsfw", "onlyfans", "horny", "xxx",
    "씨발", "시발", "좆", "개새끼", "병신", "섹스", "야동", "19금",
)  # fmt: skip

_SEP = re.compile(r"[\s._\-]+")


def _plain(text: str) -> str:
    return _SEP.sub("", text).casefold()


def words_from(raw: Mapping[str, Any] | None) -> tuple[str, ...]:
    """`[instagram]` 표에서 회사가 더한 단어 (`blocked_words`, 문자열 목록만)."""
    listed = (raw or {}).get("blocked_words", [])
    if not isinstance(listed, list):
        return ()
    return tuple(dict.fromkeys(w for x in listed if (w := _plain(str(x)))))


def blocked_word(texts: Iterable[str | None], extra: Iterable[str] = ()) -> str | None:
    """글 중 하나라도 욕설·성인 표현이 들었으면 그 단어(정리한 형태), 아니면 None."""
    words = [p for w in (*BLOCKED_WORDS, *extra) if (p := _plain(w))]
    for text in texts:
        plain = _plain(text or "")
        if plain and (hit := next((w for w in words if w in plain), None)):
            return hit
    return None


def mark(texts: Iterable[str | None], extra: Iterable[str] = ()) -> str | None:
    """걸리면 `MARK`, 아니면 None (단어는 밖으로 내지 않는다)."""
    return MARK if blocked_word(texts, extra) else None


__all__ = ["BLOCKED_WORDS", "MARK", "blocked_word", "mark", "words_from"]
