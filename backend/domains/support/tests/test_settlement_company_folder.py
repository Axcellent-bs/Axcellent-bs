"""정산 묶음 기록을 캣북스 회사 폴더로 (여러 회사 설계 1단계, 2026-11-23).

- `SUPPORT_SETTLEMENT_FILE`을 정했으면 그대로(옛 파일을 옮기지 않는다)
- 안 정했고 회사 폴더를 켰으면(`AX_COMPANIES_DIR`) `<회사 폴더>/catbooks/support_settlement.json`.
  옛 자리(`<회사 폴더의 부모>/support_settlement.json`)에 있으면 처음 읽을 때 옮기고
  옛 것은 `.moved`
- 둘 다 없으면 예전처럼 메모리에만 둔다
- `AX_COMPANY_FOLDERS=off`로 옛 경로를 정해 켜면, 옛 경로가 비어 있을 때 회사 폴더 값으로 되살린다
- 옮긴 뒤에도 승인된 주를 잊지 않고, 재무 기록에서 다시 가져오지 않는다
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from support_agent import settlement_batches as batches_mod

NAME = "support_settlement.json"
FOLDER = Path("companies") / "catbooks"
APPROVED = {"batches": {"2026-W46": {"status": "approved", "total": 120000}}}


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".data"
    root.mkdir()
    monkeypatch.delenv(batches_mod.ENV_FILE, raising=False)
    monkeypatch.delenv(batches_mod.ENV_IMPORT, raising=False)
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    company_files.reset_for_tests()
    return root


def test_without_company_folders_it_stays_in_memory(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    (data / NAME).write_text(json.dumps(APPROVED), encoding="utf-8")
    assert batches_mod.store_path() is None
    assert batches_mod.build_store().path is None
    assert (data / NAME).exists()  # 건드리지 않는다


def test_env_path_wins(data: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    pinned = tmp_path / "pinned" / "settlement.json"
    monkeypatch.setenv(batches_mod.ENV_FILE, str(pinned))
    (data / NAME).write_text(json.dumps(APPROVED), encoding="utf-8")
    assert batches_mod.store_path() == pinned
    assert (data / NAME).exists()  # 옮기지 않는다
    assert not (data / FOLDER / NAME).exists()


def test_new_file_goes_to_the_company_folder(data: Path) -> None:
    path = batches_mod.store_path()
    assert path == data / FOLDER / NAME
    store = batches_mod.BatchStore(path)
    store.put("2026-W47", {"status": "requested"})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["batches"]["2026-W47"]["status"] == "requested"
    assert not (data / NAME).exists()


def test_old_file_moves_and_approved_weeks_are_kept(data: Path) -> None:
    (data / NAME).write_text(json.dumps(APPROVED), encoding="utf-8")
    finance = data / "finance_state.json"
    finance.write_text(
        json.dumps({"records": {"batches": {"2026-W40": {"status": "approved"}}}}), encoding="utf-8"
    )
    store = batches_mod.BatchStore(batches_mod.store_path(), finance)

    assert store.path == data / FOLDER / NAME
    assert store.get("2026-W46") == {"status": "approved", "total": 120000}
    assert store.get("2026-W40") is None  # 옮긴 파일이 있으니 재무 기록에서 다시 가져오지 않는다
    assert (data / f"{NAME}.moved").exists() and not (data / NAME).exists()


def test_import_from_finance_when_nothing_exists_yet(data: Path) -> None:
    finance = data / "finance_state.json"
    finance.write_text(
        json.dumps({"records": {"batches": {"2026-W40": {"status": "approved"}}}}), encoding="utf-8"
    )
    store = batches_mod.BatchStore(batches_mod.store_path(), finance)
    assert store.get("2026-W40") == {"status": "approved"}
    assert (data / FOLDER / NAME).exists()  # 가져온 기록은 회사 폴더에 남는다


def test_off_restores_the_old_path(data: Path, monkeypatch) -> None:  # noqa: ANN001
    folder = data / FOLDER
    folder.mkdir(parents=True)
    (folder / NAME).write_text(json.dumps(APPROVED), encoding="utf-8")
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    # run_all.sh가 off일 때 옛 경로를 정한다
    monkeypatch.setenv(batches_mod.ENV_FILE, str(data / NAME))

    store = batches_mod.build_store()
    assert store.path == data / NAME
    assert store.get("2026-W46") == {"status": "approved", "total": 120000}
    assert (folder / NAME).exists()  # 회사 폴더 파일은 지우지 않는다
