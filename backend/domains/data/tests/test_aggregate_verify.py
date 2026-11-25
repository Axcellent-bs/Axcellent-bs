"""지난 날짜 비교 (3단계 B): 원본으로 지난 날짜 집계를 다시 계산해 분석별로 견준다.

- 가짜 판매 데이터에서 모든 분석이 "다름 0" (같은 정의·같은 기간이면 집계 경로와 원본이 같다)
- 원본 파일은 바뀌지 않고(읽기 전용 복사), 임시 폴더는 남지 않는다
- 출력 줄에 구매자 id가 없다
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from data_agent import catalog
from data_agent.aggregate import verify as v
from data_agent.aggregate.__main__ import main
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_store import DB_ENV as SALES_DB_ENV
from data_agent.sales_store import SalesStore

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00 → 어제 11/15


def _sales(path: Path) -> None:
    store = SalesStore(path)
    src = FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)
    assert asyncio.run(collect_once(src, store, source_name="fake", now=NOW)).errors == []
    store.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_analysis_is_the_same_on_fake_data(tmp_path: Path) -> None:
    path = tmp_path / "sales.db"
    _sales(path)
    before = _digest(path)
    left = set(Path(tempfile.gettempdir()).glob("agg-verify-*"))
    report = v.verify(path, TENANT, catalog.load_catalog("catbooks"), NOW, days=8, every=7)
    assert report.days == [date(2026, 11, 8), date(2026, 11, 15)]
    names = set(report.analyses)
    assert {"sales_analysis", "customer_mix", "sales_analysis 화면", "channel_value", "retention",
            "basket_pairs", "segment_favorites", "segment_profile", "coverage_buyers",
            "event_proposal"} <= names  # fmt: skip
    for name, t in report.analyses.items():
        assert t.different == 0, (name, t.diffs)
    assert sum(t.same for t in report.analyses.values()) > 0
    assert _digest(path) == before  # 원본은 그대로
    assert set(Path(tempfile.gettempdir()).glob("agg-verify-*")) == left  # 임시 폴더 지움
    text = "\n".join(report.lines())
    assert "같음" in text and "fk_b" not in text  # 가짜 구매자 id 모양이 없다


def test_sample_days_and_missing_source(capsys, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    assert v.sample_days(date(2026, 11, 15), 15, 7) == [
        date(2026, 11, 1), date(2026, 11, 8), date(2026, 11, 15)]  # fmt: skip
    monkeypatch.setenv(SALES_DB_ENV, str(tmp_path / "none.db"))
    assert main(["--verify-days", "30"]) == 0
    assert "판매 원본이 없음" in capsys.readouterr().out
