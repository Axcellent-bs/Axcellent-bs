"""수집기 잠금(`collector_lock`): pid를 적고, 끝난 pid가 남긴 잠금은 지우고 잡고, 상태를 읽는다."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from data_agent import collector_lock


def _dead_pid() -> int:
    """방금 끝난 프로세스의 pid."""
    out = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                         capture_output=True, text=True, check=True)  # fmt: skip
    return int(out.stdout.strip())


def test_lock_writes_the_pid_and_blocks_a_second_collector(tmp_path: Path) -> None:
    path = tmp_path / "youtube_collect.lock"
    first = collector_lock.acquire(path, "youtube", lambda _: None)
    assert first is not None
    try:
        owner = collector_lock.read_owner(path)
        assert owner is not None and owner["pid"] == os.getpid() and owner["started_at"]
        assert collector_lock.acquire(path, "youtube", lambda _: None) is None
        state = collector_lock.status(path)
        assert state.running and state.pid == os.getpid()
    finally:
        first.close()
    state = collector_lock.status(path)
    assert not state.running and "풀려" in state.reason


def test_a_lock_left_by_a_finished_pid_is_cleared_with_one_log_line(tmp_path: Path) -> None:
    path = tmp_path / "trending_collect.lock"
    dead = _dead_pid()
    path.write_text(json.dumps({"pid": dead, "started_at": "2026-11-05T00:00:00+00:00"}) + "\n",
                    encoding="utf-8")  # fmt: skip
    lines: list[str] = []
    handle = collector_lock.acquire(path, "trending", lines.append)
    assert handle is not None
    try:
        assert len(lines) == 1 and f"pid {dead}" in lines[0] and "남은 잠금" in lines[0]
        owner = collector_lock.read_owner(path)
        assert owner is not None and owner["pid"] == os.getpid()
    finally:
        handle.close()


def test_status_without_a_file_or_with_a_dead_pid(tmp_path: Path) -> None:
    assert not collector_lock.status(tmp_path / "none.lock").running
    path = tmp_path / "x.lock"
    path.write_text(json.dumps({"pid": _dead_pid()}) + "\n", encoding="utf-8")
    assert not collector_lock.status(path).running
    assert collector_lock.pid_alive(os.getpid()) and not collector_lock.pid_alive(0)
