"""지난 날짜 비교 (3단계 B, 11/25): `sales.db`로 지난 N일 집계를 다시 계산해 분석별로 견준다.

하루씩 쌓이는 병행 비교(3-1b)를 기다리지 않고, 이미 있는 원본으로 지난 기준일들을 바로 확인한다.

- 원본 `sales.db`는 읽기 전용으로 열어 임시 폴더에 복사본을 만든다. 계산·비교는 그 복사본과
  임시 `aggregates.db`에서만 한다(운영 `aggregates.db`·`sales.db`에 쓰지 않는다).
- 일별 행: 첫 채우기(`backfill`)와 같다. 기준일 스냅샷: 지난 N일 안에서 `every`일마다 하나씩
  (`snapshot_day`, 하루 계산과 같은 함수).
- 견주는 것(기준일마다): 원본 경로(스위치 꺼짐, 기간을 스냅샷과 같은 꽉 찬 N일로) ↔ 집계 경로
  (`DATA_SALES_READ=aggregate`). 매출 분석은 `compare.compare_day`(7·30일 합·고객 구성)와
  화면 출력(다음 날 09시, 오늘 몫은 원본), 고객 분석은 skill 출력의 숫자 칸.
- 집계가 답하지 못해 원본으로 간 경우(요약에 "기준"이 없음)는 "원본으로 감"으로 따로 센다.
- 출력에는 분석 이름·기준일·다른 칸 이름만 쓴다. 구매자 id·주문 id는 없다.
"""

from __future__ import annotations

import itertools
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from ax_contracts.schemas.data import MetricCatalog
from data_agent.aggregate import compare, read
from data_agent.aggregate import read_buyers as rb
from data_agent.aggregate.backfill import backfill, snapshot_day
from data_agent.aggregate.build_day import day_window
from data_agent.aggregate.store import DB_ENV, AggregateStore
from data_agent.sales_quality import kst_day
from data_agent.sales_store import SalesStore
from data_agent.skills import basket_pairs, channel_value, event_proposal, retention
from data_agent.skills import segment_analysis as seg
from data_agent.skills.sales_analysis import analyze

DIFFS_SHOWN = 3


@dataclass
class Tally:
    same: int = 0
    different: int = 0
    fallback: int = 0  # 집계가 답하지 못해 원본으로 감
    diffs: list[str] = field(default_factory=list)

    def add(self, label: str, ok: bool, *, used_aggregate: bool = True) -> None:
        if not used_aggregate:
            self.fallback += 1
        elif ok:
            self.same += 1
        else:
            self.different += 1
            if len(self.diffs) < DIFFS_SHOWN:
                self.diffs.append(label)


@dataclass
class Report:
    days: list[date]
    analyses: dict[str, Tally] = field(default_factory=dict)

    def tally(self, name: str) -> Tally:
        return self.analyses.setdefault(name, Tally())

    def lines(self) -> list[str]:
        span = f"{self.days[0]}~{self.days[-1]}" if self.days else "없음"
        out = [f"[aggregate] 지난 날짜 비교: 기준일 {len(self.days)}개 ({span})"]
        for name, t in self.analyses.items():
            line = f"[aggregate] {name}: 같음 {t.same} · 다름 {t.different}"
            if t.fallback:
                line += f" · 원본으로 감 {t.fallback}"
            if t.diffs:
                line += " — " + "; ".join(t.diffs)
            out.append(line)
        return out

    @property
    def different(self) -> int:
        return sum(t.different for t in self.analyses.values())


def sample_days(yesterday: date, days: int, every: int) -> list[date]:
    """지난 `days`일 안에서 어제부터 `every`일마다 (오래된 날부터)."""
    out = [yesterday - timedelta(days=k) for k in range(0, days, max(1, every))]
    return sorted(out)


def copy_read_only(path: Path, dst: Path) -> None:
    """원본 DB를 읽기 전용으로 열어 `dst`로 한 시점 복사 (원본에 쓰지 않는다)."""
    src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


