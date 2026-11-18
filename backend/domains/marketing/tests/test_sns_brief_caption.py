"""기획안 캡션 재구성: 원문 복사가 아니라 SNS 형식으로 다시 짜고 출처 메타를 남긴다 (목 모드)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent import handlers, sns
from marketing_agent.main import app

CTX = "ctx_inc_0001"
client = TestClient(app)

IDEA = "신간 그림책 세 권을 서점 진열대 사진과 함께 소개한다. 두 번째 문장은 상세 일정이다."
WHY = "봄 나들이 철에는 가볍게 읽을 책을 찾는 사람이 늘어난다. 둘째 문장은 내부 메모다."
FACT = {"label": '"독서" 검색 작년 11월', "value": "평균의 1.6배", "source": "trend"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()


def _add(**over) -> None:
    data = {
        "proposal_id": "PROP-7",
        "title": "봄 그림책 소개",
        "idea": IDEA,
        "why": WHY,
        "facts": [FACT],
        "target": {"label": "캠페인 기간 주문 증분", "value": "+10% 이상"},
        "approved_by": "gaon",
    } | over
    handlers.STATE.briefs.add(CampaignBriefInput(**data), datetime.now(UTC))


def _call(skill: str, input: dict) -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def _draft() -> dict:
    return _call("draft_sns_post", {"proposal_id": "PROP-7"})


def test_caption_is_restructured_not_copied() -> None:
    _add()
    out = _draft()
    assert out["status"] == "completed", out
    caption = out["output"]["caption"]
    head = caption.split("\n\n")
    assert head[0] == "봄 그림책 소개"  # 첫 줄 훅
    assert "신간 그림책 세 권을 서점 진열대 사진과 함께 소개한다." in caption
    assert "근거(시연 환경 기준): " in caption and "평균의 1.6배" in caption  # 값은 글자 그대로
    assert "기획팀 제안 기반" in caption
    assert "#캣북스" in caption and sns.AI_LABEL_TEXT in caption
    # 원문 통째 복사 아님: 뒷 문장·목표치·옛 형식 머리말은 없다
    assert "두 번째 문장" not in caption and "둘째 문장" not in caption
    assert "+10%" not in caption
    assert "\n근거\n" not in caption
    # 새로 만든 숫자 없음: 숫자는 기획안 값에서만 나온다
    body = caption.split(sns.AI_LABEL_TEXT)[0]
    assert set("".join(c for c in body if c.isdigit())) <= set("1" + "6")


def test_caption_length_is_bounded() -> None:
    _add(title="제목 " * 60, idea="길고 " * 200, why="이유 " * 200)
    caption = _draft()["output"]["caption"]
    assert len(caption) <= sns.CAPTION_MAX
    body = caption.split(sns.AI_LABEL_TEXT)[0]
    assert len(body) < 400 and "…" in body


def test_blank_fields_are_skipped() -> None:
    _add(facts=[{"label": "독서", "value": "x", "source": "s"}])
    out = _draft()["output"]
    names = [f["name"] for f in out["caption_source"]["fields"]]
    assert names == ["title", "idea", "why", "facts"]
    handlers.reset_state()
    _add(facts=[])
    out = _draft()["output"]
    assert [f["name"] for f in out["caption_source"]["fields"]] == ["title", "idea", "why"]
    assert "근거" not in out["caption"]
    # 제목이 비어도 본문부터 시작한다(저장소가 막지 않는 경로를 직접 확인)
    handlers.reset_state()
    stored = type(
        "S",
        (),
        {
            "proposal_id": "PROP-7",
            "brief": CampaignBriefInput(
                proposal_id="PROP-7", title=" ", idea=IDEA, why=" ", approved_by="g"
            ),
        },
    )()
    body, meta = sns._brief_body_and_source(stored)
    assert body.startswith("신간 그림책") and [f["name"] for f in meta["fields"]] == ["idea"]


def test_source_meta_records_names_lengths_hashes_not_values() -> None:
    _add()
    out = _draft()["output"]
    meta = out["caption_source"]
    assert meta["proposal_id"] == "PROP-7" and meta["reconstructed"] is True
    assert [f["name"] for f in meta["fields"]] == ["title", "idea", "why", "facts"]
    first = meta["fields"][0]
    assert first["chars"] == len("봄 그림책 소개") and len(first["sha12"]) == 12
    # 해시태그 근거(whitelist 태그 이름)는 값이 아니라 따로 둔다: 태그 `#그림책` 자체는 들어간다
    dumped = str({k: v for k, v in meta.items() if k != "hashtags"})
    assert "그림책" not in dumped
    assert "그림책" not in str(meta["hashtags"]["topic"][0]["from"])
    assert all(set(t) == {"tag", "from"} for t in meta["hashtags"]["topic"])
    assert "1.6배" not in dumped  # 값 전문 없음
    # 저장소에서 다시 읽어도 같다
    assert handlers.STATE.sns.get(out["post_draft_id"]).caption_source == meta


def test_notice_and_coupon_drafts_have_no_caption_source() -> None:
    coupon = {
        "campaign_id": "cmp_1",
        "unit_value": 2000,
        "constraints": [{"code": c, "text": f"{c} 안내"} for c in sns.CONSTRAINT_LABELS],
    }
    out = _call("draft_sns_post", {"coupon": coupon})["output"]
    assert out["caption_source"] is None


@pytest.mark.parametrize(
    "text",
    [
        "인건비 절감 효과",
        "직원을 대체한다",
        "에이전트가 빨라진다",
        "인건비를 절감한다",
        "최고의 책",
    ],
)
@pytest.mark.parametrize("field", ["title", "idea", "why"])
def test_forbidden_claims_are_rejected(field: str, text: str) -> None:
    _add(**{field: text})
    out = _draft()
    assert out["status"] == "failed" and out["outcome"] == "forbidden_claim", out


def test_forbidden_claim_in_unused_sentence_is_still_rejected() -> None:
    _add(why="봄에는 책을 찾는다. 인건비 절감도 된다.")
    assert _draft()["outcome"] == "forbidden_claim"


def test_participation_phrase_still_rejected() -> None:
    _add(idea="팔로우하면 선물을 드린다")
    assert _draft()["outcome"] == "forbidden_phrase"


def test_check_rejects_forbidden_claim_in_caption() -> None:
    _add()
    draft = _draft()["output"]
    ok = _call(
        "check_sns_post",
        {
            "post_draft_id": draft["post_draft_id"],
            "caption": draft["caption"],
            "is_ai_generated": True,
        },
    )["output"]
    assert "forbidden_claim" not in ok["problems"] and ok["unverified_number_count"] == 0
    bad = _call(
        "check_sns_post",
        {
            "post_draft_id": draft["post_draft_id"],
            "caption": draft["caption"] + "\n직원 대체",
            "is_ai_generated": True,
        },
    )["output"]
    assert bad["verdict"] == "rejected" and "forbidden_claim" in bad["problems"]
