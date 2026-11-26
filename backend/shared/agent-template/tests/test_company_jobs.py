"""주기 작업을 회사마다 돌리는 도우미 (회사 분리 11/26, `company_jobs`)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from ax_agent_template import companies, company_connection, company_jobs, company_scope
from ax_contracts.schemas.common import TENANT_ID_DEFAULT

NUBI = "c-aaaaaa"
STUDY = "c-bbbbbb"
OFF = "c-cccccc"


@dataclass
class _Conn:
    enabled: bool = True


@pytest.fixture
def two_companies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(companies, "company_ids", lambda: ["catbooks", NUBI, STUDY, OFF, NUBI])
    conns = {NUBI: _Conn(), STUDY: _Conn(), OFF: _Conn(enabled=False)}
    monkeypatch.setattr(company_connection, "read_connection", lambda cid=None: conns.get(cid))


def test_job_companies_are_catbooks_then_connected_others(two_companies) -> None:
    # 연결 꺼진 회사는 빠지고, 같은 회사는 한 번만
    assert company_jobs.job_companies() == ["catbooks", NUBI, STUDY]


def test_job_companies_fall_back_to_catbooks(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> list[str]:
        raise OSError("목록 깨짐")

    monkeypatch.setattr(companies, "company_ids", broken)
    assert company_jobs.job_companies() == ["catbooks"]


def test_broken_connection_file_skips_only_that_company(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(companies, "company_ids", lambda: ["catbooks", NUBI, STUDY])

    def read(cid=None):  # noqa: ANN001, ANN202
        if cid == NUBI:
            raise ValueError("깨진 파일")
        return _Conn()

    monkeypatch.setattr(company_connection, "read_connection", read)
    assert company_jobs.job_companies() == ["catbooks", STUDY]


def test_each_company_runs_in_its_own_scope_and_catbooks_as_before(two_companies) -> None:
    seen: list[tuple[str, str | None, str]] = []

    async def job(cid: str) -> str:
        seen.append((cid, company_scope.active(), company_jobs.meta_tenant_id()))
        return cid

    got = asyncio.run(company_jobs.for_each_company(job))
    assert got == {"catbooks": "catbooks", NUBI: NUBI, STUDY: STUDY}
    assert seen == [
        ("catbooks", None, TENANT_ID_DEFAULT),  # 캣북스는 예전처럼 요청 회사 없이, 예전 meta 값
        (NUBI, NUBI, NUBI),
        (STUDY, STUDY, STUDY),
    ]
    assert company_scope.active() is None  # 끝나면 범위가 풀린다


def test_one_company_failing_does_not_stop_the_others(two_companies) -> None:
    async def job(cid: str) -> str:
        if cid == NUBI:
            raise RuntimeError("누비투어 고장")
        return cid

    got = asyncio.run(company_jobs.for_each_company(job))
    assert got == {"catbooks": "catbooks", STUDY: STUDY}


def test_cancel_is_not_swallowed(two_companies) -> None:
    async def job(cid: str) -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(company_jobs.for_each_company(job))


def test_meta_tenant_id_for_explicit_company() -> None:
    assert company_jobs.meta_tenant_id("catbooks") == TENANT_ID_DEFAULT
    assert company_jobs.meta_tenant_id("shop-demo") == TENANT_ID_DEFAULT
    assert company_jobs.meta_tenant_id(NUBI) == NUBI
    with company_scope.using(STUDY):
        assert company_jobs.meta_tenant_id() == STUDY
