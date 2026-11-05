"""광고 재료 판정 한 곳 (예시 아이디어·인사이트 브리프·급상승 판정이 같이 쓴다). 코드 규칙만.

- 분류(공유 분류기 `topic_category`)가 없거나 unknown → 모름(None) + "분류 대기"
- 뉴스·사건·기부·재난·정치·사회·인물 분류 → 아님 (설정과 관계없이 늘)
- 주제 이름 자체가 실존 사람 이름 → 아님 (초상권)
- 회사 "추천 안 함" 분류(`[recommend] exclude_categories`) → 아님
- 회사 광고 재료 분류(`[promo] trend_categories`) 밖 → 아님
- 실존 인물·팀·리그·상표가 들었으면 → 가능 + "분위기·형식만 빌릴 것"
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

TREND_CONFIG_ENV = "DATA_TREND_CONFIG"  # trend_scan과 같은 이름 (회사 trends.toml)
# 광고 재료에서 늘 빼는 주제 분류
NEVER_TREND = frozenset({"뉴스·사건", "기부·재난", "정치·사회", "인물"})
DEFAULT_ALLOWED: tuple[str, ...] = (
    "게임", "연예·스포츠", "K-pop·음악", "IT", "유머", "드라마", "교양",
)  # fmt: skip
DEFAULT_EXCLUDE: tuple[str, ...] = ("뉴스·사건", "정치·사회", "기부·재난", "인물")
PENDING = "분류 대기"


@dataclass(frozen=True)
class AdRules:
    allowed: tuple[str, ...] = DEFAULT_ALLOWED
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE


def load_rules(env: Mapping[str, str]) -> AdRules:
    """회사 `trends.toml`의 `[promo] trend_categories`·`[recommend] exclude_categories`."""
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return AdRules()
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f)
        promo = raw.get("promo") or {}
        rec = raw.get("recommend") or {}
        allowed = tuple(str(c) for c in promo.get("trend_categories", DEFAULT_ALLOWED))
        exclude = tuple(str(c) for c in rec.get("exclude_categories", DEFAULT_EXCLUDE))
        return AdRules(allowed=allowed, exclude=exclude)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return AdRules()


def judge(
    category: str | None,
    *,
    is_person: bool | None,
    real_entity: bool | None,
    rules: AdRules,
) -> tuple[bool | None, str]:
    """(광고 재료로 쓸 수 있는지, 이유). 모르면 None."""
    if category is None or category == "unknown":
        return None, f"{PENDING}: 공유 분류기가 아직 분류하지 못함"
    if category in NEVER_TREND:
        return False, f"{category} 분류는 광고 재료로 쓰지 않음"
    if is_person:
        return False, "사람 이름 주제라 광고 재료로 쓰지 않음 (초상권)"
    if category in rules.exclude:
        return False, f"{category} 분류는 회사 추천 안 함 분류"
    if category not in rules.allowed:
        return False, f"{category} 분류는 회사 광고 재료 분류가 아님"
    if real_entity:
        return True, "실존 이름·상표가 든 주제라 분위기·형식만 빌릴 것"
    return True, "회사 광고 재료 분류"


def allowed_categories(rules: AdRules) -> Sequence[str]:
    return [c for c in rules.allowed if c not in NEVER_TREND]
