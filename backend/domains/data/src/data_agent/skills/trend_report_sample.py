"""분석 의뢰 표본 (11/26 D): 계정 언어 · 대상 맞음 · 사람 확인 · 인스타 상태. 코드 계산, 호출 없음.

- 계정 언어: 레이더 저장분(`radar_latest`)과 이번 보고서가 읽은 계정의 최근 캡션(최대 12개)에서
  한글 비율 = 한글 글자 ÷ 글자(한글·영문·그 밖 문자, 해시태그 포함, 숫자·기호 제외). 글자가
  `MIN_LETTERS`보다 적으면 모름. `KOREAN_MIN` 이상이면 한국어 계정
- 대상 맞음: 한국어 계정이면서 레이더 계정 메타의 대상층 태그가 의뢰 대상층과 맞음
  (`radar_meta.matches`). 대상층이 전체면 한국어만. 메타가 없는 계정은 대상층을 몰라 대상
  맞음에 넣지 않는다
- 사람 확인: 계정 메타 `tag_source == "human"` 수 (지금 0이면 0)
- 대상층이 한국(의뢰문·제품 이름에 한글)이면 콘텐츠 근거에서 한국어 계정 게시물을 앞으로(빼지 않음)
- 계정 추가는 코드로 하지 않는다 (사람이 레이더에 넣는다)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ax_contracts.schemas.data import (
    InstagramRadarPost,
    RadarAccountMeta,
    TrendReportBrief,
    TrendReportSample,
)
from data_agent import radar_meta as rm

KOREAN_MIN = 0.5  # 한글 비율이 이 이상이면 한국어 계정
MIN_LETTERS = 20  # 글자가 이보다 적으면 언어를 모른다
RECENT = 12  # 계정마다 볼 최근 캡션 수


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ"


def hangul_ratio(captions: Iterable[str]) -> float | None:
    """한글 글자 ÷ 글자 (숫자·기호·공백 제외). 글자가 적으면 None."""
    hangul = letters = 0
    for text in captions:
        for ch in text or "":
            if ch.isalpha():
                letters += 1
                hangul += _is_hangul(ch)
    if letters < MIN_LETTERS:
        return None
    return round(hangul / letters, 3)


def account_languages(*media: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, float | None]:
    """계정 → 한글 비율 (저장분 여럿을 합침, 먼저 준 쪽이 우선). 최근 캡션 RECENT개."""
    out: dict[str, float | None] = {}
    for source in media:
        for account, items in source.items():
            if out.get(account) is not None:
                continue
            ordered = sorted(items, key=lambda m: str(m.get("timestamp") or ""), reverse=True)
            caps = [str(m.get("caption") or "") for m in ordered[:RECENT]]
            out[account] = hangul_ratio(caps)
    return out


def is_korean(ratio: float | None) -> bool:
    return ratio is not None and ratio >= KOREAN_MIN


def korea_target(brief: TrendReportBrief, request_text: str | None) -> bool:
    """대상층이 한국: 의뢰문 또는 제품·주제 이름에 한글이 있다."""
    text = " ".join(x for x in (request_text, brief.product, brief.theme) if x)
    return any(_is_hangul(ch) for ch in text)


def metas_of(stored_radar: Any) -> list[RadarAccountMeta]:
    output = stored_radar[1] if isinstance(stored_radar, list | tuple) and len(
        stored_radar) > 1 else stored_radar  # fmt: skip
    rows = output.get("accounts_meta") if isinstance(output, Mapping) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        try:
            out.append(RadarAccountMeta.model_validate(row))
        except ValueError:
            continue
    return out


def sample(brief: TrendReportBrief, languages: Mapping[str, float | None],
           metas: Sequence[RadarAccountMeta], *, korean_first: bool,
           connected: bool | None, read: int | None) -> TrendReportSample:  # fmt: skip
    audience = rm.audience_from_brief(brief.target_ages, brief.target_gender)
    by_account = {m.account: m for m in metas}
    accounts = set(languages) | set(by_account)
    korean = {a for a in accounts if is_korean(languages.get(a))}

    def fits(account: str) -> bool:
        meta = by_account.get(account)
        if meta is None:
            return audience.is_all
        return not meta.excluded_reason and rm.matches(meta, audience)

    return TrendReportSample(
        total=len(accounts), matched=sum(1 for a in korean if fits(a)),
        human_checked=sum(1 for m in metas if m.tag_source == "human"), korean=len(korean),
        korean_first=korean_first, instagram_connected=connected, instagram_read=read,
    )  # fmt: skip


def korean_first(posts: list[InstagramRadarPost],
                 languages: Mapping[str, float | None]) -> list[InstagramRadarPost]:  # fmt: skip
    """한국어 계정 게시물을 앞으로 (안정 정렬: 그 안의 순서는 그대로, 빼지 않는다)."""
    return sorted(posts, key=lambda p: not is_korean(languages.get(p.account)))
