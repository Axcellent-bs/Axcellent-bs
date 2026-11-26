"""집계 비교(verify)·첫 채우기를 회사마다 (11/26, 3-3 켜는 기준: 세 회사 모두 verify 차이 0).

예전에는 `--tenant`로 다른 회사 id를 넣어도 카탈로그는 설치 값(캣북스)으로 계산했다.

- 캣북스(생략·shop-demo·catbooks)는 예전 그대로: 범위 없이 `DATA_SALES_TENANT_ID`, 캣북스 카탈로그
- 다른 회사 `--tenant`면 그 회사 범위에서 그 회사 카탈로그, tenant = 회사 id(수집기와 같은 값)
- `--all`은 캣북스와 회사 연결이 켜진 회사를 차례로, 한 회사 실패가 다른 회사를 막지 않음
- 회사끼리 행이 섞이지 않음(실제 원본으로 verify)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template import company_scope, tenant
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from data_agent import catalog
from data_agent import sales_collect as sc
from data_agent.aggregate import __main__ as cli
from data_agent.aggregate import verify as v
from data_agent.sales_store import DB_ENV as SALES_DB_ENV
from data_agent.sales_store import SalesStore

NUBI = "nubi-tour"
STUDY = "studyon"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(catalog.CATALOG_ENV, "catbooks")
    monkeypatch.delenv(cli.TENANT_ENV, raising=False)
    monkeypatch.setattr(tenant, "current", lambda: None)
    monkeypatch.setenv(SALES_DB_ENV, str(tmp_path / "sales.db"))
    catalog.use_catalog(None)


class _Seen:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str]] = []

    def __call__(self, path, tenant_id, cat, now, days, every):  # noqa: ANN001, ANN204, PLR0913
        self.calls.append((tenant_id, company_scope.active(), cat.id))
        if tenant_id == "boom":
            raise RuntimeError("고장")
        return v.Report(days=[], analyses={})


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Seen:
    (tmp_path / "sales.db").write_bytes(b"")  # 원본 파일이 있어야 비교를 시작한다
    got = _Seen()
    monkeypatch.setattr(v, "verify", got)
    return got


def test_catbooks_is_unchanged(seen: _Seen) -> None:
    for argv in ([], ["--tenant", "shop-demo"], ["--tenant", "catbooks"]):
        assert cli.main(["--verify-days", "3", *argv]) == 0
    assert seen.calls == [("shop-demo", None, "catbooks")] * 3


def test_other_company_uses_its_own_catalog_and_tenant(seen: _Seen) -> None:
    assert cli.main(["--verify-days", "3", "--tenant", NUBI]) == 0
    assert cli.main(["--verify-days", "3", "--tenant", "c-cf2f0d"]) == 0
    assert seen.calls == [(NUBI, NUBI, "nubi"), ("c-cf2f0d", "c-cf2f0d", "common")]
    assert company_scope.active() is None  # 범위는 명령 뒤에 풀린다


def test_bad_company_id_is_refused(seen: _Seen, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--verify-days", "3", "--tenant", "누비 투어!"]) == 2
    assert cli.main(["--verify-days", "3", "--all", "--tenant", NUBI]) == 2
    assert seen.calls == []  # 모르는 값을 캣북스로 바꿔 돌지 않는다
    assert "회사 id 모양이 아니에요" in capsys.readouterr().out


def test_all_walks_every_connected_company_and_survives_one_failure(
    seen: _Seen, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sc, "watched_companies", lambda env: [NUBI, STUDY])
    real = cli._verify

    def flaky(tenant_id, args, env):  # noqa: ANN001, ANN202
        if tenant_id == NUBI:
            raise RuntimeError("누비투어 고장")
        return real(tenant_id, args, env)

    monkeypatch.setattr(cli, "_verify", flaky)
    assert cli.main(["--verify-days", "3", "--all"]) == 1
    assert seen.calls == [("shop-demo", None, "catbooks"), (STUDY, STUDY, "studyon")]
    out = capsys.readouterr().out
    assert "== 캣북스 (shop-demo) ==" in out
    assert f"({NUBI}) 실패" in out and f"({STUDY}) ==" in out


NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)


def _rows(prefix: str, n: int, price: int) -> list[SalesOrderRow]:
    out = []
    for i in range(n):
        when = NOW - timedelta(days=1 + i % 3, hours=i)
        line = SalesOrderLine(product_id=f"{prefix}-p{i % 3}", category_code="x", title="t", qty=1,
                              unit_price=price, coupon_share=0, points_share=0)  # fmt: skip
        out.append(SalesOrderRow(
            order_id=f"{prefix}-o{i}", buyer_id=f"{prefix}-b{i % 4}", status="paid",
            created_at=when, updated_at=when, items_amount=price, payment_status="succeeded",
            lines=[line],
        ))  # fmt: skip
    return out


def _db(path: Path, companies: dict[str, list[SalesOrderRow]]) -> Path:
    store = SalesStore(path)
    for cid, rows in companies.items():
        got = asyncio.run(sc.collect_once(sc.MockSalesSource(rows), store, tenant_id=cid,
                                          source_name="shop-api", now=NOW))  # fmt: skip
        assert got.errors == []
    store.close()
    return path


def _report(path: Path, cid: str) -> list[str]:
    with company_scope.using(cid):
        report = v.verify(path, cid, catalog.current(), NOW, days=2, every=1)
    for analysis, t in report.analyses.items():
        assert t.different == 0, (cid, analysis, t.diffs)
    assert sum(t.same for t in report.analyses.values()) > 0  # 실제로 견준 칸이 있다
    return report.lines()


def test_verify_reads_only_that_company_rows(tmp_path: Path) -> None:
    """같은 sales.db에 두 회사가 있어도, 한 회사만 있을 때와 비교 결과가 같다(섞이지 않음)."""
    nubi, study = _rows("nubi", 6, 10000), _rows("study", 3, 30000)
    both = _db(tmp_path / "both.db", {NUBI: nubi, STUDY: study})
    assert _report(both, NUBI) == _report(_db(tmp_path / "n.db", {NUBI: nubi}), NUBI)
    assert _report(both, STUDY) == _report(_db(tmp_path / "s.db", {STUDY: study}), STUDY)
