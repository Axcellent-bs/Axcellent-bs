"""고객 아이디어 일일 보고 받기·보관·기간별 합산 (skill `receive_idea_report`, `list_idea_reports`).
프레임워크에 의존하지 않는다.

- 고객지원이 하루치 이벤트·혜택 요청(같은 뜻끼리 묶은 순위)을 보내면 데이터가 받아 둔다. 받아
  보관하고 합산해 보여 줄 뿐 이벤트를 열거나 회사 상태를 바꾸지 않는다 (데이터 쪽 기록만).
- 같은 (`tenant`, `report_date`)는 한 번만 받는다. 다시 오면 `duplicate`이고 보관본은 바뀌지 않는다.
- 기간별 보기(`week`·`month`·`year`)는 보관한 일일 보고를 코드가 더해 만든다. 값은 받은 숫자의
  합이고 추정하지 않는다. 날마다 대표 말이 조금 달라질 수 있어, 말 조각이 크게 겹치는 요청은
  같은 요청으로 합친다.
- 저장소는 SQLite다. `DATA_IDEA_REPORTS_DB`가 가리키는 파일에 두면 다시 시작해도 남고,
  비어 있으면 메모리라 다시 시작할 때 사라진다 (테스트·로컬 기본). 데이터 전용 파일이고
  다른 부서 DB는 쓰지 않는다.

거절 이유 코드(`reason_code`, 닫힌 집합):
- `unsupported_schema_version`: 데이터가 읽을 수 없는 `schema_version`
- `future_date`: 보고일이 오늘(회사 시간대)보다 뒤
- `inconsistent_counts`: 묶음 건수 합이 `total_ideas`보다 크다
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta

from pydantic import Field, ValidationError

from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.idea import (
    IDEA_REPORT_SCHEMA_VERSION,
    IdeaCluster,
    IdeaPeriod,
    IdeaReportInput,
    IdeaReportOutput,
    IdeaTrendBucket,
    IdeaTrendList,
    IdeaTrendRow,
)

DB_ENV = "DATA_IDEA_REPORTS_DB"
SUPPORTED_SCHEMA_VERSIONS = frozenset({IDEA_REPORT_SCHEMA_VERSION})
MERGE_OVERLAP = 0.6  # 말 조각이 이만큼 겹치면 같은 요청으로 본다 (Jaccard)
BUCKETS_DEFAULT = 8
BUCKETS_MAX = 60
TOP_DEFAULT = 5
TOP_MAX = 20


class ListIdeaReportsInput(AxModel):
    period: IdeaPeriod = "week"
    buckets: int = Field(default=BUCKETS_DEFAULT, ge=1, le=BUCKETS_MAX)
    top: int = Field(default=TOP_DEFAULT, ge=1, le=TOP_MAX)


def rejection_reason(report: IdeaReportInput, today: date) -> str | None:
    if report.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return "unsupported_schema_version"
    if report.report_date > today:
        return "future_date"
    if sum(c.count for c in report.clusters) > report.total_ideas:
        return "inconsistent_counts"
    return None


def period_start(day: date, period: IdeaPeriod) -> date:
    if period == "day":
        return day
    if period == "week":
        return day - timedelta(days=day.weekday())  # 월요일
    if period == "month":
        return day.replace(day=1)
    return day.replace(month=1, day=1)


def period_end(start: date, period: IdeaPeriod) -> date:
    if period == "day":
        return start
    if period == "week":
        return start + timedelta(days=6)
    if period == "month":
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt - timedelta(days=1)
    return start.replace(month=12, day=31)


def _words(key: str) -> frozenset[str]:
    return frozenset(key.split())


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def merge_clusters(days: list[IdeaReportInput]) -> list[IdeaTrendRow]:
    """여러 날의 묶음을 더한다. 열쇠가 같거나 말 조각이 크게 겹치면 같은 요청이다.

    많이 나온 요청부터 대표(가장 많이 나온 날의 말)로 삼고, 나머지를 거기에 합친다.
    """
    flat: list[tuple[date, IdeaCluster]] = [(d.report_date, c) for d in days for c in d.clusters]
    flat.sort(key=lambda p: (-p[1].count, p[1].key, p[0]))
    rows: list[dict] = []
    for day, cluster in flat:
        words = _words(cluster.key)
        target = next(
            (
                r
                for r in rows
                if r["key"] == cluster.key or _overlap(r["words"], words) >= MERGE_OVERLAP
            ),
            None,
        )
        if target is None:
            rows.append(
                {
                    "key": cluster.key,
                    "label": cluster.label,
                    "words": words,
                    "count": 0,
                    "customers": 0,
                    "days": set(),
                }
            )
            target = rows[-1]
        target["count"] += cluster.count
        target["customers"] += cluster.customer_count
        target["days"].add(day)
    rows.sort(key=lambda r: (-r["count"], -len(r["days"]), r["key"]))
    return [
        IdeaTrendRow(
            key=r["key"],
            label=r["label"],
            count=r["count"],
            customer_count=r["customers"],
            days=len(r["days"]),
        )
        for r in rows
    ]


def build_trends(
    reports: list[IdeaReportInput], period: IdeaPeriod, buckets: int, top: int
) -> IdeaTrendList:
    if not reports:
        return IdeaTrendList(period=period, unknown_reason="받은 일일 보고가 없음")
    grouped: dict[date, list[IdeaReportInput]] = defaultdict(list)
    for report in reports:
        grouped[period_start(report.report_date, period)].append(report)
    out: list[IdeaTrendBucket] = []
    for start in sorted(grouped, reverse=True)[:buckets]:
        days = grouped[start]
        out.append(
            IdeaTrendBucket(
                period=period,
                start=start,
                end=period_end(start, period),
                report_days=len({d.report_date for d in days}),
                total_ideas=sum(d.total_ideas for d in days),
                top=merge_clusters(days)[:top],
            )
        )
    return IdeaTrendList(period=period, buckets=out)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS idea_reports (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT NOT NULL,
    report_date TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload     TEXT NOT NULL,
    UNIQUE (tenant_id, report_date)
);
"""
_log = logging.getLogger(__name__)


