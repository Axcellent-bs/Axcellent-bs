"""규칙 초안 문장 정리 (11/26 D): 떨어져도 말이 되게.

운영 wr_bc24dee53e(누비투어 제안 1, 캐릭터 안 고름): "원본 일러스트 캐릭터", 주제 자리에 회사 이름
("'누비투어' 순간에"), 2장면 자막이 콘셉트 문장 통째였다. LLM은 부르지 않는다."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_rule_wording", "test_video_plan.py")
C = _load("company_character_helpers_rule_wording", "test_plan_company_character.py")
IDEA = "누비투어 채널을 알리는 15초 세로 숏폼으로 재미있게 여행 순간을 보여 준다"


@pytest.fixture
def nubi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[brand] 없는 누비투어 (서버처럼 주인공이 기본값)."""
    root = tmp_path / "tenants"
    (root / "nubi").mkdir(parents=True)
    (root / "nubi" / "tenant.toml").write_text('name = "누비투어"\n', encoding="utf-8")
    monkeypatch.setenv("AX_TENANTS_DIR", str(root))
    monkeypatch.setenv("AX_TENANT", "nubi")


def _prop(**kw: Any) -> Any:
    fact = {"label": "주제 '온천' 후보 노천탕 (언급 3회)", "value": "3", "source": "trend"}
    base = {"title": "누비투어 홍보 영상", "idea": IDEA, "insight": "누비투어 홍보", "products": [],
            "content_category": None, "shorts_format": None, "facts": [fact],
            "created_at": datetime(2026, 11, 26, tzinfo=UTC)}  # fmt: skip
    return V._prop(**(base | kw))  # noqa: SLF001


def test_nubi_rule_plan_reads_naturally(nubi: None) -> None:
    plan = vp.fun_rule_plan(_prop())
    assert plan.scenes[0].visual.startswith("첫 1초 반전: '온천' 순간에 주인공이 깜짝 놀라")
    assert "원본 일러스트" not in " ".join(s.visual for s in plan.scenes)
    body = plan.scenes[:-1]  # 엔드카드 빼고
    texts = [t for s in body for t in (s.visual, s.prompt, s.caption)]
    assert all("누비투어" not in t for t in texts)  # 회사 이름은 엔드카드만
    assert all(len(s.caption) <= vp.RULE_CAPTION_MAX for s in plan.scenes)
    assert plan.scenes[1].caption != plan.concept


def test_theme_falls_back_without_a_topic(nubi: None) -> None:
    assert vp.rule_theme(_prop(facts=[])) == vp.THEME_FALLBACK  # 주제 후보도 회사 이름뿐
    assert vp.rule_theme(_prop(content_category="브이로그")) == "브이로그"  # 회사 이름 아님


def test_second_caption_is_cut_and_never_the_concept(nubi: None) -> None:
    plan = vp.fun_rule_plan(_prop(idea="온천에서 피로를 푸는 여행자의 하루를 보여 준다"))
    assert plan.concept.startswith("온천에서")
    assert plan.scenes[1].caption == ""  # 콘셉트 문장과 같으면 비운다
    names = vp.company_names()
    assert vp.body_caption("여행 가방을 싸다가 깜짝 놀라는 순간", "다른 콘셉트", names) == (
        "여행 가방을 싸다가 깜짝 놀라는…"
    )
    assert len(vp.body_caption("여행 가방을 싸다가 깜짝 놀라는 순간", "x", names)) == 18


def test_company_character_illustration_keeps_todays_wording(nubi: None) -> None:
    plan = vp.fun_rule_plan(_prop(company_character=C.DRAWN))
    assert "순간에 회사 캐릭터가 깜짝 놀라" in plan.scenes[0].visual


def test_josa() -> None:
    assert vp._ga("주인공") == "이" and vp._ga("회사 캐릭터") == "가"  # noqa: SLF001
    assert vp._ga("원본 일러스트 여행자") == "가" and vp._ga("Cat") == "가"  # noqa: SLF001
