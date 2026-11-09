"""캠페인 링크 꼬리표 (skill `build_campaign_link`). 프레임워크에 의존하지 않는 순수 함수다.

- 캠페인으로 들어온 방문을 나중에 캠페인별로 세려고, 링크에 UTM 꼬리표 3개를 붙인다
  (`utm_campaign`·`utm_source`·`utm_medium`).
- `utm_campaign`은 받은 기획안의 `campaign_id`(`cmp_` + `proposal_id`)를 그대로 쓴다.
  `briefs.campaign_id_for`와 같은 문자 집합이라, 기획안에서 나온 id는 그대로 통과한다.
- `source`·`medium`은 닫힌 허용 목록이고, 어느 source가 어느 medium과 맞는지는 조합표로 검증한다.
- 꼬리표 값은 허용 목록과 id 형식에서만 나온다. 사용자·구매자 정보나 토큰을 값으로 만들지 않는다.
  그래서 `base_url`에 사용자 정보(`user:pw@`)가 들어 있으면 거절한다.
- 상태를 바꾸지 않는다(저장·발행 없음).
"""

from __future__ import annotations

import re
from types import MappingProxyType
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from ax_contracts.schemas.common import AxModel

CAMPAIGN_ID_PATTERN = r"^cmp_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
_CAMPAIGN_ID = re.compile(CAMPAIGN_ID_PATTERN)

ALLOWED_SOURCES = ("banner", "notice", "instagram", "google", "youtube")
ALLOWED_MEDIUMS = ("onsite", "social", "cpc")

# source → 맞는 medium. 쇼핑몰 안(배너·공지)은 onsite, 인스타는 social,
# 구글 광고는 cpc, 유튜브는 일반 게시(social)와 광고(cpc) 둘 다 있다.
SOURCE_MEDIUMS = MappingProxyType(
    {
        "banner": ("onsite",),
        "notice": ("onsite",),
        "instagram": ("social",),
        "google": ("cpc",),
        "youtube": ("social", "cpc"),
    }
)

UTM_PARAMS = ("utm_source", "utm_medium", "utm_campaign")  # 우리가 정하고 덮어쓰는 키
BASE_URL_MAX_LENGTH = 2048
_SCHEMES = ("http", "https")
_URL_SAFE = "/%:@!$&'()*+,;=~-._"  # 이미 있는 %xx는 다시 인코딩하지 않는다


class BuildCampaignLinkInput(AxModel):
    base_url: str
    campaign_id: str
    source: str
    medium: str


class CampaignLink(AxModel):
    url: str
    utm_campaign: str
    utm_source: str
    utm_medium: str


def build_campaign_link(base_url: str, campaign_id: str, source: str, medium: str) -> str:
    """`base_url`에 UTM 꼬리표를 붙인 링크를 돌려준다. 입력이 어긋나면 `ValueError`.

    - 기존 쿼리는 순서대로 보존하고, `utm_source`·`utm_medium`·`utm_campaign`이 이미 있으면
      새 값으로 덮어쓴다(키 대소문자 무시). `utm_term` 같은 다른 `utm_*`는 건드리지 않는다.
    - 프래그먼트(`#...`)는 그대로 둔다. 한글·공백은 표준 라이브러리로 퍼센트 인코딩한다.
    """
    if not isinstance(campaign_id, str) or not _CAMPAIGN_ID.fullmatch(campaign_id):
        raise ValueError(f"campaign_id must match {CAMPAIGN_ID_PATTERN}")
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"source must be one of {', '.join(ALLOWED_SOURCES)}")
    if medium not in ALLOWED_MEDIUMS:
        raise ValueError(f"medium must be one of {', '.join(ALLOWED_MEDIUMS)}")
    if medium not in SOURCE_MEDIUMS[source]:
        raise ValueError(
            f"medium {medium!r} does not fit source {source!r} "
            f"(allowed: {', '.join(SOURCE_MEDIUMS[source])})"
        )

    parts = _split_base_url(base_url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in UTM_PARAMS
    ]
    tags = [("utm_source", source), ("utm_medium", medium), ("utm_campaign", campaign_id)]
    query = urlencode(kept + tags, quote_via=quote)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe=_URL_SAFE),
            query,
            quote(parts.fragment, safe=_URL_SAFE + "?"),
        )
    )


def _split_base_url(base_url: str):
    if not isinstance(base_url, str) or not base_url or base_url != base_url.strip():
        raise ValueError("base_url must be a non-empty URL without surrounding whitespace")
    if len(base_url) > BASE_URL_MAX_LENGTH:
        raise ValueError(f"base_url must be at most {BASE_URL_MAX_LENGTH} characters")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in base_url):
        raise ValueError("base_url must not contain control characters")
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
        parts.port  # noqa: B018 — 잘못된 포트는 여기서 ValueError
    except ValueError as error:
        raise ValueError("base_url is not a valid URL") from error
    if parts.scheme.lower() not in _SCHEMES:
        raise ValueError("base_url scheme must be http or https")
    if not host:
        raise ValueError("base_url must include a host")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("base_url must not contain user information")
    return parts._replace(scheme=parts.scheme.lower())