class IdeaReportStorageError(Exception):
    """저장소를 읽거나 쓰지 못했다. 받은 보고는 저장되지 않았다."""


class IdeaReportStore:
    """받은 일일 보고 저장소. `path`가 없으면 `DATA_IDEA_REPORTS_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute(_SCHEMA)
        except sqlite3.Error as error:
            raise IdeaReportStorageError(
                f"고객 아이디어 보고 저장소를 열 수 없다: {type(error).__name__}"
            ) from error

    def close(self) -> None:
        self._conn.close()

    def reports(self, tenant_id: str) -> list[IdeaReportInput]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT report_date, payload FROM idea_reports WHERE tenant_id = ?"
                    " ORDER BY report_date",
                    (tenant_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise IdeaReportStorageError(
                f"고객 아이디어 보고를 읽을 수 없다: {type(error).__name__}"
            ) from error
        found: list[IdeaReportInput] = []
        for report_date, payload in rows:
            try:
                found.append(IdeaReportInput.model_validate_json(payload))
            except ValidationError:  # 계약이 바뀌어 읽을 수 없는 줄은 건너뛴다
                _log.warning("고객 아이디어 보고 %s를 읽을 수 없어 건너뜀", report_date)
        return found

    def has(self, tenant_id: str, report_date: date) -> bool:
        return any(r.report_date == report_date for r in self.reports(tenant_id))

    def add(self, tenant_id: str, report: IdeaReportInput, at: datetime) -> IdeaReportOutput:
        """관문이 `executed`를 돌려준 뒤에만 부른다. 이미 있으면 보관본을 그대로 두고 duplicate."""
        if self.has(tenant_id, report.report_date):
            return IdeaReportOutput(outcome="duplicate", report_date=report.report_date)
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO idea_reports (tenant_id, report_date, received_at, payload)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        tenant_id,
                        report.report_date.isoformat(),
                        at.isoformat(),
                        report.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError:  # 동시에 같은 날 보고가 들어왔다. 먼저 들어간 것이 이긴다
            return IdeaReportOutput(outcome="duplicate", report_date=report.report_date)
        except sqlite3.Error as error:
            raise IdeaReportStorageError(
                f"고객 아이디어 보고를 저장할 수 없다: {type(error).__name__}"
            ) from error
        return IdeaReportOutput(outcome="accepted", report_date=report.report_date)

    def trends(self, tenant_id: str, query: ListIdeaReportsInput) -> IdeaTrendList:
        return build_trends(self.reports(tenant_id), query.period, query.buckets, query.top)
