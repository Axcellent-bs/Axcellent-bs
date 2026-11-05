from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from marketing_agent import handlers
from marketing_agent.briefs import campaign_id_for
from marketing_agent.links import (
    ALLOWED_MEDIUMS,
    ALLOWED_SOURCES,
    SOURCE_MEDIUMS,
    build_campaign_link,
)
from marketing_agent.main import app, load_card

CID = "cmp_PROP-1"
BASE = "https://shop.example.com/products/1"
client = TestClient(app)


def _q(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query, keep_blank_values=True)


def test_basic_link_has_three_tags():
    url = build_campaign_link(BASE, CID, "banner", "onsite")
    assert url == f"{BASE}?utm_source=banner&utm_medium=onsite&utm_campaign={CID}"


@pytest.mark.parametrize(
    ("source", "medium"),
    [(s, m) for s, mediums in SOURCE_MEDIUMS.items() for m in mediums],
)
def test_every_allowed_combination_works(source, medium):
    url = build_campaign_link(BASE, CID, source, medium)
    assert dict(_q(url)) == {"utm_source": source, "utm_medium": medium, "utm_campaign": CID}


def test_combination_table_covers_exactly_the_allowed_lists():
    assert set(SOURCE_MEDIUMS) == set(ALLOWED_SOURCES)
    assert {m for ms in SOURCE_MEDIUMS.values() for m in ms} <= set(ALLOWED_MEDIUMS)
    assert set(ALLOWED_MEDIUMS) == {"onsite", "social", "cpc"}
    assert set(ALLOWED_SOURCES) == {"banner", "notice", "instagram", "google", "youtube"}


@pytest.mark.parametrize(
    ("source", "medium"),
    [("banner", "cpc"), ("banner", "social"), ("instagram", "onsite"), ("google", "social")],
)
def test_mismatched_combination_is_rejected(source, medium):
    with pytest.raises(ValueError, match="does not fit"):
        build_campaign_link(BASE, CID, source, medium)


@pytest.mark.parametrize("source", ["", "Banner", "facebook", "banner ", None])
def test_unknown_source_is_rejected(source):
    with pytest.raises(ValueError, match="source"):
        build_campaign_link(BASE, CID, source, "onsite")


@pytest.mark.parametrize("medium", ["", "email", "CPC", None])
def test_unknown_medium_is_rejected(medium):
    with pytest.raises(ValueError, match="medium"):
        build_campaign_link(BASE, CID, "banner", medium)


@pytest.mark.parametrize(
    "campaign_id",
    [
        "",
        "PROP-1",
        "cmp_",
        "cmp_-x",
        "cmp_PROP 1",
        "cmp_PROP-1\n",
        "cmp_한글",
        "cmp_" + "a" * 65,
        None,
    ],
)
def test_bad_campaign_id_is_rejected(campaign_id):
    with pytest.raises(ValueError, match="campaign_id"):
        build_campaign_link(BASE, campaign_id, "banner", "onsite")


def test_campaign_id_from_a_brief_and_the_64_char_limit_pass():
    assert campaign_id_for("PROP-7") == "cmp_PROP-7"
    url = build_campaign_link(BASE, campaign_id_for("PROP-7"), "notice", "onsite")
    assert dict(_q(url))["utm_campaign"] == "cmp_PROP-7"
    longest = "cmp_" + "a" * 64
    url = build_campaign_link(BASE, longest, "notice", "onsite")
    assert dict(_q(url))["utm_campaign"] == longest


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        " https://shop.example.com",
        "ftp://shop.example.com/x",
        "javascript:alert(1)",
        "//shop.example.com/x",
        "/products/1",
        "shop.example.com/products/1",
        "https:///nohost",
        "https://user:pw@shop.example.com/",
        "https://token@shop.example.com/",
        "https://shop.example.com:99999/",
        "https://shop.example.com/a\nb",
        None,
    ],
)
def test_bad_base_url_is_rejected(base_url):
    with pytest.raises(ValueError, match="base_url"):
        build_campaign_link(base_url, CID, "banner", "onsite")


def test_http_and_port_are_allowed():
    url = build_campaign_link("http://localhost:3090/shop", CID, "banner", "onsite")
    assert url.startswith("http://localhost:3090/shop?utm_source=banner")


