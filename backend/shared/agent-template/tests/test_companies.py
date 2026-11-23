"""회사 목록과 회사 폴더 경로 (여러 회사 설계 1-2).

회사 목록 파일이 없으면 캣북스 하나다(지금 동작 그대로). 옛 회사 값 `shop-demo`는
캣북스의 별칭이라 어디서 읽어도 `catbooks`로 바뀐다.
"""

import json
from pathlib import Path

import pytest

from ax_agent_template import companies
from ax_agent_template import company_connection as cc
from ax_contracts.schemas import Company


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(companies.COMPANIES_FILE_ENV, raising=False)
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    return tmp_path


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps({"companies": rows}, ensure_ascii=False), encoding="utf-8")


ROW_A = {
    "company_id": "nubi",
    "name": "누비투어",
    "industry": "여행",
    "site_url": "",
    "created_at": "2026-11-20T00:00:00Z",
}


# ---- 별칭 ----


def test_the_old_value_means_catbooks() -> None:
    assert companies.canonical_company_id("shop-demo") == "catbooks"
    assert companies.canonical_company_id("catbooks") == "catbooks"
    assert companies.canonical_company_id("nubi") == "nubi"


@pytest.mark.parametrize("bad", ["", "AB", "a", "../x", "nu bi", "Catbooks", "x" * 41, None, 3])
def test_bad_company_ids_are_none(bad) -> None:
    assert companies.canonical_company_id(bad) is None


# ---- 회사 목록 ----


def test_without_a_file_there_is_only_catbooks(data: Path) -> None:
    rows = companies.list_companies()
    assert [c.company_id for c in rows] == ["catbooks"]
    assert rows[0].name == "캣북스"
    assert isinstance(rows[0], Company)
    assert not (data / companies.COMPANIES_FILE).exists()  # 읽기만, 파일을 만들지 않는다


def test_without_any_setting_there_is_only_catbooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV, raising=False)
    monkeypatch.delenv(companies.COMPANIES_FILE_ENV, raising=False)
    assert companies.companies_file() is None
    assert [c.company_id for c in companies.list_companies()] == ["catbooks"]


def test_the_file_sits_next_to_the_connection_file(data: Path) -> None:
    assert companies.companies_file() == data / "companies.json"


def test_the_file_env_wins(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = data / "elsewhere.json"
    monkeypatch.setenv(companies.COMPANIES_FILE_ENV, str(other))
    assert companies.companies_file() == other


def test_reads_the_file(data: Path) -> None:
    _write(data / "companies.json", [ROW_A, {**ROW_A, "company_id": "catbooks", "name": "캣북스"}])
    assert [c.company_id for c in companies.list_companies()] == ["nubi", "catbooks"]


def test_an_old_value_in_the_file_is_read_as_catbooks(data: Path) -> None:
    _write(data / "companies.json", [{**ROW_A, "company_id": "shop-demo", "name": "캣북스"}])
    assert [c.company_id for c in companies.list_companies()] == ["catbooks"]


def test_same_company_twice_is_kept_once(data: Path) -> None:
    rows = [
        {**ROW_A, "company_id": "catbooks", "name": "캣북스"},
        {**ROW_A, "company_id": "shop-demo", "name": "옛 캣북스"},
    ]
    _write(data / "companies.json", rows)
    got = companies.list_companies()
    assert [(c.company_id, c.name) for c in got] == [("catbooks", "캣북스")]


def test_bad_rows_are_skipped(data: Path) -> None:
    rows = [ROW_A, {"company_id": "BAD!", "name": "x"}, "not a row", {**ROW_A, "name": ""}]
    _write(data / "companies.json", rows)
    assert [c.company_id for c in companies.list_companies()] == ["nubi"]


@pytest.mark.parametrize("text", ["{깨짐", "[]", '{"companies": "x"}'])
def test_a_broken_file_reads_as_catbooks_only(data: Path, text: str) -> None:
    # 깨진 파일 때문에 지금 회사(캣북스)가 사라지면 화면이 멈춘다 → 지금 동작 그대로
    (data / "companies.json").write_text(text, encoding="utf-8")
    assert [c.company_id for c in companies.list_companies()] == ["catbooks"]


def test_get_company_takes_the_old_value(data: Path) -> None:
    assert companies.get_company("shop-demo").company_id == "catbooks"
    assert companies.get_company("catbooks").company_id == "catbooks"
    assert companies.get_company("nubi") is None
    assert companies.get_company("../etc") is None
    assert companies.is_company("shop-demo") and not companies.is_company("nubi")


def test_company_ids(data: Path) -> None:
    _write(data / "companies.json", [ROW_A])
    assert companies.company_ids() == ["nubi"]


# ---- 회사 폴더 경로 ----


def test_company_path_is_under_the_companies_folder(data: Path) -> None:
    got = companies.company_path("catbooks", "company_connection.json")
    assert got == data / "companies" / "catbooks" / "company_connection.json"


def test_company_path_takes_the_old_value(data: Path) -> None:
    assert companies.company_path("shop-demo", "x.json") == companies.company_path(
        "catbooks", "x.json"
    )


def test_company_path_allows_a_sub_folder(data: Path) -> None:
    got = companies.company_path("nubi", "site_adapter/mapping.json")
    assert got == data / "companies" / "nubi" / "site_adapter" / "mapping.json"


def test_company_path_does_not_make_folders(data: Path) -> None:
    companies.company_path("nubi", "site_adapter/mapping.json")
    assert not (data / "companies").exists()


def test_the_folder_env_wins(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(data / "c"))
    assert companies.company_path("nubi", "a.json") == data / "c" / "nubi" / "a.json"


def test_company_path_is_none_without_a_data_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV, raising=False)
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    assert companies.company_path("catbooks", "a.json") is None


