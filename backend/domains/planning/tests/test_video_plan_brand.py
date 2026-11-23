"""영상 제작안이 회사 [brand]·의뢰 주제를 따른다 (11/22, 고양이 책방 고정 없애기).

11/20 서버 영상: "10대 여성이 좋아하는 아이돌 찾아서 책 홍보" 의뢰도 고양이가 책방에서 책을 꺼내는
영상이 됐다. 제작안 STYLE·PROMPT에 온라인 서점·고양이 마스코트가 박혀 있었고, 실존 인물 주제를
무엇으로 대신 표현할지 지시가 없었다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_workflow.models import Proposal
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")

# 가상의 그룹 이름(실존 인물 아님).
# 데이터분석 주제 후보 근거 줄 모양(trend_report_insight.row_facts)
IDOL_FACTS = [
    {"label": "주제 '아이돌' 후보 별빛소년단 (10대 여성) 네이버 검색 관심", "value": "87/100",
     "source": "trend_report"},
    {"label": "주제 '아이돌' 후보 달무리 (10대 여성) 네이버 검색 관심", "value": "64/100",
     "source": "trend_report"},
]  # fmt: skip


@pytest.fixture(autouse=True)
def _no_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AX_TENANT", "AX_TENANTS_DIR", "AX_COMPANY_CONNECTION_FILE"):
        monkeypatch.delenv(name, raising=False)


def _idol(**kw) -> Proposal:
    base = {
        "id": "PROP-9", "title": "10대 여성이 좋아하는 아이돌 찾아서 책 홍보", "from": "data",
        "skill": "trend_report",
        "insight": "## 주제 후보 · 아이돌\n- 별빛소년단: 네이버 검색 관심 87/100",
        "facts": IDOL_FACTS, "idea": "팬 문화로 책을 소개하는 숏츠", "why": "10대 여성 관심이 높다",
        "products": ["무대 뒤의 일기"], "format": "릴스 15초", "angle": "ride_trend",
        "created_at": datetime(2026, 11, 20, tzinfo=UTC),
    }  # fmt: skip
    return Proposal.model_validate({**base, **kw})


def _out(prompt: str = "Close-up of glowing light sticks and photo cards around a reading nook"):
    scenes = [{"visual": f"장면 {i}", "caption": "", "prompt": prompt} for i in range(4)]
    return vp._PlanOut.model_validate(
        {"concept": "응원봉 불빛 아래 책 읽기", "script": "훅 → 공감 → 마무리", "scenes": scenes}
    )


def _sent(monkeypatch: pytest.MonkeyPatch, tenant_id: str | None) -> tuple[str, object]:
    if tenant_id:
        monkeypatch.setenv("AX_TENANT", tenant_id)
    llm = MockLLMClient(responses={vp._PlanOut: _out()})
    plan = asyncio.run(vp.write(llm, _idol(), "ctx_wr_test"))
    return llm.calls[0], plan


def test_idol_request_prompt_has_fan_culture_brand_and_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent, plan = _sent(monkeypatch, "catbooks")
    assert "온라인 서점" not in sent  # 고정 글자 없음 (회사는 데이터 블록으로만)
    assert "응원봉" in sent and "포토카드" in sent and "팬 굿즈" in sent  # 팬 문화 지시
    assert "등장인물은 자체 제작 고양이 마스코트, 배경은 아늑한 동네 책방" in sent  # 캣북스 [brand]
    assert sent.index("캣북스") > sent.index("방향:")  # 회사 이름은 데이터 블록 안
    # 의뢰 주제·후보 이름은 제안 근거에 있던 값만
    assert '"topic": "아이돌"' in sent or "'topic': '아이돌'" in sent
    assert "별빛소년단" in sent and "달무리" in sent
    assert plan.written_by == "llm" and "cat mascot" in plan.style


def test_topic_comes_only_from_proposal_values() -> None:
    assert vp.topic_of(_idol()) == {"topic": "아이돌", "topic_candidates": ["별빛소년단", "달무리"]}
    plain = _idol(facts=[], content_category="브이로그")
    assert vp.topic_of(plain) == {"topic": "브이로그", "topic_candidates": []}
    assert vp.topic_of(_idol(facts=[], content_category=None))["topic"] == _idol().title


def test_nubi_style_and_rule_plan_have_no_cat_or_bookstore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_TENANT", "nubi")
    style = vp.style()
    assert "travelers" in style and "overseas travel" in style
    plan = vp.rule_plan(_idol())
    texts = [plan.style, *(s.prompt for s in plan.scenes), *(s.visual for s in plan.scenes)]
    words = " ".join(texts)
    for banned in ("cat", "bookstore", "bookshelf", "고양이", "책방", "book about"):
        assert banned not in words.lower()
    assert "원본 일러스트 여행자" in plan.scenes[0].visual
    assert all(vp.prompt_problems(s.prompt) == [] for s in plan.scenes)
    sent, _ = _sent(monkeypatch, "nubi")
    assert "원본 일러스트 여행자" in sent and "해외 여행지 풍경" in sent and "누비투어" in sent


@pytest.mark.parametrize("tenant_id", [None, "studyon"])
def test_without_brand_defaults_are_used(monkeypatch: pytest.MonkeyPatch, tenant_id) -> None:
    if tenant_id:
        monkeypatch.setenv("AX_TENANT", tenant_id)
    style = vp.style()
    assert "original illustrated characters, not real people" in style
    assert "a setting that fits the company's products" in style
    assert "flat illustration style" in style and "cat" not in style
    plan = vp.rule_plan(_idol())
    assert all(vp.prompt_problems(s.prompt) == [] for s in plan.scenes)
    assert len(plan.style) <= vp.STYLE_MAX
    # 아이돌 주제는 규칙 제작안에서도 이름 대신 팬 문화 소품으로
    assert vp.FAN_THEME in plan.scenes[0].prompt and "idol" not in plan.scenes[0].prompt.lower()


def test_brand_values_breaking_rules_fall_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "acme"
    folder.mkdir()
    (folder / "tenant.toml").write_text(
        'name = "에이크미"\n[brand]\n'
        'character_en = "a famous singer lookalike"\n'  # 실존 인물
        'setting_en = "store open 24 hours"\n'  # 숫자
        'character_ko = "아이돌 멤버"\n'
        'style_en = "watercolor style"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AX_TENANTS_DIR", str(tmp_path))
    monkeypatch.setenv("AX_TENANT", "acme")
    style = vp.style()
    assert "famous" not in style and "24" not in style
    assert "original illustrated characters, not real people" in style
    assert "a setting that fits the company's products" in style
    assert "watercolor style" in style  # 통과한 칸은 그대로


# ------------------------------------------------------ 실존 이름 막기 (11/23 예진 님 리뷰)
# 후보 "NewJeans"는 실존 그룹 이름 검사용 값. 영상에는 쓰지 않는다
NAME_FACTS = [
    {"label": "주제 '아이돌' 후보 NewJeans (10대 여성) 네이버 검색 관심", "value": "90/100",
     "source": "trend_report"},
    IDOL_FACTS[0],  # 별빛소년단
]  # fmt: skip


def _plan_with(monkeypatch: pytest.MonkeyPatch, got: vp._PlanOut):
    monkeypatch.setenv("AX_TENANT", "catbooks")
    llm = MockLLMClient(responses={vp._PlanOut: got})
    return asyncio.run(vp.write(llm, _idol(facts=NAME_FACTS), "ctx_wr_test"))


@pytest.mark.parametrize(
    "prompt",
    [
        "Close-up of NewJeans dancing with light sticks",
        "Close-up of  newjeans dancing with light sticks",  # 대소문자·공백
        "Close-up of ＮｅｗＪｅａｎｓ dancing with light sticks",  # 전각(NFKC)
        "별빛소년단 members holding a book",
        "별빛 소년단 members holding a book",
    ],
)
def test_candidate_name_in_scene_prompt_falls_back_to_rule_plan(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, prompt: str
) -> None:
    assert vp.topic_of(_idol(facts=NAME_FACTS))["topic_candidates"] == ["NewJeans", "별빛소년단"]
    with caplog.at_level("INFO", logger="ax.planning"):
        plan = _plan_with(monkeypatch, _out(prompt))
    assert plan.written_by == "rule"
    assert "real_name_in_scene" in caplog.text
    # 이유 코드만 남기고 이름(값)은 로그·제작안에 남기지 않는다
    assert "NewJeans" not in caplog.text and "별빛소년단" not in caplog.text
    texts = [plan.concept, plan.script, *(s.prompt for s in plan.scenes)]
    assert not any("newjeans" in t.lower() for t in texts)


def test_candidate_name_in_visual_or_concept_also_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual = _out()
    visual.scenes[1].visual = "별빛소년단 응원봉을 든 팬이 책을 편다"
    assert _plan_with(monkeypatch, visual).written_by == "rule"
    concept = _out()
    concept.concept = "NewJeans 팬을 위한 책 소개"
    assert _plan_with(monkeypatch, concept).written_by == "rule"


def test_fan_culture_scene_without_names_keeps_llm_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    got = _out("Fans waving glowing light sticks under warm stage lights around a reading nook")
    plan = _plan_with(monkeypatch, got)
    assert plan.written_by == "llm"
    assert vp.reject_reason(_idol(facts=NAME_FACTS), got) is None
    assert plan.scenes[0].prompt == got.scenes[0].prompt


@pytest.mark.parametrize(
    ("tenant_id", "who"),
    [
        ("catbooks", "a friendly original cartoon cat mascot"),  # 단수
        ("nubi", "original illustrated travelers, not real people"),  # 복수
    ],
)
def test_rule_plan_sentences_fit_singular_and_plural(
    monkeypatch: pytest.MonkeyPatch, tenant_id: str, who: str
) -> None:
    monkeypatch.setenv("AX_TENANT", tenant_id)
    plan = vp.rule_plan(_idol())
    first, last = plan.scenes[0].prompt, plan.scenes[-1].prompt
    assert f"{who} noticing something surprising and turning toward the camera" in first
    assert f"Closing shot: {who} waving at the camera" in last
    # 수에 따라 틀리는 꼴(notice/notices, wave/waves)이 없다
    for wrong in (f"{who} notice ", f"{who} notices ", f"{who} wave ", f"{who} waves "):
        assert all(wrong not in s.prompt for s in plan.scenes)
