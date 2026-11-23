"""캠페인별 광고 숫자 저장·검증·합계 (docs/marketing-ad-metrics-design.md B, 2026-11-23).

- 사람이 적은 값을 그대로 둔다. `None` = 모름, 0 = 0. 비율은 계산하지 않는다(데이터분석이 한다)
- 같은 (캠페인, 날짜, source, medium)을 다시 적으면 고쳐 쓰고,
  이전 값은 `ad_metrics_revisions`에 남긴다. 값이 같으면 `unchanged`(멱등)
- 저장은 `MARKETING_AD_METRICS_DB`(run_all.sh는 `.data/marketing_ad_metrics.db`), 없으면 메모리
- 회사 칸(`tenant_id`)은 다른 마케팅 저장소와 같은 규칙(지금 회사, 기본 캣북스)
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from ax_contracts.schemas.ad_metrics import (
    AdMetricsRow,
    AdMetricsTotals,
    GetAdMetricsInput,
    RecordAdMetricsInput,
)
from marketing_agent import company_scope
from marketing_agent.links import SOURCE_MEDIUMS

DB_ENV = "MARKETING_AD_METRICS_DB"
BASE_URL_ENV = "MARKETING_LINK_BASE_URL"  # 추적 링크 기본 주소(설정값, 화면에서 바꿀 수 있다)
KST = ZoneInfo("Asia/Seoul")
FIELDS = ("impressions", "clicks", "spend_krw")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_metrics (
    tenant_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    date TEXT NOT NULL,
    source TEXT NOT NULL,
    medium TEXT NOT NULL,
    impressions INTEGER,
    clicks INTEGER,
    spend_krw INTEGER,
    report_ref TEXT,
    entered_by TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revisions INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, campaign_id, date, source, medium)
);
CREATE TABLE IF NOT EXISTS ad_metrics_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    date TEXT NOT NULL,
    source TEXT NOT NULL,
    medium TEXT NOT NULL,
    before TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
"""
_COLUMNS = (
    "campaign_id, date, source, medium, impressions, clicks, spend_krw, report_ref,"
    " entered_by, entered_at, updated_at, revisions"
)


class AdMetricsStorageError(Exception):
    """파일을 읽거나 쓰지 못했다."""


def today_kst() -> date:
    return datetime.now(KST).date()


def combos() -> dict[str, list[str]]:
    return {source: list(mediums) for source, mediums in SOURCE_MEDIUMS.items()}


def default_base_url() -> str | None:
    return os.getenv(BASE_URL_ENV, "").strip() or None


def rejection(data: RecordAdMetricsInput, today: date | None = None) -> str | None:
    """적을 수 없는 이유 코드(캠페인 확인은 호출하는 쪽). 적을 수 있으면 None."""
    try:
        day = date.fromisoformat(data.date)
    except ValueError:
        return "invalid_date"
    if day > (today or today_kst()):
        return "future_date"
    if data.medium not in SOURCE_MEDIUMS.get(data.source, ()):
        return "invalid_source_medium"
    if data.impressions is not None and data.clicks is not None:
        if data.clicks > data.impressions:
            return "clicks_over_impressions"
    return None


def _row(values: tuple) -> AdMetricsRow:
    return AdMetricsRow(**dict(zip(_COLUMNS.replace(" ", "").split(","), values, strict=True)))


class AdMetricsStore:
    """`path`가 없으면 `MARKETING_AD_METRICS_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None, company_id: str | None = None) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self.company_id = company_scope.scope_id(company_id)
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error as error:
            raise AdMetricsStorageError(str(error)) from error

    def record(self, data: RecordAdMetricsInput) -> tuple[str, AdMetricsRow]:
        """적거나 고쳐 쓴다. (outcome, 저장된 줄). outcome: recorded·updated·unchanged."""
        key = (self.company_id, data.campaign_id, data.date, data.source, data.medium)
        now = datetime.now(UTC).isoformat()
        try:
            with self._lock, self._conn:
                old = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM ad_metrics WHERE tenant_id = ? AND campaign_id = ?"  # noqa: S608
                    " AND date = ? AND source = ? AND medium = ?",
                    key,
                ).fetchone()
                values = (data.impressions, data.clicks, data.spend_krw, data.report_ref)
                if old is None:
                    self._conn.execute(
                        "INSERT INTO ad_metrics (tenant_id, campaign_id, date, source, medium,"
                        " impressions, clicks, spend_krw, report_ref, entered_by, entered_at,"
                        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (*key, *values, data.entered_by, now, now),
                    )
                    outcome = "recorded"
                else:
                    before = _row(old)
                    same = (
                        before.impressions,
                        before.clicks,
                        before.spend_krw,
                        before.report_ref,
                    ) == values
                    if same:
                        return "unchanged", before
                    self._conn.execute(
                        "INSERT INTO ad_metrics_revisions (tenant_id, campaign_id, date, source,"
                        " medium, before, changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (*key, before.model_dump_json(), data.entered_by, now),
                    )
                    self._conn.execute(
                        "UPDATE ad_metrics SET impressions = ?, clicks = ?, spend_krw = ?,"
                        " report_ref = ?, updated_at = ?, revisions = revisions + 1"
                        " WHERE tenant_id = ? AND campaign_id = ? AND date = ? AND source = ?"
                        " AND medium = ?",
                        (*values, now, *key),
                    )
                    outcome = "updated"
                saved = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM ad_metrics WHERE tenant_id = ? AND campaign_id = ?"  # noqa: S608
                    " AND date = ? AND source = ? AND medium = ?",
                    key,
                ).fetchone()
        except sqlite3.Error as error:
            raise AdMetricsStorageError(str(error)) from error
        return outcome, _row(saved)

    def query(self, ask: GetAdMetricsInput) -> list[AdMetricsRow]:
        """원본 줄(날짜·source·medium 순). 조건이 없으면 이 회사의 모든 줄."""
        where, args = ["tenant_id = ?"], [self.company_id]
        if ask.campaign_id:
            where.append("campaign_id = ?")
            args.append(ask.campaign_id)
        if ask.date_from:
            where.append("date >= ?")
            args.append(ask.date_from)
        if ask.date_to:
            where.append("date <= ?")
            args.append(ask.date_to)
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM ad_metrics WHERE {' AND '.join(where)}"  # noqa: S608
                    " ORDER BY date, source, medium, campaign_id",
                    args,
                ).fetchall()
        except sqlite3.Error as error:
            raise AdMetricsStorageError(str(error)) from error
        return [_row(r) for r in rows]

    def revisions(self, campaign_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT date, source, medium, before, changed_by, changed_at"
                " FROM ad_metrics_revisions WHERE tenant_id = ? AND campaign_id = ? ORDER BY id",
                (self.company_id, campaign_id),
            ).fetchall()
        return [
            {
                "date": d,
                "source": s,
                "medium": m,
                "before": json.loads(b),
                "changed_by": by,
                "changed_at": at,
            }
            for d, s, m, b, by, at in rows
        ]


def totals(rows: list[AdMetricsRow]) -> AdMetricsTotals:
    """칸별 합계. 0은 더하고, `None`(모름)이 하나라도 있으면 그 칸 합계는 None + 빈 날짜."""
    sums: dict[str, int | None] = {}
    missing: dict[str, list[str]] = {}
    for name in FIELDS:
        values = [getattr(r, name) for r in rows]
        gaps = sorted({r.date for r in rows if getattr(r, name) is None})
        if gaps:
            missing[name] = gaps
            sums[name] = None
        else:
            sums[name] = sum(v for v in values if v is not None)
    return AdMetricsTotals(**sums, missing_dates=missing)