def test_existing_query_and_fragment_are_preserved():
    url = build_campaign_link(
        "https://shop.example.com/list?category=cat&page=2#reviews", CID, "google", "cpc"
    )
    assert urlsplit(url).fragment == "reviews"
    assert _q(url) == [
        ("category", "cat"),
        ("page", "2"),
        ("utm_source", "google"),
        ("utm_medium", "cpc"),
        ("utm_campaign", CID),
    ]


def test_existing_utm_tags_are_overwritten_not_duplicated():
    url = build_campaign_link(
        f"{BASE}?utm_source=old&x=1&UTM_Medium=old&utm_campaign=cmp_OLD&utm_term=keep",
        CID,
        "instagram",
        "social",
    )
    pairs = _q(url)
    assert [k for k, _ in pairs].count("utm_source") == 1
    assert [k for k, _ in pairs].count("utm_campaign") == 1
    assert not any(k.lower() == "utm_medium" and k != "utm_medium" for k, _ in pairs)
    assert dict(pairs) == {
        "x": "1",
        "utm_term": "keep",
        "utm_source": "instagram",
        "utm_medium": "social",
        "utm_campaign": CID,
    }


def test_blank_values_in_existing_query_survive():
    assert ("flag", "") in _q(build_campaign_link(f"{BASE}?flag=", CID, "banner", "onsite"))


def test_korean_and_spaces_are_percent_encoded():
    url = build_campaign_link(
        "https://shop.example.com/상품 목록?q=고양이 사료#후기 보기", CID, "notice", "onsite"
    )
    assert " " not in url
    assert all(ord(ch) < 128 for ch in url)
    parts = urlsplit(url)
    assert parts.path == "/%EC%83%81%ED%92%88%20%EB%AA%A9%EB%A1%9D"
    assert dict(_q(url))["q"] == "고양이 사료"
    assert parts.fragment == "%ED%9B%84%EA%B8%B0%20%EB%B3%B4%EA%B8%B0"


def test_already_encoded_input_is_not_double_encoded():
    url = build_campaign_link(f"{BASE}%20x?q=a%20b#f%20g", CID, "notice", "onsite")
    assert "%2520" not in url
    assert dict(_q(url))["q"] == "a b"


def test_only_scheme_case_is_normalised():
    url = build_campaign_link("HTTPS://Shop.Example.com/", CID, "banner", "onsite")
    assert url.startswith("https://Shop.Example.com/?")


def test_result_is_idempotent_for_same_tags():
    once = build_campaign_link(BASE, CID, "youtube", "cpc")
    assert build_campaign_link(once, CID, "youtube", "cpc") == once


# --- 읽기 skill ---


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()


def _call(input: dict, token: str = "mock:planning") -> dict:
    body = {
        "meta": {
            "context_id": "ctx_inc_0001",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "build_campaign_link",
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def test_skill_returns_link_and_is_read_only():
    out = _call({"base_url": BASE, "campaign_id": CID, "source": "banner", "medium": "onsite"})
    assert out["status"] == "completed"
    assert out["output"]["url"] == build_campaign_link(BASE, CID, "banner", "onsite")
    assert out["output"]["utm_campaign"] == CID
    card = {s.id: s for s in load_card().skills}["build_campaign_link"]
    assert card.grade == "read"
    assert set(card.allowed_requesters) == {"planning", "orchestrator", "dashboard-api", "shop-api"}


@pytest.mark.parametrize(
    "bad",
    [
        {"base_url": "ftp://x.com", "campaign_id": CID, "source": "banner", "medium": "onsite"},
        {"base_url": BASE, "campaign_id": "PROP-1", "source": "banner", "medium": "onsite"},
        {"base_url": BASE, "campaign_id": CID, "source": "tiktok", "medium": "onsite"},
        {"base_url": BASE, "campaign_id": CID, "source": "banner", "medium": "cpc"},
        {"base_url": BASE, "campaign_id": CID, "source": "banner"},
    ],
)
def test_skill_rejects_bad_input_as_invalid_input(bad):
    out = _call(bad)
    assert out["status"] == "failed"
    assert out["outcome"] == "invalid_input"


def test_skill_refuses_unlisted_requester():
    out = _call(
        {"base_url": BASE, "campaign_id": CID, "source": "banner", "medium": "onsite"},
        token="mock:support",
    )
    assert out["status"] != "completed"