@pytest.mark.parametrize("bad", ["../catbooks", "AB", "", "nu/bi"])
def test_company_path_refuses_bad_company_ids(data: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        companies.company_path(bad, "a.json")


@pytest.mark.parametrize(
    "bad", ["", ".", "..", "../x.json", "a/../../x.json", "/etc/passwd", "C:/x", "a\\b", "a//b"]
)
def test_company_path_refuses_names_that_leave_the_folder(data: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        companies.company_path("catbooks", bad)


# --- default_company_id ---


@pytest.fixture
def clean_default(data: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(companies.DEFAULT_COMPANY_ENV, raising=False)
    monkeypatch.setattr(companies, "_legacy_warned", False)
    return data


def test_default_falls_back_to_legacy(
    clean_default: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="ax.companies"):
        assert companies.default_company_id() == companies.LEGACY_DEFAULT_COMPANY == "catbooks"
        assert companies.default_company_id() == "catbooks"
    assert sum("옛 기본값" in r.message for r in caplog.records) == 1  # 한 번만 경고


def test_default_from_env(clean_default: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(companies.DEFAULT_COMPANY_ENV, "nubi")
    assert companies.default_company_id() == "nubi"
    assert companies.company_ids() == ["nubi"]  # 목록 파일이 없으면 기본 회사 한 줄


def test_default_env_alias_is_canonical(
    clean_default: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(companies.DEFAULT_COMPANY_ENV, "shop-demo")
    assert companies.default_company_id() == "catbooks"


def test_default_bad_env_is_ignored(clean_default: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(companies.DEFAULT_COMPANY_ENV, "Bad Value!")
    assert companies.default_company_id() == "catbooks"


def test_default_single_company_list(clean_default: Path) -> None:
    _write(clean_default / "companies.json", [ROW_A])
    assert companies.default_company_id() == "nubi"


def test_default_env_beats_single_company_list(
    clean_default: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(clean_default / "companies.json", [ROW_A])
    monkeypatch.setenv(companies.DEFAULT_COMPANY_ENV, "catbooks")
    assert companies.default_company_id() == "catbooks"


def test_default_two_companies_uses_legacy(clean_default: Path) -> None:
    _write(clean_default / "companies.json", [ROW_A, {**ROW_A, "company_id": "other-co"}])
    assert companies.default_company_id() == "catbooks"
