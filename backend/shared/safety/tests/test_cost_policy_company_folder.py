"""비용 정책 파일을 회사 폴더로 (여러 회사 설계 1-4).

`SAFETY_COST_POLICY_FILE`을 정했으면 그대로. 안 정했고 회사 폴더를 켰으면
`<회사 폴더>/catbooks/cost_policy.json`. 옛 자리(`SAFETY_STATE_DB` 옆)에 있으면 이력과 같이 옮긴다.
"""

import json
import logging
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from ax_safety import cost_policy
from ax_safety.rules import load_thresholds


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
    cost_policy.prepare()  # 안전 서버가 켤 때 한 번
    assert cost_policy.policy_path() == new
    assert json.loads(new.read_text(encoding="utf-8")) == POLICY
    assert (new.parent / "cost_policy.json.history.jsonl").exists()
    assert (data / "cost_policy.json.moved").exists() and not old.exists()


def test_no_old_file_reads_new_path(data: Path) -> None:
    assert cost_policy.policy_path() == data / "companies" / "catbooks" / "cost_policy.json"
    assert not (data / "companies").exists()


VALUES = {"values": {"video_duration": {"value": 30, "updated_by": "a", "updated_at": "x"}}}


def test_judging_path_does_not_move_or_wait(data: Path, monkeypatch) -> None:  # noqa: ANN001
    """판정 경로(`effective`)는 경로만 읽는다. 이관은 켤 때 `prepare()`가 한 번."""
    old = data / "cost_policy.json"
    old.write_text(json.dumps(VALUES), encoding="utf-8")
    lock = data / "companies" / "catbooks" / company_files.LOCK_NAME
    lock.parent.mkdir(parents=True)
    lock.write_text("other", encoding="utf-8")

    def no_wait(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("판정 경로가 잠금을 기다렸다")

    monkeypatch.setattr(company_files, "_acquire", no_wait)
    assert cost_policy.policy_path() == old
    table = cost_policy.effective(load_thresholds())
    assert next(r for r in table.rules if r.key == "video_duration").value == 30
    assert old.exists()


def test_prepare_restores_old_path_when_off(data: Path, monkeypatch, caplog) -> None:  # noqa: ANN001
    old = data / "cost_policy.json"
    old.write_text(json.dumps(VALUES), encoding="utf-8")
    cost_policy.prepare()
    new = data / "companies" / "catbooks" / "cost_policy.json"
    changed = {"values": {"video_duration": {"value": 20, "updated_by": "b", "updated_at": "y"}}}
    new.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    cost_policy.prepare()
    assert cost_policy.policy_path() == old
    assert json.loads(old.read_text(encoding="utf-8")) == changed
    assert cost_policy.describe(load_thresholds())["moved_unrestored"] is False


def test_off_without_company_file_marks_unrestored(data: Path, monkeypatch, caplog) -> None:  # noqa: ANN001
    (data / "cost_policy.json.moved").write_text(json.dumps(VALUES), encoding="utf-8")
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    caplog.set_level(logging.WARNING, logger="ax.company_files")
    cost_policy.prepare()
    cost_policy.prepare()
    status = cost_policy.describe(load_thresholds())
    assert status["moved_unrestored"] is True and status["editable"] is True
    assert len([r for r in caplog.records if ".moved만 있다" in r.getMessage()]) == 1


def test_status_has_unrestored_flag_normally_false(data: Path) -> None:
    assert cost_policy.describe(load_thresholds())["moved_unrestored"] is False


def test_safety_server_start_runs_prepare(data: Path, gate, monkeypatch) -> None:  # noqa: ANN001
    """안전 서버가 켜질 때 비용 정책 이관·되살리기를 한 번 한다(conftest의 `gate`)."""
    from fastapi.testclient import TestClient

    from ax_safety.main import create_app

    calls: list[int] = []
    monkeypatch.setattr(cost_policy, "prepare", lambda: calls.append(1))
    with TestClient(create_app(gate, expire_interval=None)):
        pass
    assert calls == [1]
