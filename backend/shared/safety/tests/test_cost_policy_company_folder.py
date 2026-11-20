"""비용 정책 파일을 회사 폴더로 (여러 회사 설계 1-4).

`SAFETY_COST_POLICY_FILE`을 정했으면 그대로. 안 정했고 회사 폴더를 켰으면
`<회사 폴더>/catbooks/cost_policy.json`. 옛 자리(`SAFETY_STATE_DB` 옆)에 있으면 이력과 같이 옮긴다.
"""

import json
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from ax_safety import cost_policy


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".data"
    root.mkdir()
    monkeypatch.delenv(cost_policy.FILE_ENV, raising=False)
    monkeypatch.setenv("SAFETY_STATE_DB", str(root / "safety_state.db"))
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    monkeypatch.setattr(cost_policy, "_cache", None)
    company_files.reset_for_tests()
    return root


POLICY = {"video_duration": {"value": 60, "updated_at": "2026-11-19T00:00:00Z", "by": "a"}}


def test_without_company_folders_beside_state_db(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    assert cost_policy.policy_path() == data / "cost_policy.json"


def test_explicit_file_is_respected(data: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    pinned = tmp_path / "pinned.json"
    monkeypatch.setenv(cost_policy.FILE_ENV, str(pinned))
    (data / "cost_policy.json").write_text("{}", encoding="utf-8")
    assert cost_policy.policy_path() == pinned
    assert (data / "cost_policy.json").exists()


def test_old_file_moves_with_history(data: Path) -> None:
    old = data / "cost_policy.json"
    old.write_text(json.dumps(POLICY), encoding="utf-8")
    (data / "cost_policy.json.history.jsonl").write_text("{}\n", encoding="utf-8")
    new = data / "companies" / "catbooks" / "cost_policy.json"
    assert cost_policy.policy_path() == new
    assert json.loads(new.read_text(encoding="utf-8")) == POLICY
    assert (new.parent / "cost_policy.json.history.jsonl").exists()
    assert (data / "cost_policy.json.moved").exists() and not old.exists()


def test_no_old_file_reads_new_path(data: Path) -> None:
    assert cost_policy.policy_path() == data / "companies" / "catbooks" / "cost_policy.json"
    assert not (data / "companies").exists()