@contextmanager
def _source_window(period: str, as_of: date) -> Iterator[None]:
    """원본 경로의 기간을 스냅샷과 같은 꽉 찬 N일로 (설계 2.4: 화면 기간은 N−1일 + 오늘)."""
    w = rb.window_of(period, as_of)  # type: ignore[arg-type]
    same = seg.Window(start=w.start, end=w.end, previous_start=w.start, previous_end=w.start)
    with mock.patch.object(seg, "segment_window", lambda p, now: same):
        yield


@contextmanager
def _aggregate_on(path: Path) -> Iterator[None]:
    with mock.patch.dict(os.environ, {read.READ_ENV: read.READ_AGGREGATE, DB_ENV: str(path)}):
        yield


@contextmanager
def _aggregate_off(path: Path) -> Iterator[None]:
    with mock.patch.dict(os.environ, {read.READ_ENV: "", DB_ENV: str(path)}):
        yield


def _noted(summary: str, as_of: date) -> bool:
    return summary.endswith(rb.note(as_of))


def _pair(  # noqa: PLR0913 - 비교 한 번의 재료
    report: Report,
    name: str,
    label: str,
    period: str | None,
    as_of: date,
    path: Path,
    run: Callable[[datetime], Any],
    pick: Callable[[Any], Any],
    summary: Callable[[Any], str],
) -> None:
    end = day_window(as_of)[1]
    with _aggregate_off(path):
        if period is None:
            want = run(end)
        else:
            with _source_window(period, as_of):
                want = run(end)
    with _aggregate_on(path):
        if period is None:
            got = run(end)
        else:
            with _source_window(period, as_of):  # 원본으로 가더라도 같은 기간
                got = run(end)
    used = _noted(summary(got), as_of)
    report.tally(name).add(f"{as_of} {label}", pick(got) == pick(want), used_aggregate=used)


def _shown_pairs(out: Any) -> tuple:  # noqa: ANN401 - skill 출력
    return (tuple(p for p in out.pairs if not p.suppressed),
            sum(p.suppressed for p in out.pairs))  # fmt: skip


