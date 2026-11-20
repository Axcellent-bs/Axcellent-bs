"""회사 목록 파일 쓰기 (여러 회사 설계 1-2).

dashboard-api가 `companies.json`을 쓴다. 서버가 켜질 때 스스로 만들지 않고,
사람이 `python -m ax_dashboard_api.companies init`으로 만든다(있으면 그대로 둔다).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ax_agent_template import companies as read
from ax_agent_template import company_connection as cc
from ax_contracts.schemas import Company
from ax_dashboard_api import companies


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(read.COMPANIES_FILE_ENV, raising=False)
    monkeypatch.delenv(read.COMPANIES_DIR_ENV, raising=False)
    return tmp_path


NUBI = Company(company_id="nubi", name="누비투어", created_at=datetime(2026, 11, 21, tzinfo=UTC))


def test_init_writes_catbooks_when_there_is_no_file(data: Path) -> None:
    assert companies.init_file() is True
    saved = json.loads((data / "companies.json").read_text(encoding="utf-8"))
    assert [row["company_id"] for row in saved["companies"]] == ["catbooks"]
    assert [c.company_id for c in read.list_companies()] == ["catbooks"]


def test_init_keeps_an_existing_file(data: Path) -> None:
    companies.save([NUBI])
    before = (data / "companies.json").read_bytes()
    assert companies.init_file() is False
    assert (data / "companies.json").read_bytes() == before


def test_init_keeps_a_broken_file(data: Path) -> None:
    # 깨진 파일도 사람이 볼 때까지 덮어쓰지 않는다
    (data / "companies.json").write_text("{깨짐", encoding="utf-8")
    assert companies.init_file() is False
    assert (data / "companies.json").read_text(encoding="utf-8") == "{깨짐"


def test_save_writes_canonical_ids_once(data: Path) -> None:
    old = Company.model_validate(
        {"company_id": "shop-demo", "name": "캣북스", "created_at": "2026-11-20T00:00:00Z"}
    )
    companies.save([old, read.CATBOOKS, NUBI])
    saved = json.loads((data / "companies.json").read_text(encoding="utf-8"))
    assert [row["company_id"] for row in saved["companies"]] == ["catbooks", "nubi"]
    assert "shop-demo" not in (data / "companies.json").read_text(encoding="utf-8")


def test_save_leaves_no_temp_file(data: Path) -> None:
    companies.save([NUBI])
    assert sorted(p.name for p in data.iterdir()) == ["companies.json"]


def test_save_without_a_place_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV, raising=False)
    monkeypatch.delenv(read.COMPANIES_FILE_ENV, raising=False)
    with pytest.raises(RuntimeError):
        companies.save([NUBI])
    with pytest.raises(RuntimeError):
        companies.init_file()


def test_save_refuses_an_empty_list(data: Path) -> None:
    with pytest.raises(ValueError):
        companies.save([])


def test_the_command(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert companies.main(["init"]) == 0
    assert "만들었" in capsys.readouterr().out
    assert companies.main(["init"]) == 0
    assert "그대로" in capsys.readouterr().out
    assert companies.main(["list"]) == 0
    assert "catbooks" in capsys.readouterr().out
    assert companies.main(["모름"]) == 2


def test_server_start_does_not_write_the_file(data: Path) -> None:
    from fastapi.testclient import TestClient

    from ax_dashboard_api.main import app

    with TestClient(app) as client:
        client.get("/health")
    assert not (data / "companies.json").exists()
