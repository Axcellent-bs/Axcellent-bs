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
        if str(path).endswith(".lock"):
            calls["n"] += 1
            if calls["n"] == 1:
                Path(str(path)).write_text("1")  # 윈도에서 지워지는 중인 잠금 파일
                raise PermissionError("지워지는 중")
            if calls["n"] == 2:
                Path(str(path)).unlink()  # 지우기가 끝났다
        return real_open(path, flags, mode)

    monkeypatch.setattr(tc.os, "open", flaky)
    assert _save().id == "jenkins-1"
    assert calls["n"] == 2


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


def test_unwritable_lock_dir_raises_instead_of_looping(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(path, flags, mode=0o777):
        raise PermissionError("denied")

    real_stat = os.stat

    def no_stat(path, *a, **k):
        if str(path).endswith(".lock"):
            raise OSError("no such file")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(tc.os, "open", denied)
    monkeypatch.setattr(tc.os, "stat", no_stat)
    monkeypatch.setattr(tc, "LOCK_TIMEOUT", 0.2)
    start = time.monotonic()
    with pytest.raises(tc.ToolConnectionError) as e:
        _save()
    assert e.value.code == "config"
    assert time.monotonic() - start < 2


def test_lock_stat_failure_still_hits_deadline(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """잠금 파일이 있다고 나오는데 stat이 계속 실패해도 마감에 busy로 끝난다."""
    real_open = os.open

    def exists(path, flags, mode=0o777):
        if str(path).endswith(".lock"):
            raise FileExistsError
        return real_open(path, flags, mode)

    real_stat = os.stat

    def no_stat(path, *a, **k):
        if str(path).endswith(".lock"):
            raise OSError("gone")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(tc.os, "open", exists)
    monkeypatch.setattr(tc.os, "stat", no_stat)
    monkeypatch.setattr(tc, "LOCK_TIMEOUT", 0.2)
    with pytest.raises(tc.ToolConnectionError) as e:
        _save()
    assert e.value.code == "busy"


def test_stale_lock_replaced_by_fresh_one_is_not_removed(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """오래됐다고 본 사이 다른 프로세스가 새 잠금을 잡았으면 그 잠금을 지우지 않는다."""
    lock = Path(str(vault) + ".lock")
    lock.write_text("12345")
    old = time.time() - tc.LOCK_STALE - 5
    os.utime(lock, (old, old))
    real_stat = os.stat
    swapped = {"done": False}

    def racy_stat(path, *a, **k):
        result = real_stat(path, *a, **k)
        if str(path) == str(lock) and not swapped["done"]:
            swapped["done"] = True
            lock.write_text("other")  # 그사이 다른 프로세스가 오래된 잠금을 깨고 새로 잡았다
        return result

    monkeypatch.setattr(tc.os, "stat", racy_stat)
    monkeypatch.setattr(tc, "LOCK_TIMEOUT", 0.3)
    with pytest.raises(tc.ToolConnectionError) as e:
        _save()
    assert e.value.code == "busy"
    assert lock.read_text() == "other"  # 남의 새 잠금은 그대로
    assert not list(vault.parent.glob(vault.name + ".lock.*"))  # 옆에 둔 파일도 남기지 않는다
    assert tc.read_all() == []


def test_stale_lock_that_cannot_be_moved_waits_until_deadline(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """윈도에서 오래된 잠금을 옮기거나 지우지 못해도 쉬지 않고 돌지 않고 마감에 busy로 끝난다."""
    lock = Path(str(vault) + ".lock")
    lock.write_text("12345")
    old = time.time() - tc.LOCK_STALE - 5
    os.utime(lock, (old, old))
    calls = {"n": 0}

    def denied(src, *a, **k):
        if str(src) == str(lock):
            calls["n"] += 1
            if calls["n"] > 200:
                raise RuntimeError("쉬지 않고 돈다")
            raise PermissionError("사용 중")
        raise AssertionError("다른 파일은 옮기지 않는다")

    monkeypatch.setattr(tc.os, "replace", denied)
    monkeypatch.setattr(tc.os, "rename", denied)
    monkeypatch.setattr(tc.os, "remove", denied)
    monkeypatch.setattr(tc, "LOCK_TIMEOUT", 0.2)
    start = time.monotonic()
    with pytest.raises(tc.ToolConnectionError) as e:
        _save()
    assert e.value.code == "busy"
    assert time.monotonic() - start < 2
    assert lock.exists()


def _spy_tmp_fds(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    """임시 파일 fd와 tc가 직접 닫은 fd를 모은다."""
    real_open, real_close = os.open, os.close
    tmp_fds: list[int] = []
    closed: list[int] = []

    def spy_open(path, flags, mode=0o777):
        fd = real_open(path, flags, mode)
        if str(path).endswith(".tmp"):
            tmp_fds.append(fd)
        return fd

    def spy_close(fd):
        if fd in tmp_fds:  # 잠금 파일 fd가 같은 번호를 먼저 썼어도 섞이지 않게
            closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(tc.os, "open", spy_open)
    monkeypatch.setattr(tc.os, "close", spy_close)
    return tmp_fds, closed


def test_tmp_fd_closed_when_fails_before_fdopen(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """임시 파일을 연 뒤 fdopen 전에 실패하면 fd를 직접 닫고 임시 파일을 지운다."""
    tmp_fds, closed = _spy_tmp_fds(monkeypatch)

    class Stop(BaseException):
        pass

    def bad_chmod(*_a: object, **_k: object) -> None:
        raise Stop

    monkeypatch.setattr(tc.os, "chmod", bad_chmod)
    with pytest.raises(Stop):
        _save()
    assert len(tmp_fds) == 1 and tmp_fds[0] in closed
    assert not list(vault.parent.glob(vault.name + "*.tmp"))


def test_failed_fdopen_owns_fd_and_is_not_closed_twice(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fdopen이 fd를 넘겨받은 뒤 실패하면(스스로 닫는다) 그 번호를 다시 닫지 않는다.

    닫힌 번호는 다른 스레드가 바로 다시 쓸 수 있어 두 번 닫으면 남의 파일을 닫는다.
    """
    real_close = os.close  # tc.os는 os 모듈이라 바꾸기 전에 잡아 둔다
    tmp_fds, closed = _spy_tmp_fds(monkeypatch)

    def bad_fdopen(fd, *_a: object, **_k: object):
        real_close(fd)  # io.open처럼 넘겨받은 fd를 닫고 실패한다
        raise OSError("fdopen 실패")

    monkeypatch.setattr(tc.os, "fdopen", bad_fdopen)
    with pytest.raises(OSError):
        _save()
    assert len(tmp_fds) == 1 and tmp_fds[0] not in closed
    assert not list(vault.parent.glob(vault.name + "*.tmp"))


def test_mutating_returned_connection_does_not_change_cache(vault: Path) -> None:
    """돌려받은 연결의 config·secrets를 고쳐도 다음 읽기에 번지지 않는다."""
    _save()
    for got in (tc.read_all()[0], tc.get("jenkins-1"), tc.find("jenkins")):
        assert got is not None
        got.config["url"] = "http://evil"
        got.secrets["token"] = "바뀜"
    again = tc.get("jenkins-1")
    assert again is not None
    assert again.config == {"url": "http://ci.local"}
    assert again.secrets == {"token": SECRET}


def test_record_check_is_written_to_history(vault: Path) -> None:
    """확인 결과도 바꾼 것이라 이력에 한 줄 남긴다. 비밀·config·확인 문구는 넣지 않는다."""
    conn = _save()
    tc.record_check(conn.id, False, "확인 실패 문구")
    raw = vault.with_name(vault.name + ".history.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(x) for x in raw.splitlines()]
    assert [x["action"] for x in lines] == ["save", "check"]
    assert lines[1]["id"] == conn.id and lines[1]["kind"] == "jenkins"
    assert set(lines[1]) == {"at", "by", "action", "id", "kind", "name"}
    assert SECRET not in raw and "ci.local" not in raw and "확인 실패 문구" not in raw


def test_release_keeps_lock_now_held_by_someone_else(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """내 잠금이 그사이 남의 잠금으로 바뀌었으면 풀 때 지우지 않는다."""
    lock = Path(str(vault) + ".lock")
    real_write = tc._write

    def write_then_lose_lock(conns):
        real_write(conns)
        lock.write_text("other-token")  # 오래 걸려 남이 깨고 새로 잡았다

    monkeypatch.setattr(tc, "_write", write_then_lose_lock)
    assert _save().id == "jenkins-1"
    assert lock.read_text() == "other-token"


def test_lock_file_holds_unique_token_and_is_released(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = Path(str(vault) + ".lock")
    seen: list[str] = []
    real_write = tc._write

    def spy(conns):
        seen.append(lock.read_text())
        real_write(conns)

    monkeypatch.setattr(tc, "_write", spy)
    _save()
    _save("두 번째")
    assert len(seen) == 2 and seen[0] != seen[1]
    assert all(s.startswith(f"{os.getpid()}.") for s in seen)
    assert not lock.exists()


def test_stale_break_does_not_clobber_lock_taken_while_putting_back(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """남의 새 잠금을 되돌리려는데 그 자리를 또 다른 프로세스가 잡았으면 덮어쓰지 않는다."""
    lock = Path(str(vault) + ".lock")
    lock.write_text("12345")
    old = time.time() - tc.LOCK_STALE - 5
    os.utime(lock, (old, old))
    real_stat, real_link = os.stat, os.link
    swapped = {"done": False}

    def racy_stat(path, *a, **k):
        result = real_stat(path, *a, **k)
        if str(path) == str(lock) and not swapped["done"]:
            swapped["done"] = True
            lock.write_text("second")
        return result

    def racy_link(src, dst, *a, **k):
        Path(str(dst)).write_text("third")  # 되돌리기 직전 세 번째 프로세스가 잡았다
        return real_link(src, dst, *a, **k)

    monkeypatch.setattr(tc.os, "stat", racy_stat)
    monkeypatch.setattr(tc.os, "link", racy_link)
    monkeypatch.setattr(tc, "LOCK_TIMEOUT", 0.3)
    with pytest.raises(tc.ToolConnectionError) as e:
        _save()
    assert e.value.code == "busy"
    assert lock.read_text() == "third"
    assert not list(vault.parent.glob(vault.name + ".lock.*"))


def test_get_and_find_copy_only_the_match(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get·find는 목록 전체가 아니라 찾은 연결 하나만 복사한다."""
    for name in ("a", "b", "c"):
        _save(name)
    tc.read_all()  # 캐시를 채운다
    real_deepcopy = tc.copy.deepcopy
    copied: list[object] = []

    def spy(obj, *a, **k):
        copied.append(obj)
        return real_deepcopy(obj, *a, **k)

    monkeypatch.setattr(tc.copy, "deepcopy", spy)
    got = tc.get("jenkins-2")
    found = tc.find("jenkins", url="http://ci.local")
    assert got is not None and got.name == "b"
    assert found is not None and found.id == "jenkins-1"
    assert len(copied) == 2 and all(isinstance(x, tc.ToolConnection) for x in copied)
    assert tc.get("nope") is None and tc.find("dejafix") is None
    assert len(copied) == 2  # 못 찾으면 복사하지 않는다
