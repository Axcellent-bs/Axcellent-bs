"""광고 재료 판정 (ad_material). 캣북스 설정의 [promo] trend_categories."""

from __future__ import annotations

from pathlib import Path

from data_agent.skills import ad_material as am

CATBOOKS = Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "trends.toml"


def test_catbooks_allows_books_and_hobby() -> None:
    """캣북스는 책 회사라 교육·책이 광고 재료 분류에 있어야 한다 (생활·취미도, 2026-11-19)."""
    rules = am.load_rules({am.TREND_CONFIG_ENV: str(CATBOOKS)})
    for category in ("교육·책", "생활·취미"):
        ok, why = am.judge(category, is_person=False, real_entity=False, rules=rules)
        assert ok is True and why == "회사 광고 재료 분류"
        assert category in am.allowed_categories(rules)
    # 다른 값은 그대로
    assert rules.allowed[:7] == am.DEFAULT_ALLOWED
    # 늘 빼는 분류·사람 이름은 그대로 아님
    assert am.judge("뉴스·사건", is_person=False, real_entity=False, rules=rules)[0] is False
    assert am.judge("교육·책", is_person=True, real_entity=False, rules=rules)[0] is False


def test_default_rules_do_not_include_books() -> None:
    ok, why = am.judge("교육·책", is_person=False, real_entity=False, rules=am.AdRules())
    assert ok is False and "광고 재료 분류가 아님" in why
