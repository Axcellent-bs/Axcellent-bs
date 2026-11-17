"""공용 도구 연결 보관함."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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
    assert conn.public()["secrets_set"] == {"token": "••••"}
    assert conn.secret_values() == [SECRET]


def test_mask() -> None:
    assert tc.mask("abc") == "••••"
    assert tc.mask("abcd") == "••••"
    assert tc.mask("abcdef") == "••••"  # 앞글자도 내지 않는다
    assert tc.mask("11aa22bb33cc44dd55ee") == "••••"
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


def test_broken_file_blocks_writes(vault: Path) -> None:
    conn = _save()
    broken = b"{not json, keep me"
    vault.write_bytes(broken)
    for action in (
        lambda: _save("새 연결"),
        lambda: _save("고침", conn_id=conn.id),
        lambda: tc.record_check(conn.id, True, "ok"),
        lambda: tc.delete(conn.id, by="t"),
    ):
        with pytest.raises(tc.ToolConnectionError) as e:
            action()
        assert e.value.code == "config" and "읽을 수 없어" in e.value.message
    assert vault.read_bytes() == broken
    assert not list(vault.parent.glob(vault.name + "*.tmp"))
    assert tc.read_all() == []


def test_failed_write_removes_tmp(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(tc.json, "dump", boom)
    with pytest.raises(OSError):
        _save()
    assert not list(vault.parent.glob(vault.name + "*.tmp"))


def test_dejafix_proxy_url_env_then_vault_then_none(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("X_DEJAFIX_URL", raising=False)
    monkeypatch.delenv("AX_DEJAFIX_PROXY_URL", raising=False)
    assert tc.dejafix_proxy_url("X_DEJAFIX_URL") is None
    tc.upsert(
        "dejafix",
        "장애",
        {"url": "http://df.local", "username": "v"},
        {"password": "pw"},
        conn_id=None,
        by="tester",
    )
    assert tc.dejafix_proxy_url("X_DEJAFIX_URL") == "http://dejafix-proxy:8030"
    monkeypatch.setenv("X_DEJAFIX_URL", " http://env.test/ ")
    assert tc.dejafix_proxy_url("X_DEJAFIX_URL") == "http://env.test"


_WRITER = """
import sys
from ax_agent_template import tool_connections as tc
for i in range(int(sys.argv[2])):
    tc.upsert("jenkins", f"{sys.argv[1]}-{i}", {"url": "http://ci.local"}, {}, conn_id=None, by="t")
"""


def test_two_processes_writing_do_not_lose_rows(vault: Path) -> None:
    env = {**os.environ, tc.FILE_ENV: str(vault)}
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER, who, "15"], env=env) for who in ("a", "b")
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    names = {c.name for c in tc.read_all()}
    assert names == {f"{w}-{i}" for w in ("a", "b") for i in range(15)}
    assert not Path(str(vault) + ".lock").exists()
    assert not list(vault.parent.glob(vault.name + "*.tmp"))


def test_stale_lock_is_broken(vault: Path) -> None:
    lock = Path(str(vault) + ".lock")
    lock.write_text("12345")
    old = time.time() - tc.LOCK_STALE - 5
    os.utime(lock, (old, old))
    assert _save().id == "jenkins-1"
    assert not lock.exists()


def test_fresh_lock_held_elsewhere_is_busy(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = Path(str(vault) + ".lock")
    lock.write_text("12345")
    monkeypatch.setattr(tc, "LOCK_TIMEOUT", 0.1)
    with pytest.raises(tc.ToolConnectionError) as e:
        _save()
    assert e.value.code == "busy"
    assert lock.exists()  # 남의 잠금은 지우지 않는다
    assert tc.read_all() == []


def test_same_size_same_mtime_rewrite_is_reread(vault: Path) -> None:
    """바뀐 시각·크기가 같아도 파일이 바꿔치기(inode 변경)되면 다시 읽는다."""
    _save(name="가나")
    first = vault.stat()
    if first.st_ino == 0:
        pytest.skip("st_ino를 주지 않는 파일 시스템")
    assert tc.read_all()[0].name == "가나"
    other = vault.with_name("other.json")
    other.write_text(vault.read_text(encoding="utf-8").replace("가나", "다라"), encoding="utf-8")
    assert other.stat().st_size == first.st_size
    os.utime(other, ns=(first.st_atime_ns, first.st_mtime_ns))
    os.replace(other, vault)
    assert tc.read_all()[0].name == "다라"


def test_lock_permission_error_is_retried(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = os.open
    calls = {"n": 0}

    def flaky(path, flags, mode=0o777):
        if str(path).endswith(".lock") and calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError("지워지는 중")
        return real_open(path, flags, mode)

    monkeypatch.setattr(tc.os, "open", flaky)
    assert _save().id == "jenkins-1"
    assert calls["n"] == 1


def test_lock_fd_closed_when_write_fails(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[int] = []
    real_close = os.close

    def bad_write(fd, data):
        raise OSError("디스크 가득")

    def spy_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(tc.os, "write", bad_write)
    monkeypatch.setattr(tc.os, "close", spy_close)
    with pytest.raises(OSError):
        _save()
    assert len(closed) == 1
    assert not Path(str(vault) + ".lock").exists()
