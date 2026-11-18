"""기획안 SNS 초안의 주제 해시태그: 화이트리스트 매핑·한도·미매핑 폴백·중복·검증."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent import handlers, sns, sns_hashtags
from marketing_agent.main import app

CTX = "ctx_inc_0001"
client = TestClient(app)


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv(sns_hashtags.CONFIG_ENV, raising=False)
    events.reset_mock_bus()
    handlers.reset_state()
    sns_hashtags.reset_cache()


def _brief(**over) -> CampaignBriefInput:
    data = {
        "proposal_id": "PROP-9",
        "title": "봄 소개",
        "idea": "가볍게 소개한다.",
        "why": "이유가 있다.",
        "approved_by": "gaon",
    } | over
    return CampaignBriefInput(**data)


def _tbl() -> sns_hashtags.HashtagTable:
    tbl = sns_hashtags.table(sns.tag_check, sns.HASHTAG_MAX)
    assert tbl is not None
    return tbl


def _select(**over):
    return sns_hashtags.select_hashtags(_brief(**over), _tbl(), sns.HASHTAG_MAX)


def _meta() -> dict:
    return {
        "context_id": CTX,
        "token": "mock:orchestrator",
        "path": [],
        "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
    }


def _draft(**over) -> dict:
    handlers.STATE.briefs.add(_brief(**over), datetime.now(UTC))
    body = {"meta": _meta(), "skill": "draft_sns_post", "input": {"proposal_id": "PROP-9"}}
    return client.post("/a2a", json=body).json()


def _check(draft: dict, caption: str) -> dict:
    body = {
        "meta": _meta(),
        "skill": "check_sns_post",
        "input": {
            "post_draft_id": draft["output"]["post_draft_id"],
            "caption": caption,
            "is_ai_generated": True,
        },
    }
    return client.post("/a2a", json=body).json()["output"]


def test_default_table_is_valid() -> None:
    assert _tbl().brand == ("#캣북스",)


def test_unmapped_brief_keeps_brand_only() -> None:
    tags, meta = _select()
    assert tags == ("#캣북스",) == sns.HASHTAGS and meta["topic"] == []
    out = _draft()
    assert out["output"]["hashtags"] == ["#캣북스"]
    assert out["output"]["caption"].endswith("\n\n#캣북스")


def test_keyword_category_format_mapping_and_sources() -> None:
    tags, meta = _select(
        title="신간 그림책 소개", content_category="정보·팁", content_format="shorts"
    )
    assert tags == ("#캣북스", "#그림책", "#책", "#신간", "#꿀팁", "#쇼츠")
    assert [(t["tag"], t["from"]) for t in meta["topic"]] == [
        ("#그림책", "title"),
        ("#책", "title"),
        ("#신간", "title"),
        ("#꿀팁", "content_category"),
        ("#쇼츠", "content_format"),
    ]


def test_format_tag_added_when_room() -> None:
    tags, meta = _select(content_format="reels")
    assert tags == ("#캣북스", "#릴스")
    assert meta["topic"] == [{"tag": "#릴스", "from": "content_format"}]


def test_keyword_found_in_products_and_trend_refs() -> None:
    _, meta = _select(products=["고양이 서점 세트"], trend_refs=["독서 챌린지"])
    assert {(t["tag"], t["from"]) for t in meta["topic"]} == {
        ("#도서", "products"),
        ("#책", "products"),
        ("#독서", "trend_refs"),
        ("#고양이", "products"),
    }


def test_unknown_category_and_why_text_are_ignored() -> None:
    tags, _ = _select(content_category="아재개그", why="그림책 도서 독서")
    assert tags == ("#캣북스",)


def test_duplicates_are_removed() -> None:
    tags, _ = _select(title="그림책", idea="도서 서점 책 추천")
    assert len(tags) == len(set(tags)) and tags.count("#책") == 1


def test_topic_limit_and_global_cap() -> None:
    tags, _ = _select(title="그림책 서점 독서 신간 고양이")
    assert len(tags) == 1 + _tbl().max_topic
    tags, _ = sns_hashtags.select_hashtags(_brief(title="그림책"), _tbl(), 2)
    assert len(tags) <= 2


def test_draft_caption_and_meta() -> None:
    out = _draft(title="신간 그림책", content_category="챌린지")["output"]
    tags = out["hashtags"]
    assert tags[0] == "#캣북스" and "#그림책" in tags and "#챌린지" in tags
    assert out["caption"].endswith(" ".join(tags))
    topic = out["caption_source"]["hashtags"]["topic"]
    assert {"tag": "#챌린지", "from": "content_category"} in topic
    assert handlers.STATE.sns.get(out["post_draft_id"]).hashtags == tags


def test_check_accepts_unchanged_and_rejects_edited_hashtags() -> None:
    draft = _draft(title="신간 그림책")
    caption = draft["output"]["caption"]
    ok = _check(draft, caption)
    assert "caption_differs_from_draft" not in ok["problems"]
    assert "unverified_number" not in ok["problems"]
    assert ok["hashtag_count"] == len(draft["output"]["hashtags"])
    added = _check(draft, caption + " #추가")
    assert "caption_differs_from_draft" in added["problems"]
    dropped = _check(draft, caption.replace(" #그림책", ""))
    assert "caption_differs_from_draft" in dropped["problems"]


def test_outage_hashtags_unchanged() -> None:
    assert sns.HASHTAGS == ("#캣북스",)  # outage_sns는 이 상수 그대로 쓴다


@pytest.mark.parametrize(
    "tag", ["#좋아요", "#팔로우이벤트", "#최고", "#공 백", "캣북스", "#a-b", "#", "#태그!"]
)
def test_bad_tags_make_config_invalid(tag: str, tmp_path: Path) -> None:
    path = tmp_path / "h.toml"
    path.write_text(f'brand = ["#캣북스"]\n[category]\n"챌린지" = ["{tag}"]\n', encoding="utf-8")
    with pytest.raises(sns_hashtags.HashtagConfigError):
        sns_hashtags.load_table(path, sns.tag_check, sns.HASHTAG_MAX)


def test_invalid_config_falls_back_to_brand_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.toml"
    path.write_text('brand = ["#캣북스"]\n[category]\n"챌린지" = ["#좋아요"]\n', encoding="utf-8")
    monkeypatch.setenv(sns_hashtags.CONFIG_ENV, str(path))
    sns_hashtags.reset_cache()
    out = _draft(title="그림책", content_category="챌린지")["output"]
    assert out["hashtags"] == ["#캣북스"]
    assert out["caption_source"]["hashtags"]["table"] == "unavailable"


def test_missing_config_file_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(sns_hashtags.CONFIG_ENV, str(tmp_path / "none.toml"))
    sns_hashtags.reset_cache()
    assert _draft(title="그림책")["output"]["hashtags"] == ["#캣북스"]


def test_domestic_shipping_phrase_does_not_add_book_tags() -> None:
    tags, _ = _select(title="도서산간 배송비 안내")
    assert tags == ("#캣북스",)


@pytest.mark.parametrize(
    "toml_text",
    [
        'brand = ["#캣북스"]\n[keyword]\nwords = ["x"]\n',
        'brand = ["#캣북스"]\nkeyword = "x"\n',
        'brand = ["#캣북스"]\ncategory = ["x"]\n',
    ],
)
def test_malformed_structure_falls_back_to_brand_only(
    toml_text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(toml_text, encoding="utf-8")
    monkeypatch.setenv(sns_hashtags.CONFIG_ENV, str(path))
    sns_hashtags.reset_cache()
    out = _draft(title="그림책")["output"]
    assert out["hashtags"] == ["#캣북스"]
    assert out["caption_source"]["hashtags"]["table"] == "unavailable"
