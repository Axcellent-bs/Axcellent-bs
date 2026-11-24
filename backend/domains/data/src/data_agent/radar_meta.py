"""레이더 계정 메타와 의뢰 대상층 (11/24). 계산은 전부 순수 함수, 읽기만 회사 파일.

인스타 공식 API는 다른 계정의 팔로워 나이·성별을 주지 않는다. 그래서 계정마다 "주로 보는 층"
태그(kind·ages·genders·topic)를 두고, 의뢰 대상층에 맞는 계정의 게시물만 재료로 쓴다.

- 메타: 회사 파일(`tenant.ig_radar_meta`, 회사마다 따로). 사람이 고친 줄(tag_source=human)이
  먼저고 AI 추정(ai)은 `radar_tags`가 새 계정만 채운다. 메타가 없는 옛 목록은 source_kind로 kind를
  정한다(meme → meme, benchmark → industry)이고 ages·genders는 비어 있다
- 대상층: brief(target_ages "20"·"60", target_gender m·f)나 대화 질문("20대 여성")을
  ages(10s~60plus)·gender(male·female·all)로. 대상 없음·"전체"면 전체
- 맞추기: 계정 ages가 대상과 겹치고, genders가 대상 성별 또는 all. 태그가 없는 계정은 특정
  대상층에 맞지 않는다(모르면 넣지 않음). 전체 대상이면 모두 맞다
- 팔로워 하한: creator·meme 계정만, followers_count가 하한(기본 5만)보다 작으면 판정에서 뺀다
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ax_contracts.schemas.data import RadarAccountMeta

AGES = ("10s", "20s", "30s", "40s", "50s", "60plus")
KINDS = ("industry", "creator", "meme", "brand")
GENDERS = ("male", "female", "all")
AGE_WORD = {"10s": "10대", "20s": "20대", "30s": "30대", "40s": "40대", "50s": "50대",
            "60plus": "60대 이상"}  # fmt: skip
GENDER_WORD = {"male": "남성", "female": "여성", "all": "남녀"}
KIND_WORD = {"industry": "업종·경쟁", "creator": "크리에이터", "meme": "밈·코미디",
             "brand": "브랜드"}  # fmt: skip
SEARCH_WORD = {"10s": "10대", "20s": "20대", "30s": "30대", "40s": "40대", "50s": "50대",
               "60plus": "시니어"}  # fmt: skip
CREATOR_KINDS = ("creator", "meme")  # 팔로워 하한을 보는 종류
MIN_FOLLOWERS = 50_000
WIDEN_BELOW = 5  # 맞는 계정이 이보다 적으면 전체 계정으로 넓힌다
THIN_BELOW = 3  # 맞는 계정이 이보다 적은 60plus는 "인스타 표본 적음"
WIDENED = "대상층에 맞는 계정이 적어 전체 계정으로 넓혀 봤어요"


# ---------------------------------------------------------------- 대상층


def band_of(age: int | str) -> str | None:
    """나이(대) → 칸: 10 → 10s … 50 → 50s, 60·70·80 → 60plus. 모르면 None."""
    try:
        n = int(str(age).strip())
    except ValueError:
        return "60plus" if str(age).strip() in ("시니어", "노년", "어르신") else None
    if n >= 60:
        return "60plus"
    return f"{n}s" if n in (10, 20, 30, 40, 50) else None


@dataclass(frozen=True)
class Audience:
    ages: frozenset[str] = frozenset()  # 비면 모든 나이
    gender: str = "all"  # male · female · all

    @property
    def is_all(self) -> bool:
        return not self.ages and self.gender == "all"

    @property
    def label(self) -> str:
        if self.is_all:
            return "전체"
        ages = [a for a in AGES if a in self.ages]
        age = _ages_label(ages)
        who = {"male": "남성", "female": "여성"}.get(self.gender, "남녀" if ages else "")
        return " ".join(p for p in (age, who) if p) or "전체"

    @property
    def search_word(self) -> str | None:
        """쇼츠 검색에 붙일 층 말 (가장 어린 나이 칸 하나, 60plus는 "시니어")."""
        ages = [a for a in AGES if a in self.ages]
        return SEARCH_WORD[ages[0]] if ages else None


def _ages_label(ages: Sequence[str]) -> str:
    if not ages:
        return ""
    if ages == ["60plus"]:
        return "60대 이상"
    idx = [AGES.index(a) for a in ages]
    nums = [a.removesuffix("s") for a in ages if a != "60plus"]
    tail = " 이상" if "60plus" in ages else ""
    if "60plus" in ages:
        nums.append("60")
    if len(idx) >= 3 and idx == list(range(idx[0], idx[0] + len(idx))):
        return f"{nums[0]}~{nums[-1]}대{tail}"
    return "·".join(nums) + "대" + tail


def audience_from_brief(target_ages: Iterable[str], target_gender: str | None) -> Audience:
    """brief 대상(나이 "20"·"60", 성별 m·f) → 대상층. 대상 없음이면 전체."""
    ages = frozenset(b for a in target_ages if (b := band_of(a)))
    gender = {"m": "male", "f": "female"}.get(target_gender or "", "all")
    return Audience(ages, gender)


_SPAN = re.compile(r"(\d{2})(?:\s*대)?(?:\s*[·,/~∼\-]\s*(?:\d{2})(?:\s*대)?)*\s*대")
_NUM = re.compile(r"\d{2}")
_RANGE = re.compile(r"(\d{2})\s*대?\s*[~∼\-]\s*(\d{2})")
_SENIOR = re.compile(r"시니어|노년|어르신|실버")


def audience_from_text(text: str | None) -> Audience | None:
    """글 속 대상층 ("20·30대 남성", "10~30대 여성", "70대 남녀", "시니어").

    대상층 말이 없으면 None, "전체"면 전체."""
    if not text:
        return None
    ages: set[str] = set()
    for span in _SPAN.finditer(text):
        part = span.group(0)
        nums = [int(n) for n in _NUM.findall(part)]
        for lo, hi in ((int(a), int(b)) for a, b in _RANGE.findall(part)):
            nums.extend(range(lo, hi + 1, 10))
        ages |= {b for n in nums if (b := band_of(n))}
    if _SENIOR.search(text):
        ages.add("60plus")
    if re.search(r"남녀|남여|남자\s*여자|남성\s*여성", text):
        gender = "all"
    elif re.search(r"남성|남자", text):
        gender = "male"
    elif re.search(r"여성|여자", text):
        gender = "female"
    else:
        gender = None
    if not ages and gender is None:
        return Audience() if "전체" in text else None
    return Audience(frozenset(ages), gender or "all")


def matches(meta: RadarAccountMeta, audience: Audience) -> bool:
    """계정 태그가 대상층에 맞나. 전체 대상이면 모두 맞고, 태그가 없으면 특정 대상층에 안 맞는다."""
    if audience.is_all:
        return True
    if audience.ages and not set(meta.ages) & audience.ages:
        return False
    if audience.gender != "all":
        return bool({audience.gender, "all"} & set(meta.genders))
    return True


def tags_match(ages: Sequence[str], genders: Sequence[str], audience: Audience) -> bool:
    """게시물·영상에 붙은 층 태그로 맞추기 (`matches`와 같은 규칙)."""
    meta = RadarAccountMeta(account="-", ages=list(ages), genders=list(genders))  # type: ignore[arg-type]
    return matches(meta, audience)


def layer_words(ages: Sequence[str], genders: Sequence[str]) -> str | None:
    """층 태그를 말로: "20대·30대 · 여성". 태그가 없으면 None."""
    a = [AGE_WORD[x] for x in AGES if x in ages]
    g = [GENDER_WORD[x] for x in GENDERS if x in genders]
    words = " · ".join(p for p in ("·".join(a), "·".join(g)) if p)
    return words or None


# ---------------------------------------------------------------- 메타 읽기·팔로워 하한


def clean_row(account: str, row: Mapping[str, Any], default_kind: str) -> RadarAccountMeta:
    """파일 한 줄 → 메타 (모르는 값은 뺀다). kind가 없거나 틀리면 기본값."""
    kind = row.get("kind") if row.get("kind") in KINDS else default_kind
    ages = [a for a in AGES if a in (row.get("ages") or [])]
    genders = [g for g in GENDERS if g in (row.get("genders") or [])]
    topic = " ".join(str(row.get("topic") or "").split())[:20] or None
    source = row.get("tag_source") if row.get("tag_source") in ("ai", "human") else None
    followers = row.get("followers")
    followers = followers if isinstance(followers, int) and followers >= 0 else None
    return RadarAccountMeta(account=account, kind=kind, ages=ages, genders=genders,  # type: ignore[arg-type]
                            topic=topic, tag_source=source, followers=followers)  # fmt: skip


def load_meta(accounts: Sequence[str], meme_accounts: Sequence[str],
              company_id: str | None = None) -> dict[str, RadarAccountMeta]:  # fmt: skip
    """레이더 계정마다 메타. 파일에 없으면 source_kind로 kind (meme → meme, 그 밖 → industry)."""
    from ax_agent_template.tenant import ig_radar_meta

    rows = {k.lower(): v for k, v in ig_radar_meta(company_id).items()}
    memes = {a.lower() for a in meme_accounts}
    out = {}
    for account in accounts:
        default = "meme" if account.lower() in memes else "industry"
        out[account] = clean_row(account, rows.get(account.lower(), {}), default)
    return out


def excluded_reason(meta: RadarAccountMeta, followers: int | None, minimum: int) -> str | None:
    """creator·meme 계정이 팔로워 하한보다 작으면 이유. 팔로워를 모르거나 다른 종류면 None."""
    if meta.kind not in CREATOR_KINDS or followers is None or followers >= minimum:
        return None
    return f"팔로워 {_man(minimum)} 미만이라 제외 ({followers:,}명)"


def _man(n: int) -> str:
    return f"{n // 10_000}만" if n % 10_000 == 0 and n >= 10_000 else f"{n:,}명"


__all__ = [
    "AGES",
    "Audience",
    "audience_from_brief",
    "audience_from_text",
    "band_of",
    "excluded_reason",
    "layer_words",
    "load_meta",
    "matches",
    "tags_match",
]
