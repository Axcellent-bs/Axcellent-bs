"""ad_performance: 메타·구글 애즈 광고 성과 (읽기만). 실제 호출 없음 (가짜 응답).

두 플랫폼 값·micros 변환·연령 성별 비중·영상 잇기·env 없음 needs·계정 번호 없음 건너뜀·하루 상한·
하루 1번·옛 env 이름·토큰 값 없음·쓰기 엔드포인트를 부르는 코드 없음을 본다."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas.ad_performance import AdPerformanceRequest
from data_agent.connectors import google_ads as gads
from data_agent.connectors import meta_ads
from data_agent.connectors.naver_searchad import JsonDailyCache
from data_agent.skills import ad_performance as ap
from data_agent.skills import video_performance as vp

NOW = datetime(2026, 11, 26, 6, 0, tzinfo=UTC)
VID = "abcdefghijk"
META_TOKEN, DEV, CID, SEC, RT, ACCESS = ("meta-secret-1", "dev-secret-2", "gcid-3", "gsec-4",
                                         "grt-5", "gat-6")  # fmt: skip
META_ENV = {"DATA_META_ADS_TOKEN": META_TOKEN}
GADS_ENV = {"DATA_GADS_DEVELOPER_TOKEN": DEV, "DATA_GADS_OAUTH_CLIENT_ID": CID,
            "DATA_GADS_OAUTH_CLIENT_SECRET": SEC, "DATA_GADS_REFRESH_TOKEN": RT,
            "DATA_GADS_LOGIN_CUSTOMER_ID": "111-222-3333"}  # fmt: skip


class Fake:
    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = str(request.url)
        if url.startswith(gads.TOKEN_URL):
            return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600})
        if url.startswith(meta_ads.HOST):
            assert request.method == "GET" and request.url.path == "/v27.0/act_123/insights"
            q = dict(request.url.params)
            assert json.loads(q["time_range"]) == {"since": "2026-11-19", "until": "2026-11-25"}
            if "breakdowns" in q:
                assert q["breakdowns"] == "age,gender"
                return httpx.Response(200, json={"data": [
                    {"age": "18-24", "gender": "female", "impressions": "300",
                     "video_play_actions": [{"action_type": "video_view", "value": "90"}]},
                    {"age": "25-34", "gender": "male", "impressions": "100",
                     "video_play_actions": [{"action_type": "video_view", "value": "10"}]},
                ]})  # fmt: skip
            return httpx.Response(200, json={"data": [{
                "campaign_id": "m-1", "campaign_name": f"릴스 {VID} 홍보", "impressions": "400",
                "reach": "350", "clicks": "8", "ctr": "2.0", "spend": "12.34",
                "account_currency": "KRW",
                "video_play_actions": [{"action_type": "video_view", "value": "100"}],
                "video_thruplay_watched_actions": [{"action_type": "video_view", "value": "40"}],
                "video_p25_watched_actions": [{"action_type": "video_view", "value": "80"}],
                "video_p50_watched_actions": [{"action_type": "video_view", "value": "60"}],
                "video_p75_watched_actions": [{"action_type": "video_view", "value": "45"}],
                "video_p100_watched_actions": [{"action_type": "video_view", "value": "30"}],
            }]})  # fmt: skip
        assert request.method == "POST"
        assert request.url.path == "/v26/customers/1234567890/googleAds:search"
        assert request.headers["Authorization"] == f"Bearer {ACCESS}"
        assert request.headers["developer-token"] == DEV
        assert request.headers["login-customer-id"] == "1112223333"
        query = json.loads(request.content)["query"]
        assert query.startswith("SELECT") and "BETWEEN '2026-11-19' AND '2026-11-25'" in query
        if "FROM campaign" in query:
            return httpx.Response(200, json={"results": [
                {"campaign": {"id": "g-1", "name": "유튜브 다른 캠페인"},
                 "customer": {"currencyCode": "KRW"},
                 "metrics": {"impressions": "1000", "clicks": "20", "ctr": 0.02,
                             "costMicros": "1230000", "videoTrueviewViews": "300",
                             "videoTrueviewViewRate": 0.3, "conversions": 2.0}},
            ]})  # fmt: skip
        if "age_range_view" in query:
            return httpx.Response(200, json={"results": [
                {"adGroupCriterion": {"ageRange": {"type": "AGE_RANGE_18_24"}},
                 "metrics": {"impressions": "600", "videoTrueviewViews": "200"}},
                {"adGroupCriterion": {"ageRange": {"type": "AGE_RANGE_18_24"}},
                 "metrics": {"impressions": "200", "videoTrueviewViews": "40"}},
                {"adGroupCriterion": {"ageRange": {"type": "AGE_RANGE_25_34"}},
                 "metrics": {"impressions": "200", "videoTrueviewViews": "60"}},
            ]})  # fmt: skip
        return httpx.Response(200, json={"results": [
            {"adGroupCriterion": {"gender": {"type": "FEMALE"}},
             "metrics": {"impressions": "1000", "videoTrueviewViews": "300"}},
        ]})  # fmt: skip


@pytest.fixture
def tenants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "tenants"
    (root / "catbooks").mkdir(parents=True)
    _ads(root, "1234567890", "123")
    monkeypatch.setenv("AX_TENANTS_DIR", str(root))
    return root


def _ads(root: Path, google: str, meta: str) -> None:
    (root / "catbooks" / "tenant.toml").write_text(
        f'name = "캣북스"\n[ads]\ngoogle_customer_id = "{google}"\nmeta_ad_account_id = "{meta}"\n',
        encoding="utf-8",
    )


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {ap.DB_ENV: str(tmp_path / "ads.db"), vp.DB_ENV: str(tmp_path / "perf.db"), **extra}
    store = vp.store_of(env)
    try:
        store.upsert(vp.Video(key=f"youtube:{VID}", platform="youtube", video_id=VID,
                              video_url=f"https://youtu.be/{VID}", campaign_id="CMP-1",
                              insight_id=None, proposal_id=None, test=False, published_at=NOW,
                              title=None, registered_at=NOW, category=None,
                              shorts_format=None))  # fmt: skip
    finally:
        store.close()
    return env


def _collect(env: dict[str, str], fake: Fake, now: datetime = NOW) -> ap.CollectResult:
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    store = ap.store_of(env)
    try:
        return asyncio.run(ap.collect_due(
            store, env, meta=meta_ads.MetaAds(env, client=client, calls=JsonDailyCache()),
            google=gads.GoogleAds(env, client=client, calls=JsonDailyCache()),
            companies=["catbooks"], now=now))  # fmt: skip
    finally:
        store.close()


def _read(env: dict[str, str], **req: Any) -> Any:
    return ap.ad_performance(AdPerformanceRequest(company="catbooks", **req), env=env,
                             now=NOW).output  # fmt: skip


def test_both_platforms_values_as_given(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **META_ENV, **GADS_ENV)
    assert _collect(env, Fake()).saved == 2
    out = _read(env)
    assert out.status == "known" and out.period == "2026-11-19~2026-11-25"
    by = {c.platform: c for c in out.campaigns}
    m, g = by["meta"], by["google"]
    assert (m.cost, m.currency, m.impressions, m.reach, m.clicks) == (12.34, "KRW", 400, 350, 8)
    assert (m.ctr, m.ctr_unit, m.views, m.thruplays) == (2.0, "percent", 100, 40)
    assert (m.p25, m.p50, m.p75, m.p100) == (80, 60, 45, 30)
    assert g.cost == 1.23 and g.currency == "KRW"  # micros ÷ 1e6 (변환은 이것만)
    got = (g.ctr, g.ctr_unit, g.views, g.view_rate, g.conversions)
    assert got == (0.02, "ratio", 300, 0.3, 2.0)
    assert g.reach is None and g.p25 is None  # 플랫폼이 안 주는 값은 비움
    assert m.video_key == f"youtube:{VID}" and g.video_key is None  # 이름에 영상 id가 있을 때만
    aud = {(a.platform, a.age, a.gender): a for a in out.audience}
    assert aud[("meta", "18-24", "female")].impressions_share == 0.75
    assert aud[("meta", "18-24", "female")].views_share == 0.9
    young = aud[("google", "AGE_RANGE_18_24", "")]
    assert (young.impressions, young.views, young.impressions_share) == (800, 240, 0.8)
    assert aud[("google", "", "FEMALE")].views_share == 1.0
    assert {s.platform: s.status for s in out.platforms} == {"meta": "known", "google": "known"}
    assert any(f.label.endswith("비용") for f in out.facts)
    only = _read(env, platform="meta")
    assert {c.platform for c in only.campaigns} == {"meta"}


def test_without_env_it_waits_with_needs_and_no_call(tmp_path: Path, tenants: Path) -> None:
    from data_agent import youtube_collect

    env = _env(tmp_path)
    assert asyncio.run(youtube_collect.collect_ads(env, NOW)) == 0
    out = _read(env)
    assert out.status == "unknown"
    st = {s.platform: s for s in out.platforms}
    assert st["meta"].reason.startswith(ap.WAITING) and "DATA_META_ADS_TOKEN" in st["meta"].reason
    assert any("DATA_GADS_DEVELOPER_TOKEN" in n for n in st["google"].needs)


def test_missing_account_id_skips_that_platform(tmp_path: Path, tenants: Path) -> None:
    _ads(tenants, "", "")
    env = _env(tmp_path, **META_ENV, **GADS_ENV)
    fake = Fake()
    assert _collect(env, fake).saved == 0 and fake.seen == []  # 부르지 않는다
    st = {s.platform: s for s in _read(env).platforms}
    assert st["meta"].reason == ap.NO_META_ACCOUNT and st["google"].reason == ap.NO_GOOGLE_ACCOUNT
    env["DATA_GADS_CUSTOMER_ID"] = "1234567890"  # 회사 설정이 없으면 env
    assert _collect(env, Fake()).saved == 1


def test_daily_cap_stops_calls(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **GADS_ENV, DATA_GADS_DAILY_MAX="1")
    fake = Fake()
    _collect(env, fake)
    assert len(fake.seen) == 1  # 토큰 1번 뒤 상한
    st = {s.platform: s for s in _read(env).platforms}["google"]
    assert st.status == "unknown" and "RATE_LIMITED" in (st.reason or "")


def test_once_a_day_and_days_must_match(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **META_ENV)
    _collect(env, Fake())
    again = Fake()
    assert _collect(env, again, NOW + timedelta(hours=3)).saved == 0 and again.seen == []
    out = _read(env, days=30, platform="meta")
    assert out.status == "unknown" and "저장분은 최근 7일만" in (out.platforms[0].reason or "")


def test_old_google_env_names_also_connect() -> None:
    old = {"GOOGLE_ADS_DEVELOPER_TOKEN": "d", "GOOGLE_ADS_CLIENT_ID": "c",
           "GOOGLE_ADS_CLIENT_SECRET": "s", "GOOGLE_ADS_REFRESH_TOKEN": "r",
           "GOOGLE_ADS_CUSTOMER_ID": "123-456-7890"}  # fmt: skip
    conn = gads.GoogleAds(old)
    assert conn.configured and conn.default_customer == "1234567890"
    both = gads.GoogleAds({**old, "DATA_GADS_CUSTOMER_ID": "9999999999"})
    assert both.default_customer == "9999999999"  # 정식 이름이 먼저


def test_token_values_never_leave(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **META_ENV, **GADS_ENV)
    _collect(env, Fake())
    text = json.dumps(_read(env).model_dump(mode="json"), ensure_ascii=False)
    text += repr(meta_ads.MetaAds(env)) + repr(gads.GoogleAds(env))
    for secret in (META_TOKEN, DEV, CID, SEC, RT, ACCESS):
        assert secret not in text, secret


def test_no_write_endpoints_in_the_code() -> None:
    """광고 만들기·예산·켜기/끄기·게시 경로가 없다 (경로 문자열 검사)."""
    meta_src = Path(meta_ads.__file__).read_text(encoding="utf-8")
    google_src = Path(gads.__file__).read_text(encoding="utf-8")
    skill_src = Path(ap.__file__).read_text(encoding="utf-8")
    for word in ("mutate", "/campaigns", "/adsets", "/ads\"", "budget", ":upload", '"DELETE"',
                 '"PATCH"', '"PUT"'):  # fmt: skip
        assert all(word not in s for s in (meta_src, google_src, skill_src)), word
    assert '"POST"' not in meta_src and ".post(" not in meta_src  # 메타는 GET만
    assert google_src.count('/googleAds:search"') == 1  # 부르는 주소는 조회 하나
    with pytest.raises(gads.GoogleAdsError):
        asyncio.run(gads.GoogleAds(GADS_ENV).search("1234567890", "UPDATE campaign"))
