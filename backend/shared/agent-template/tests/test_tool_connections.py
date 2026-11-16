"""공용 도구 연결 보관함."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ax_agent_template import tool_connections as tc

SECRET = "ghp_supersecretvalue123"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tool-connections.json"
    monkeypatch.setenv(tc.FILE_ENV, str(path))
    tc._cache = None
    return path


def _save(name: str = "빌드", secret: str = SECRET, conn_id: str | None = None):
    return tc.upsert(
        "jenkins",
        name,
        {"url": "http://ci.local"},
        {"token": secret},
        conn_id=conn_id,
        by="tester",
    )


def test_upsert_then_read_all(vault: Path) -> None:
    first = _save()
    second = _save("두 번째")
    assert (first.id, second.id) == ("jenkins-1", "jenkins-2")
    assert [c.id for c in tc.read_all()] == ["jenkins-1", "jenkins-2"]
    assert tc.get("jenkins-2") is not None
    assert tc.find("jenkins", url="http://ci.local").id == "jenkins-1"  # type: ignore[union-attr]
    assert tc.find("dejafix") is None


def test_blank_secret_keeps_previous(vault: Path) -> None:
    conn = _save()
    again = _save("이름만 변경", secret="", conn_id=conn.id)
    assert again.name == "이름만 변경"
    assert again.secrets == {"token": SECRET}


def test_public_has_no_secret_values(vault: Path) -> None:
    conn = _save()
    text = json.dumps(conn.public(), ensure_ascii=False) + repr(conn)
    assert SECRET not in text
    assert conn.public()["secrets_set"] == {"token": "ghp_••••"}
    assert conn.secret_values() == [SECRET]


def test_mask() -> None:
    assert tc.mask("abc") == "••••"
    assert tc.mask("abcdef") == "abcd••••"
    assert tc.mask("-----BEGIN PRIVATE KEY", key=True) == "설정됨"


def test_history_line_has_no_secret(vault: Path) -> None:
    conn = _save()
    tc.delete(conn.id, by="tester")
    raw = vault.with_name(vault.name + ".history.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(x) for x in raw.splitlines()]
    assert [x["action"] for x in lines] == ["save", "delete"]
    assert SECRET not in raw and "ci.local" not in raw
    assert set(lines[0]) == {"at", "by", "action", "id", "kind", "name"}


def test_reload_when_file_changes(vault: Path) -> None:
    _save()
    assert len(tc.read_all()) == 1
    vault.write_text("[]", encoding="utf-8")
    assert tc.read_all() == []


def test_broken_file_reads_as_empty(vault: Path, caplog: pytest.LogCaptureFixture) -> None:
    vault.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="ax.tool_connections"):
        assert tc.read_all() == []
        assert tc.read_all() == []
    assert len([r for r in caplog.records if "깨져" in r.message]) == 1


def test_no_env_means_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tc.FILE_ENV, raising=False)
    tc._cache = None
    assert tc.read_all() == []
    with pytest.raises(tc.ToolConnectionError):
        _save()


def test_unknown_kind_and_missing_id(vault: Path) -> None:
    with pytest.raises(tc.ToolConnectionError) as e:
        tc.upsert("nope", "x", {}, {}, conn_id=None, by="t")
    assert e.value.code == "invalid"
    with pytest.raises(tc.ToolConnectionError) as e2:
        _save(conn_id="jenkins-9")
    assert e2.value.code == "not_found"


def test_record_check(vault: Path) -> None:
    conn = _save()
    checked = tc.record_check(conn.id, True, "확인됨")
    assert checked.check_ok is True and checked.checked_at
    assert tc.get(conn.id).check_detail == "확인됨"  # type: ignore[union-attr]
    assert checked.secrets == conn.secrets


@pytest.mark.skipif(os.name == "nt", reason="윈도에는 0600이 없다")
def test_written_file_is_0600(vault: Path) -> None:
    _save()
    assert vault.stat().st_mode & 0o777 == 0o600
    vault.chmod(0o644)
    tc._cache = None
    tc.read_all()
    assert vault.stat().st_mode & 0o777 == 0o600