def verify_day(  # noqa: PLR0913 - 저장소·회사·카탈로그·기준일·경로·보고
    sales: SalesStore, tenant_id: str, cat: MetricCatalog, as_of: date, path: Path, report: Report
) -> None:
    cfg = cat.sales
    assert cfg is not None
    agg = AggregateStore(path)
    try:
        for row in compare.compare_day(sales, agg, tenant_id, as_of, day_window(as_of)[1]).rows:
            report.tally(row.target.split(":")[0]).add(
                f"{as_of} {row.target} ({', '.join(d.split(':')[0] for d in row.diffs)})", row.same
            )
        codes = [(r["dim_kind"], r["dim_code"]) for r in agg.buyer_rows(tenant_id, as_of)
                 if r["period"] == "30d" and r["dim_kind"] in ("genre", "category")
                 and not r["attr_cell"] and r["dim_code"] and not r["buyers_hidden"]]  # fmt: skip
    finally:
        agg.close()
    # 매출 분석 화면 출력: 다음 날 09시(한국). 꽉 찬 날은 일별 행, 오늘 몫은 원본
    morning = day_window(as_of)[1] + timedelta(hours=9)
    for period in ("7d", "30d"):
        outs = []
        for ctx in (_aggregate_off(path), _aggregate_on(path)):
            with ctx:
                o = analyze(sales, tenant_id, period, cfg, cat.id, morning).model_dump(mode="json")
            o["basis"].pop("rows_read")
            outs.append(o)
        report.tally("sales_analysis 화면").add(f"{as_of} {period}", outs[0] == outs[1])

    def cv(now: datetime) -> Any:  # noqa: ANN401
        return channel_value.channel_value(sales, tenant_id, "365d", cfg, cat.id, now)

    # 뺀 새 고객 수(근거 문장)는 경로 줄에 함께 저장돼 보이는 줄이 없으면 스냅샷에 없다
    # → 줄이 있을 때만 견준다
    _pair(report, "channel_value", "365d", "365d", as_of, path, cv,
          lambda o: (o[0].rows, o[1] if o[0].rows else None),
          lambda o: o[0].summary)  # fmt: skip
    _pair(report, "retention", "cohorts 6", None, as_of, path,
          lambda now: retention.retention(sales, tenant_id, 6, {}, cfg, cat.id, now),
          lambda o: (o.cohorts, o.at_risk), lambda o: o.summary)  # fmt: skip
    for period, level in itertools.product(("30d", "90d"), ("genre", "product")):
        _pair(report, "basket_pairs", f"{period} {level}", period, as_of, path,
              lambda now, p=period, lv=level: basket_pairs.basket(
                  sales, tenant_id, lv, p, {}, 10, cfg, cat.id, now),
              _shown_pairs, lambda o: o.summary)  # fmt: skip
    for a in cfg.attributes:
        for v in a.values:
            _pair(report, "segment_favorites", f"90d {a.key}", "90d", as_of, path,
                  lambda now, f={a.key: v}: seg.favorites(sales, tenant_id, f, "90d", 10, cfg,
                                                          cat.id, now),
                  lambda o: (o.segment_buyers, o.segment_suppressed, o.internal),
                  lambda o: o.summary)  # fmt: skip
    for kind, code in codes:
        _pair(report, "segment_profile", f"30d {kind}", "30d", as_of, path,
              lambda now, k=kind, c=code: seg.profile(sales, tenant_id, k, c, [], "30d", cfg,
                                                      cat.id, now),
              lambda o: (o.target_buyers, o.target_suppressed, o.overall_buyers, o.attributes),
              lambda o: o.summary)  # fmt: skip
    for pop in cfg.population:
        _pair(report, "coverage_buyers", "365d " + "·".join(pop.attribute_keys), "365d", as_of,
              path, lambda now, ks=list(pop.attribute_keys): seg.coverage_buyers(
                  sales, tenant_id, ks, cfg, cat.id, now),
              lambda o: o.rows, lambda o: o.summary)  # fmt: skip

    def base(now: datetime) -> Any:  # noqa: ANN401
        return event_proposal.read_base(sales, tenant_id, cfg, cat.id, now)

    _pair(report, "event_proposal", "90d 후보", event_proposal.ANALYSIS_PERIOD, as_of, path,
          base, lambda b: b.candidates,
          lambda b: rb.note(b.as_of) if b.as_of else "")  # fmt: skip


def verify(  # noqa: PLR0913 - 원본·회사·카탈로그·시각·기간
    sales_path: Path,
    tenant_id: str,
    cat: MetricCatalog,
    now: datetime,
    days: int = 90,
    every: int = 7,
) -> Report:
    """지난 `days`일을 다시 계산해 분석별로 견준다. 임시 폴더는 끝나면 지운다."""
    tmp = Path(tempfile.mkdtemp(prefix="agg-verify-"))
    try:
        copy = tmp / "sales.db"
        copy_read_only(sales_path, copy)
        path = tmp / "aggregates.db"
        sales = SalesStore(copy)
        try:
            agg = AggregateStore(path)
            try:
                run = backfill(sales, agg, tenant_id, cat.sales, now, run_id="verify")
                if run.status != "done" or cat.sales is None:
                    return Report([])
                _, first, _ = sales.order_span(tenant_id)
                start = kst_day(first) if first else run.as_of
                picked = [d for d in sample_days(run.as_of, days, every) if d >= start]
                for d in picked:
                    if d != run.as_of:
                        snapshot_day(sales, agg, tenant_id, d, cat.sales, run, now)
            finally:
                agg.close()
            report = Report(picked)
            for d in picked:
                verify_day(sales, tenant_id, cat, d, path, report)
            return report
        finally:
            sales.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
