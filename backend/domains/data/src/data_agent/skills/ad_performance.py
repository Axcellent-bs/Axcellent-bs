"""ad_performance: 회사 광고 성과 (메타·구글 애즈 캠페인, 연령·성별). 읽기 전용(grade read).

- 수집(하루 1번, `DATA_ADS_EVERY_HOURS` 기본 24): 1시간 수집기가 회사 설정마다 `[ads]
  meta_ad_account_id`·`google_customer_id`(구글은 없으면 env `DATA_GADS_CUSTOMER_ID`)로 최근 7일
  캠페인 값과 연령·성별을 읽어 `ad_performance.db`(`DATA_ADS_DB`)에 저장한다. skill·화면은 저장분만
- 계정 번호가 없으면 그 플랫폼은 건너뛴다(부르지 않음). env가 없으면 unknown + "연결 대기" +
  필요한 것(env 이름만, 값·일부 글자 없음)
- 값은 받은 그대로(구글 cost_micros ÷ 1e6만). 연령·성별 비중만 코드가 계산한다
- 광고 ↔ 영상: 캠페인 이름에 등록 영상의 영상 id나 캠페인 번호가 있으면 `video_key`, 못 이으면 비움
- 광고 만들기·예산·켜기/끄기·게시는 없다 (커넥터가 조회만 보낸다)
"""

from __future__ import annotations

import sqlite3
import threading
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.ad_performance import (
    AdAudience,
    AdCampaign,
    AdPerformanceOutput,
    AdPerformanceRequest,
    AdPlatform,
    AdPlatformStatus,
)
from ax_contracts.schemas.data import DataFact
from data_agent import company_scope
from data_agent.connectors import ConnectorUnavailable, meta_ads
from data_agent.connectors import google_ads as gads

SCHEMA_VERSION = "ad_performance.v1"
DB_ENV = "DATA_ADS_DB"
DEFAULT_DB = Path(__file__).resolve().parents[5] / ".data" / "ad_performance.db"
EVERY_ENV, DEFAULT_EVERY_HOURS = "DATA_ADS_EVERY_HOURS", 24
DAYS = 7  # 수집 기간 (최근 7일)
KST = timezone(timedelta(hours=9))
WAITING = "연결 대기"
NOT_COLLECTED = "아직 수집 전 (하루 1번 수집기)"
NO_META_ACCOUNT = "회사 설정에 메타 광고 계정 번호가 없음 ([ads] meta_ad_account_id)"
NO_GOOGLE_ACCOUNT = "회사 설정에 구글 애즈 고객 번호가 없음 ([ads] google_customer_id)"
NEEDS_META = ["서버 env DATA_META_ADS_TOKEN (ads_read 권한)", "회사 설정 [ads] meta_ad_account_id"]
NEEDS_GOOGLE = [
    *[f"서버 env {names[0]} (옛 이름 {names[1]})" for names in gads.KEY_ENVS],
    "회사 설정 [ads] google_customer_id (또는 env DATA_GADS_CUSTOMER_ID)",
]
GAQL_CAMPAIGN = (
    "SELECT campaign.id, campaign.name, customer.currency_code, metrics.impressions,"
    " metrics.clicks, metrics.ctr, metrics.cost_micros, metrics.video_trueview_views,"
    " metrics.video_trueview_view_rate, metrics.conversions FROM campaign"
    " WHERE segments.date BETWEEN '{since}' AND '{until}'"
)
GAQL_AGE = (
    "SELECT ad_group_criterion.age_range.type, metrics.impressions,"
    " metrics.video_trueview_views FROM age_range_view"
    " WHERE segments.date BETWEEN '{since}' AND '{until}'"
)
GAQL_GENDER = (
    "SELECT ad_group_criterion.gender.type, metrics.impressions,"
    " metrics.video_trueview_views FROM gender_view"
    " WHERE segments.date BETWEEN '{since}' AND '{until}'"
)


# ---------------------------------------------------------------- 저장


