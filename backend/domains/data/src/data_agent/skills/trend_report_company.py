"""분석 의뢰 brief의 빈칸을 지금 연결된 회사로 채운다 (값 우선순위: 폼 > 의뢰문 > 프리셋 > 회사).

회사 이름·업종을 코드나 프리셋에 박지 않는다. 연결된 회사(tenant)의 저장된 domain_profile·판매
집단 순위(segment_favorites)·키워드 풀·확인된 인스타 계정(company_accounts)만 쓴다. 회사 연결을
바꾸면 다음 의뢰부터 그 회사로 채운다. 숫자·계정은 지어내지 않는다.

- product: 프로필 회사 이름, 업종이 있으면 "회사 이름 (업종)"
- format: 의뢰문에 영상·광고·홍보·릴스·숏츠가 있으면 "릴스 30초", 없으면 `[report] default_format`
  (회사 trends.toml, 기본 "릴스")
- keywords(최대 5): ① 타깃 성별·나이의 우리 쇼핑몰 장르·분류 순위(최근 90일, 모자라면 건너뜀)
  ② 프로필 씨앗 키워드 ③ 키워드 풀 최신 날 검색량 큰 순. 겹치는 것은 뺀다
- shopping_category: 프로필 업종 → 쇼핑인사이트 분야 짝(`INDUSTRY_SHOPPING`), 짝이 없으면 비움
- 타깃 나이에 10대 미만이 없으면 어린이 낱말(`[report] child_words`)이 든 회사 키워드는 뺀다
- competitors: 확인된 계정 팔로워 큰 순 최대 7, 섹션 2·3 계정 목록도 같은 저장분
- 확인된 계정이 `[report.accounts] min_accounts`(6)보다 적으면 레이더 업종 표시
  (`[instagram.radar_industries]`)가 회사 업종과 같은 이미 읽은 레이더 계정을 더한다. 표시가 없으면
  건너뛴다. 그래도 모자라면 한계 문장
- 회사 연결이 없거나 프로필이 unknown이면 채우지 않고 이유만
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ax_agent_template.tenant import setting as tenant_setting
from data_agent.keyword_pool import compact

MAX_KEYWORDS = 5
MAX_COMPETITORS = 7
VIDEO_WORDS = ("영상", "광고", "홍보", "릴스", "숏츠")
VIDEO_FORMAT = "릴스 30초"
DEFAULT_FORMAT = "릴스"
TREND_CONFIG_ENV = "DATA_TREND_CONFIG"
NO_COMPANY = "연결된 회사 프로필이 없어 회사 값으로 채우지 않음"


@dataclass
class CompanyContext:
    """회사로 채울 값 (비어 있으면 채우지 않는다)과 한계 문장."""

    product: str | None = None
    format: str | None = None
    keywords: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)  # 섹션 2·3 계정 (확인된 것 전부)
    names: dict[str, str] = field(default_factory=dict)  # 계정 → 표시 이름
    shopping_category: str | None = None
    limits: list[str] = field(default_factory=list)


def default_format(env: Mapping[str, str]) -> str:
    """회사 trends.toml `[report] default_format` (없으면 "릴스")."""
    path = env.get(TREND_CONFIG_ENV) or tenant_setting(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return DEFAULT_FORMAT
    try:
        with Path(path).open("rb") as f:
            raw = (tomllib.load(f).get("report") or {}).get("default_format")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return DEFAULT_FORMAT
    return str(raw).strip() if isinstance(raw, str) and raw.strip() else DEFAULT_FORMAT


def format_of(request_text: str | None, env: Mapping[str, str]) -> str:
    text = request_text or ""
    return VIDEO_FORMAT if any(w in text for w in VIDEO_WORDS) else default_format(env)


def product_of(profile: Any) -> str | None:
    if not profile or not profile.tenant:
        return None
    return f"{profile.tenant} ({profile.industry})" if profile.industry else str(profile.tenant)


# 프로필 업종(domain_profile.INDUSTRIES) → 네이버 쇼핑인사이트 분야 이름(naver_shopping.
# CATEGORY_CANDIDATES). 짝이 없는 업종은 None (쇼핑 칸 건너뜀). INDUSTRIES 값은 전부 여기 있어야
# 한다(테스트가 막는다)
INDUSTRY_SHOPPING: dict[str, str | None] = {
    "출판·교육": "도서",
    "패션": "패션의류",
    "식품": "식품",
    "뷰티": "화장품/미용",
    "디지털·가전": "디지털/가전",
    "생활·리빙": "생활/건강",
    "스포츠·레저": "스포츠/레저",
    "유아·출산": "출산/육아",
    "여행·숙박": "여가/생활편의",  # 쇼핑인사이트 여행 하위 분야는 다음 단계
    "여가·취미": "여가/생활편의",
    "반려동물": None,
    "기타": None,
}
# 타깃에 10대 미만이 없으면 빼는 회사 키워드 낱말 (회사 trends.toml `[report] child_words`)
CHILD_WORDS = ("어린이", "유아", "아동", "초등", "키즈", "영유아", "그림책")
UNDER_TEN = "0"  # 사람 나이 구간 "0" = 10대 미만


def shopping_category_of(industry: str | None) -> str | None:
    """프로필 업종 → 쇼핑인사이트 분야 cid (`INDUSTRY_SHOPPING` 짝, 업종이 분야 이름과 같아도 됨).

    짝이 없으면 None (쇼핑 칸은 건너뜀)."""
    from data_agent.connectors.naver_shopping import CATEGORY_CANDIDATES

    if not industry:
        return None
    name = INDUSTRY_SHOPPING.get(industry, industry)
    return next((cid for n, cid in CATEGORY_CANDIDATES if name and n == name), None)


def child_words(env: Mapping[str, str]) -> tuple[str, ...]:
    """회사 trends.toml `[report] child_words` (없으면 기본 목록)."""
    path = env.get(TREND_CONFIG_ENV) or tenant_setting(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return CHILD_WORDS
    try:
        with Path(path).open("rb") as f:
            raw = (tomllib.load(f).get("report") or {}).get("child_words")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return CHILD_WORDS
    if not isinstance(raw, list):
        return CHILD_WORDS
    words = tuple(w.strip() for w in raw if isinstance(w, str) and w.strip())
    return words or CHILD_WORDS


def merge_keywords(*groups: Sequence[str], limit: int = MAX_KEYWORDS,
                   blocked: Sequence[str] = (), dropped: list[str] | None = None,
                   ) -> list[str]:  # fmt: skip
    """순서대로 채우고 같은 말(공백·대소문자 무시)은 한 번만. 30자 넘는 말은 뺀다(칸 규칙).

    `blocked` 낱말이 든 말은 빼고(뺀 말은 `dropped`에) 다음 순위로 채운다."""
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for word in group:
            word = " ".join(str(word).split())
            key = compact(word)
            if not key or key in seen or len(word) > 30:
                continue
            seen.add(key)
            if any(b in word for b in blocked):
                if dropped is not None:
                    dropped.append(word)
                continue
            out.append(word)
            if len(out) >= limit:
                return out
    return out


def build_context(profile: Any, *, favorites: Sequence[str], favorites_reason: str | None,
                  pool: Sequence[str], accounts: Sequence[Mapping[str, Any]],
                  accounts_reason: str | None, request_text: str | None,
                  env: Mapping[str, str], ages: Sequence[str] = ()) -> CompanyContext:  # fmt: skip
    """회사 정보 → 채울 값. 프로필이 없거나 unknown이면 값 없이 이유만.

    타깃 나이(`ages`)가 있는데 10대 미만이 없으면 어린이 낱말이 든 회사 키워드는 뺀다(의뢰문·폼·
    프리셋 키워드는 여기 오지 않으니 건드리지 않는다)."""
    out = CompanyContext()
    if profile is None or getattr(profile, "status", "unknown") != "known":
        why = getattr(profile, "reason", None) if profile is not None else None
        out.limits.append(NO_COMPANY + (f" ({why})" if why else ""))
        return out
    out.product = product_of(profile)
    out.format = format_of(request_text, env)
    if favorites_reason:
        out.limits.append(f"회사 키워드: 타깃 집단 순위를 건너뜀 ({favorites_reason})")
    seeds = [s.keyword for s in profile.seed_keywords]
    blocked = child_words(env) if ages and UNDER_TEN not in ages else ()
    dropped: list[str] = []
    out.keywords = merge_keywords(favorites, seeds, pool, blocked=blocked, dropped=dropped)
    if dropped:
        out.limits.append(f"타깃과 맞지 않아 뺀 키워드: {', '.join(dropped[:5])}")
    out.shopping_category = shopping_category_of(profile.industry)
    if out.shopping_category is None:
        out.limits.append(f"회사 업종({profile.industry})에 맞는 쇼핑인사이트 분야가 없어"
                          " 쇼핑 칸은 건너뜀")  # fmt: skip
    out.accounts = [str(a["account"]) for a in accounts]
    out.competitors = out.accounts[:MAX_COMPETITORS]
    out.names = {str(a["account"]): str(a["name"]) for a in accounts if a.get("name")}
    if accounts_reason:
        out.limits.append(accounts_reason)
    elif not accounts:
        out.limits.append("회사 경쟁사: 공식 API로 확인된 인스타 계정이 없음")
    return out


async def gather(brief: Any, request_text: str | None, note: Any, *, env: Mapping[str, str],
                 profile_fn: Any, favorites_fn: Any, pool_fn: Any,
                 accounts_fn: Any) -> tuple[CompanyContext, int, int]:  # fmt: skip
    """회사 정보를 모아 채울 값을 만든다. (값, LLM 호출 수, 인스타 호출 수).

    - `profile_fn()`: 저장된 domain_profile (해시 재사용, 없으면 None)
    - `favorites_fn(brief)`: (타깃 집단 장르·분류 이름들, 건너뛴 이유)
    - `pool_fn()`: 키워드 풀 최신 날 검색량 큰 순 이름들
    - `accounts_fn(profile, note)`: company_accounts.FoundAccounts (확인 전이면 찾는다)
    """
    profile = await profile_fn()
    if profile is None or getattr(profile, "status", "unknown") != "known":
        empty = build_context(profile, favorites=[], favorites_reason=None, pool=[], accounts=[],
                              accounts_reason=None, request_text=request_text, env=env)  # fmt: skip
        return empty, 0, 0
    favorites, why = await favorites_fn(brief)
    pool = await pool_fn()
    found = await accounts_fn(profile, note)
    from data_agent.skills import company_accounts as ca

    accounts = ca.apply_overrides(found, env)
    # 목표(min_accounts)보다 적으면 같은 업종 표시가 있는 레이더 계정(이미 읽은 것)을 더한다
    need = ca.min_accounts(env)
    _, exclude = ca.overrides(env)
    extra = [a for a in ca.radar_fill(accounts, profile.industry, env, need)
             if a["account"] not in exclude]  # fmt: skip
    accounts = [*accounts, *extra]
    ctx = build_context(profile, favorites=favorites, favorites_reason=why, pool=pool,
                        accounts=accounts, accounts_reason=found.deferred,
                        request_text=request_text, env=env,
                        ages=list(getattr(brief, "target_ages", []) or []))  # fmt: skip
    if extra:
        ctx.limits.append(f"확인된 계정이 목표({need}곳)보다 적어 같은 업종 표시가 있는 인스타"
                          f" 레이더 계정 {len(extra)}곳을 더함 (이미 읽은 것)")  # fmt: skip
    if short := ca.short_note(len(accounts), need):
        ctx.limits.append(short)
    return ctx, found.llm_calls if not found.reused else 0, found.ig_calls


def target_filters(attributes: Sequence[Any], gender: str | None,
                   ages: Sequence[str]) -> list[dict[str, str]]:  # fmt: skip
    """brief 타깃 → 판매 집단 필터 (카탈로그 속성 값 이름으로 맞춘다, 회사마다 코드가 달라도 됨).

    나이 구간마다 하나 ({성별 키: 코드, 나이 키: 코드}). 맞는 값이 없으면 그 조건은 뺀다."""
    from data_agent.skills.trend_report_brief import GENDER_WORD

    def code_of(label: str, exact: bool = True) -> tuple[str, str] | None:
        for a in attributes:
            for code, name in (a.values or {}).items():
                if (name == label) if exact else str(name).startswith(label):
                    return a.key, code
        return None

    base: dict[str, str] = {}
    if gender and (hit := code_of(GENDER_WORD.get(gender, ""))):
        base[hit[0]] = hit[1]
    out = []
    for age in ages:
        if hit := code_of(f"{age}대", exact=False):
            out.append({**base, hit[0]: hit[1]})
    if not out and base:
        out.append(base)
    return out
