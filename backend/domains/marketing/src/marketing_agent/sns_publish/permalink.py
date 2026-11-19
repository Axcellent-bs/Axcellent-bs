"""Instagram 게시물 링크(permalink) 정규화. 수동 게시 완료 입력의 형식 검사에 쓴다.

받는 모양은 `https://www.instagram.com/p/<코드>/`, `/reel/<코드>/`, `/tv/<코드>/`뿐이다.
- 도메인은 `instagram.com`·`www.instagram.com`만(다른 도메인·하위 도메인·포트·사용자 정보는 거절).
- `http`는 `https`로, 쿼리(`?igsh=...`)·프래그먼트는 버리고, 결과는 항상 `www`와 끝 `/`를 붙인
  한 가지 모양이다.
- 코드는 영숫자·`_`·`-`(5~64자). 대소문자는 그대로 둔다(코드는 대소문자를 가린다).

**링크가 실제로 있는 게시물인지, 우리 계정의 것인지는 확인하지 못한다.** 형식만 본다
(Instagram에 요청을 보내지 않는다). 정규화된 값이 같으면 같은 게시물로 보아 중복을 막는다.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_HOSTS = frozenset({"instagram.com", "www.instagram.com"})
_KINDS = frozenset({"p", "reel", "tv"})
_CODE = re.compile(r"[A-Za-z0-9_-]{5,64}")
MAX_RAW = 500


def normalize_permalink(raw: object) -> str | None:
    """정규화된 링크, 형식이 맞지 않으면 None."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > MAX_RAW or any(ch.isspace() or not ch.isprintable() for ch in text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or port is not None:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if (parts.hostname or "").lower() not in _HOSTS:
        return None
    segments = parts.path.split("/")
    if segments and segments[-1] == "":  # 끝의 `/` 하나만 허용
        segments = segments[:-1]
    if len(segments) != 3 or segments[0] != "":
        return None
    kind, code = segments[1].lower(), segments[2]
    if kind not in _KINDS or not _CODE.fullmatch(code):
        return None
    return f"https://www.instagram.com/{kind}/{code}/"