class AdStore:
    """(회사, 플랫폼) → 마지막 수집 결과 (상태·캠페인·연령 성별, 계약 JSON)."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS ad_snapshots (company TEXT NOT NULL,"
                " platform TEXT NOT NULL, days INTEGER NOT NULL, payload TEXT NOT NULL,"
                " collected_at TEXT NOT NULL, PRIMARY KEY (company, platform))"
            )

    def close(self) -> None:
        self._conn.close()

    def save(self, company: str, platform: str, days: int, out: AdPerformanceOutput,
             at: datetime) -> None:  # fmt: skip
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO ad_snapshots VALUES (?,?,?,?,?)",
                               (company, platform, days, out.model_dump_json(), at.isoformat()))

    def get(self, company: str, platform: str) -> tuple[int, AdPerformanceOutput, datetime] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT days, payload, collected_at FROM ad_snapshots"
                " WHERE company = ? AND platform = ?", (company, platform),
            ).fetchone()  # fmt: skip
        if not row:
            return None
        return int(row[0]), AdPerformanceOutput.model_validate_json(row[1]), \
            datetime.fromisoformat(row[2])


def store_of(env: Mapping[str, str]) -> AdStore:
    return AdStore(env.get(DB_ENV) or DEFAULT_DB)


# ---------------------------------------------------------------- 회사 설정


def ads_ids(company: str) -> dict[str, str]:
    """회사 설정 `[ads]` {google_customer_id, meta_ad_account_id} (없으면 빈 글)."""
    from ax_agent_template import tenant

    out = {"google_customer_id": "", "meta_ad_account_id": ""}
    if not company or not tenant.is_tenant(company):
        return out
    try:
        with (tenant.tenants_dir() / company / tenant.TENANT_FILE).open("rb") as f:
            section = tomllib.load(f).get("ads")
    except (OSError, tomllib.TOMLDecodeError):
        return out
    for key in out:
        value = section.get(key) if isinstance(section, dict) else None
        out[key] = value.strip() if isinstance(value, str) else ""
    return out


def google_customer(company: str, env: Mapping[str, str]) -> str:
    """회사 설정이 먼저, 없으면 env (정식·옛 이름). 10자리가 아니면 ""."""
    return gads.customer_id(ads_ids(company)["google_customer_id"]
                            or gads.env_value(env, gads.CUSTOMER_ENV))  # fmt: skip


def period(now: datetime, days: int = DAYS) -> tuple[date, date]:
    """(시작, 끝) 한국 날짜: 끝은 어제, 시작은 끝 − (days − 1)."""
    until = now.astimezone(KST).date() - timedelta(days=1)
    return until - timedelta(days=days - 1), until


# ---------------------------------------------------------------- 값 (코드)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None and n >= 0 else None


def _actions(v: Any) -> int | None:
    """메타 영상 값 [{action_type, value}] → 합계. 목록이 없으면 None."""
    if not isinstance(v, list):
        return None
    got = [_int(a.get("value")) for a in v if isinstance(a, dict)]
    known = [x for x in got if x is not None]
    return sum(known) if known else None


def meta_campaign(r: Mapping[str, Any]) -> AdCampaign | None:
    cid = str(r.get("campaign_id") or "")
    if not cid:
        return None
    return AdCampaign(
        platform="meta", campaign_id=cid[:64], name=str(r.get("campaign_name") or "")[:200],
        cost=_num(r.get("spend")), currency=(str(r["account_currency"])[:8]
                                             if r.get("account_currency") else None),
        impressions=_int(r.get("impressions")), reach=_int(r.get("reach")),
        clicks=_int(r.get("clicks")), ctr=_num(r.get("ctr")), ctr_unit="percent",
        views=_actions(r.get("video_play_actions")),
        thruplays=_actions(r.get("video_thruplay_watched_actions")),
        p25=_actions(r.get("video_p25_watched_actions")),
        p50=_actions(r.get("video_p50_watched_actions")),
        p75=_actions(r.get("video_p75_watched_actions")),
        p100=_actions(r.get("video_p100_watched_actions")),
    )  # fmt: skip


def google_campaign(r: Mapping[str, Any]) -> AdCampaign | None:
    camp = r.get("campaign") if isinstance(r.get("campaign"), dict) else {}
    m = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
    cust = r.get("customer") if isinstance(r.get("customer"), dict) else {}
    cid = str(camp.get("id") or "")
    if not cid:
        return None
    micros = _num(m.get("costMicros"))
    return AdCampaign(
        platform="google", campaign_id=cid[:64], name=str(camp.get("name") or "")[:200],
        cost=round(micros / 1e6, 6) if micros is not None and micros >= 0 else None,
        currency=str(cust["currencyCode"])[:8] if cust.get("currencyCode") else None,
        impressions=_int(m.get("impressions")), clicks=_int(m.get("clicks")),
        ctr=_num(m.get("ctr")), ctr_unit="ratio", views=_int(m.get("videoTrueviewViews")),
        view_rate=_num(m.get("videoTrueviewViewRate")), conversions=_num(m.get("conversions")),
    )  # fmt: skip


def shares_of(rows: list[AdAudience]) -> list[AdAudience]:
    """플랫폼 합계 대비 노출·조회 비중 (코드). 합계가 0·모름이면 비중 없음."""
    imp = sum(r.impressions or 0 for r in rows)
    views = sum(r.views or 0 for r in rows)
    for r in rows:
        r.impressions_share = round(r.impressions / imp, 4) if imp and r.impressions is not None \
            else None
        r.views_share = round(r.views / views, 4) if views and r.views is not None else None
    return rows


def meta_audience(rows: Sequence[Mapping[str, Any]]) -> list[AdAudience]:
    """연령×성별 칸 (같은 칸이 캠페인마다 오면 합친다)."""
    cells: dict[tuple[str, str], list[int | None]] = {}
    for r in rows:
        key = (str(r.get("age") or "")[:30], str(r.get("gender") or "")[:30])
        imp, views = _int(r.get("impressions")), _actions(r.get("video_play_actions"))
        old = cells.setdefault(key, [None, None])
        old[0] = imp if old[0] is None else old[0] + (imp or 0)
        old[1] = views if old[1] is None else old[1] + (views or 0)
    return shares_of([AdAudience(platform="meta", age=a, gender=g, impressions=i, views=v)
                      for (a, g), (i, v) in cells.items()])  # fmt: skip


def google_audience(ages: Sequence[Mapping[str, Any]],
                    genders: Sequence[Mapping[str, Any]]) -> list[AdAudience]:  # fmt: skip
    """연령 줄(성별 빈칸)과 성별 줄(연령 빈칸). 광고 그룹마다 오는 줄을 종류별로 합친다."""
    out: list[AdAudience] = []
    for rows, field, is_age in ((ages, "ageRange", True), (genders, "gender", False)):
        cells: dict[str, list[int]] = {}
        for r in rows:
            crit = r.get("adGroupCriterion") if isinstance(r.get("adGroupCriterion"), dict) else {}
            kind = crit.get(field) if isinstance(crit.get(field), dict) else {}
            name = str(kind.get("type") or "")[:30]
            m = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
            if not name:
                continue
            cell = cells.setdefault(name, [0, 0])
            cell[0] += _int(m.get("impressions")) or 0
            cell[1] += _int(m.get("videoTrueviewViews")) or 0
        rows_out = [AdAudience(platform="google", age=n if is_age else "",
                               gender="" if is_age else n, impressions=i, views=v)
                    for n, (i, v) in cells.items()]  # fmt: skip
        out += shares_of(rows_out)
    return out


def link_videos(campaigns: list[AdCampaign], videos: Sequence[Any]) -> None:
    """캠페인 이름에 등록 영상의 영상 id나 캠페인 번호가 있으면 video_key (못 이으면 None)."""
    for c in campaigns:
        for v in videos:
            marks = [m for m in (v.video_id, v.campaign_id) if m and len(m) >= 6]
            if any(m in c.name for m in marks):
                c.video_key = v.key
                break


def facts_of(company: str, out: AdPerformanceOutput) -> list[DataFact]:
    from data_agent.skills.video_performance import fact_id

    rows: list[DataFact] = []
    for c in out.campaigns:
        for label, value, unit in (("비용", c.cost, c.currency or "통화 모름"),
                                   ("노출", c.impressions, "회"), ("도달", c.reach, "명"),
                                   ("클릭", c.clicks, "회"), ("조회", c.views, "회"),
                                   ("전환", c.conversions, "건")):  # fmt: skip
            if value is not None:
                lab = f"{c.platform} {c.name or c.campaign_id} {label}"
                rows.append(DataFact(fact_id=fact_id(f"{company}:{c.campaign_id}", lab),
                                     label=lab[:200], value=float(value), unit=unit,
                                     source=f"{c.platform}_ads", period=out.period,
                                     about=c.video_key or c.campaign_id))  # fmt: skip
    return rows


# ---------------------------------------------------------------- 수집 (하루 1번)


@dataclass
class CollectResult:
    saved: int = 0
    problems: list[str] | None = None

    def line(self) -> str:
        tail = f", {'; '.join(self.problems)}" if self.problems else ""
        return f"[ads] 광고 성과 {self.saved}개{tail}"


async def _meta(conn: meta_ads.MetaAds, account: str, since: str, until: str
                ) -> tuple[list[AdCampaign], list[AdAudience]]:  # fmt: skip
    rows = await conn.insights(account, since, until, meta_ads.CAMPAIGN_FIELDS)
    camps = [c for c in (meta_campaign(r) for r in rows) if c is not None]
    aud = await conn.insights(account, since, until, meta_ads.AUDIENCE_FIELDS,
                              breakdowns=("age", "gender"))  # fmt: skip
    return camps, meta_audience(aud)


async def _google(conn: gads.GoogleAds, customer: str, since: str, until: str
                  ) -> tuple[list[AdCampaign], list[AdAudience]]:  # fmt: skip
    q = {"since": since, "until": until}
    rows = await conn.search(customer, GAQL_CAMPAIGN.format(**q))
    camps = [c for c in (google_campaign(r) for r in rows) if c is not None]
    ages = await conn.search(customer, GAQL_AGE.format(**q))
    genders = await conn.search(customer, GAQL_GENDER.format(**q))
    return camps, google_audience(ages, genders)


async def collect_due(
    store: AdStore, env: Mapping[str, str], *, meta: meta_ads.MetaAds | None = None,
    google: gads.GoogleAds | None = None, companies: Sequence[str] | None = None,
    now: datetime | None = None,
) -> CollectResult:  # fmt: skip
    """회사마다 플랫폼마다 마지막 수집에서 `DATA_ADS_EVERY_HOURS`가 지났으면 읽어 저장한다."""
    from ax_agent_template import tenant
    from data_agent.connectors.naver_searchad import limit_of

    now = now or datetime.now(UTC)
    result = CollectResult(problems=[])
    gap = timedelta(hours=limit_of(env, EVERY_ENV, DEFAULT_EVERY_HOURS))
    since_d, until_d = period(now)
    since, until = since_d.isoformat(), until_d.isoformat()
    names = list(companies) if companies is not None else [t["id"] for t in tenant.list_tenants()]
    for company in names:
        ids = ads_ids(company)
        jobs: list[tuple[AdPlatform, str, Any]] = []
        if meta is not None and meta.configured and ids["meta_ad_account_id"]:
            jobs.append(("meta", ids["meta_ad_account_id"], meta))
        customer = google_customer(company, env)
        if google is not None and google.configured and customer:
            jobs.append(("google", customer, google))
        for platform, account, conn in jobs:
            last = store.get(company, platform)
            if last is not None and now - last[2] < gap:
                continue
            out = AdPerformanceOutput(company=company, days=DAYS, period=f"{since}~{until}",
                                      as_of=until_d)  # fmt: skip
            status = AdPlatformStatus(platform=platform, as_of=until_d, collected_at=now)
            try:
                read = _meta if platform == "meta" else _google
                out.campaigns, out.audience = await read(conn, account, since, until)
                status.status = "known"
                if not out.campaigns:
                    status.reason = "이 기간에 값이 있는 캠페인이 없음"
            except ConnectorUnavailable as error:
                status.reason = f"광고 성과를 읽지 못함 [{error.code}]"
                result.problems = [*(result.problems or []), f"{company} {platform} [{error.code}]"]
            with company_scope.using(company):
                link_videos(out.campaigns, _videos(env))
            out.platforms = [status]
            out.status = status.status
            store.save(company, platform, DAYS, out, now)
            result.saved += 1
    return result


def _videos(env: Mapping[str, str]) -> list[Any]:
    """그 회사 등록 영상 (video_performance). 저장 파일이 없으면 []."""
    from data_agent.skills import video_performance as vp

    path = env.get(vp.DB_ENV) or vp.DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return []
    store = vp.VideoStore(path)
    try:
        return store.videos()
    finally:
        store.close()


# ---------------------------------------------------------------- skill (저장분만)


@dataclass
class AdResult:
    output: AdPerformanceOutput
    evidence: Evidence


def _platform_view(platform: AdPlatform, company: str, env: Mapping[str, str], store: AdStore,
                   days: int) -> tuple[AdPlatformStatus, AdPerformanceOutput | None]:  # fmt: skip
    if platform == "meta":
        lack = [] if (env.get(meta_ads.TOKEN_ENV) or "").strip() else [meta_ads.TOKEN_ENV]
        account, no_account, needs = ads_ids(company)["meta_ad_account_id"], NO_META_ACCOUNT, \
            NEEDS_META
    else:
        lack = gads.missing(env)
        account, no_account, needs = google_customer(company, env), NO_GOOGLE_ACCOUNT, NEEDS_GOOGLE
    if lack:
        return AdPlatformStatus(platform=platform, reason=f"{WAITING}: {', '.join(lack)} 없음",
                                needs=list(needs)), None  # fmt: skip
    if not account:
        return AdPlatformStatus(platform=platform, reason=no_account, needs=[needs[-1]]), None
    saved = store.get(company, platform)
    if saved is None:
        return AdPlatformStatus(platform=platform, reason=NOT_COLLECTED), None
    stored_days, out, _ = saved
    st = out.platforms[0] if out.platforms else AdPlatformStatus(platform=platform)
    if stored_days != days:
        st = st.model_copy(update={"status": "unknown",
                                   "reason": f"저장분은 최근 {stored_days}일만 (요청 {days}일)"})
        return st, None
    return st, out


def ad_performance(
    request: AdPerformanceRequest, *, env: Mapping[str, str], store: AdStore | None = None,
    now: datetime | None = None,
) -> AdResult:  # fmt: skip
    """저장분만 읽는다 (외부 호출 없음)."""
    now = now or datetime.now(UTC)
    own = store is None
    store = store or store_of(env)
    company = request.company or company_scope.active()
    platforms: tuple[AdPlatform, ...] = ("meta", "google")
    if request.platform == "meta":
        platforms = ("meta",)
    elif request.platform == "google":
        platforms = ("google",)
    out = AdPerformanceOutput(company=company, days=request.days)
    try:
        for p in platforms:
            st, saved = _platform_view(p, company, env, store, request.days)
            out.platforms.append(st)
            if saved is not None:
                out.campaigns += saved.campaigns
                out.audience += saved.audience
                out.period, out.as_of = saved.period, saved.as_of
    finally:
        if own:
            store.close()
    out.status = "known" if any(s.status == "known" for s in out.platforms) else "unknown"
    out.reason = None if out.status == "known" else "; ".join(
        f"{s.platform} {s.reason}" for s in out.platforms if s.reason)[:200] or None
    out.facts = facts_of(company, out)
    out.summary = (f"캠페인 {len(out.campaigns)}개 ({out.period})" if out.status == "known"
                   else f"광고 성과 없음: {out.reason or ''}")[:300]  # fmt: skip
    return AdResult(out, Evidence(queries=["ad_performance.db ad_snapshots (저장분)"],
                                  data_summary=out.summary, collected_at=now))
