"""수집기 중복 실행 잠금과 상태 (youtube_collect·trending_collect가 같이 쓴다).

- 잠금: DB 옆 `<이름>.lock`. OS 잠금(Windows `msvcrt`, 그 밖 `fcntl`)은 파일 뒤쪽 한 바이트
  (`LOCK_OFFSET`)에 건다. 파일 앞쪽에는 잡은 프로세스의 pid·시작 시각(JSON 한 줄)을 적어
  다른 프로세스(data 서버의 출처 현황)가 읽을 수 있게 한다
- 남은 잠금: 파일에 적힌 pid가 이미 끝났으면 남은 잠금으로 보고 지우고 새로 잡는다(로그 한 줄).
  OS 잠금은 프로세스가 끝나면 풀리므로, 보통은 적힌 내용만 남은 경우다
- 상태: `status(path)` = 잡혀 있는지(OS 잠금을 시험해 봄) + 적힌 pid가 살아 있는지
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

LOCK_OFFSET = 1 << 20  # OS 잠금을 거는 바이트 위치 (앞쪽 pid 글은 잠그지 않는다)


def pid_alive(pid: int) -> bool:
    """그 pid 프로세스가 살아 있는지 (프로세스를 건드리지 않고 묻기만 한다)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32  # Windows에만 있는 속성
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel.GetLastError() == 5  # 권한 없음 = 있기는 하다
        try:
            code = ctypes.c_ulong()
            ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # POSIX: 신호 0은 보내지 않고 있는지만 본다
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock(handle: IO[str]) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[str]) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _open(path: Path) -> IO[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    return os.fdopen(fd, "r+", encoding="utf-8")


def read_owner(path: Path) -> dict | None:
    """잠금 파일에 적힌 {pid, started_at}. 없거나 읽을 수 없으면 None."""
    try:
        line = path.read_text(encoding="utf-8").splitlines()[0]
        data = json.loads(line)
    except (OSError, IndexError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("pid"), int) else None


def acquire(path: Path, name: str, log: Callable[[str], None] = print) -> IO[str] | None:
    """잠금을 잡고 pid를 적는다. 살아 있는 다른 수집기가 잡고 있으면 None."""
    owner = read_owner(path)
    handle = _open(path)
    if not _lock(handle):
        handle.close()
        if owner is None or pid_alive(owner["pid"]):
            return None
        log(f"[{name}] 남은 잠금 지움: pid {owner['pid']}가 이미 끝남 ({path.name})")
        try:
            path.unlink()
        except OSError:
            return None
        handle = _open(path)
        if not _lock(handle):
            handle.close()
            return None
    elif owner is not None and owner["pid"] != os.getpid() and not pid_alive(owner["pid"]):
        log(f"[{name}] 남은 잠금 지움: pid {owner['pid']}가 이미 끝남 ({path.name})")
    handle.seek(0)
    handle.truncate()
    started = datetime.now(UTC).replace(microsecond=0).isoformat()
    handle.write(json.dumps({"pid": os.getpid(), "started_at": started}) + "\n")
    handle.flush()
    return handle


@dataclass(frozen=True)
class LockStatus:
    running: bool  # 잠금이 잡혀 있고 적힌 pid가 살아 있음
    pid: int | None
    started_at: str | None
    reason: str  # 사람이 읽는 한 줄


def status(path: Path) -> LockStatus:
    """수집기가 돌고 있는지 (잠금을 잠깐 시험해 보고 바로 푼다). 상태를 바꾸지 않는다."""
    if not path.is_file():
        return LockStatus(False, None, None, "잠금 파일이 없음 (수집기를 켠 적이 없거나 지워짐)")
    owner = read_owner(path)
    pid = owner["pid"] if owner else None
    started = owner.get("started_at") if owner else None
    try:
        handle = _open(path)
    except OSError:
        return LockStatus(False, pid, started, "잠금 파일을 열 수 없음")
    try:
        free = _lock(handle)
        if free:
            _unlock(handle)
    finally:
        handle.close()
    if free:
        why = f"잠금이 풀려 있음 (마지막 pid {pid})" if pid else "잠금이 풀려 있음"
        return LockStatus(False, pid, started, why)
    if pid is not None and not pid_alive(pid):
        return LockStatus(False, pid, started, f"잠금은 잡혀 있는데 pid {pid}가 없음")
    return LockStatus(True, pid, started, f"돌고 있음 (pid {pid}, {started}부터)")
