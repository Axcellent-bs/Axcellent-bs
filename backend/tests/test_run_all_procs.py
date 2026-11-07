"""run_all.sh 켜기·끄기 확인 (`scripts/procs.py`).

- 켜기: 같은 서버·수집기가 이미 떠 있으면 "이미 실행 중 (pid …), 먼저 stop"으로 거부
- 끄기: 기록한 pid와 자식(venv 런처 밑 실제 python)·수집기 잠금 pid까지 끄고
  "남은 프로세스 0개" 확인. 기록에 없는 옛 프로세스가 남으면 실패로 pid·명령줄을 보여 준다

2026-11-06~07: 기록에 없는 옛 수집기가 잠금을 쥐고 남아, 다시 켤 때마다 새 수집기가 바로 끝나고
옛 코드가 하루 넘게 돌았다. 그 경우를 가짜 프로세스 표로 재현한다.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
MARKERS = ["uvicorn svc_data.main:app", "data_agent.youtube_collect", "data_agent.trending_collect"]


def _load():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("procs", BACKEND / "scripts" / "procs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["procs"] = module
    spec.loader.exec_module(module)
    return module


procs = _load()
P = procs.Proc


class FakeTable:
    """가짜 프로세스 표: 끄면 표에서 빠진다."""

    def __init__(self, rows: list) -> None:  # noqa: ANN401
        self.rows = list(rows)
        self.killed: list[int] = []

    def list(self) -> list:  # noqa: ANN401
        return list(self.rows)

    def kill(self, pid: int) -> None:
        self.killed.append(pid)
        self.rows = [r for r in self.rows if r.pid != pid]


def _collector_pair(launcher: int, child: int) -> list:
    cmd = "python.exe -m data_agent.youtube_collect --every 3600"
    return [P(launcher, 1, f".venv\\Scripts\\{cmd}"), P(child, launcher, f"Python312\\{cmd}")]


def test_start_is_refused_when_the_same_collector_is_already_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    table = FakeTable([*_collector_pair(100, 101), P(300, 1, "chrome.exe")])
    monkeypatch.setattr(procs, "list_processes", table.list)
    assert procs.main(["running", *MARKERS]) == 1
    out = capsys.readouterr().out
    assert out.startswith("이미 실행 중 (pid 100, 101), 먼저 stop")
    assert "data_agent.youtube_collect" in out


def test_start_is_allowed_when_nothing_is_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    table = FakeTable([P(300, 1, "chrome.exe"), P(400, 1, "python.exe scripts/procs.py running x")])
    monkeypatch.setattr(procs, "list_processes", table.list)
    assert procs.main(["running", *MARKERS]) == 0  # procs.py 자신(표식을 인자로 받음)은 세지 않음
    assert capsys.readouterr().out == ""


def test_stop_kills_recorded_pid_with_its_child_and_the_lock_holder(tmp_path: Path) -> None:
    """기록한 런처(100)와 자식(101), 기록에 없지만 잠금을 쥔 옛 수집기(201)까지 끄고 0개 확인."""
    lock = tmp_path / "youtube_collect.lock"
    lock.write_text(json.dumps({"pid": 201, "started_at": "2026-11-06T07:04:00+00:00"}) + "\n")
    table = FakeTable([
        *_collector_pair(100, 101),
        *_collector_pair(200, 201),  # 옛 수집기: .pid 기록에 없음, 잠금은 자식(실제 python)이 적음
        P(300, 1, "chrome.exe"),
    ])  # fmt: skip
    left = procs.stop([100], [lock], MARKERS, lister=table.list, killer=table.kill, wait=0)
    assert left == []
    assert table.killed.index(101) < table.killed.index(100)  # 자식 먼저
    assert set(table.killed) == {100, 101, 200, 201}  # 잠금 자식(201)의 런처(200)도
    assert [r.pid for r in table.rows] == [300]


def test_stop_reports_leftovers_and_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """기록에도 잠금에도 없는 프로세스가 남으면 실패(1)로 pid·명령줄을 보여 준다."""
    table = FakeTable([*_collector_pair(100, 101), *_collector_pair(500, 501)])
    monkeypatch.setattr(procs, "list_processes", table.list)
    monkeypatch.setattr(procs, "kill_process", table.kill)
    monkeypatch.setattr(procs.time, "sleep", lambda _s: None)
    code = procs.main(["stop", "--pids", "100", "--locks", str(tmp_path / "none.lock"),
                       "--", *MARKERS])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 1
    assert out.startswith("남은 프로세스 2개 (끄지 못함):")
    assert "pid 500" in out and "pid 501" in out and "data_agent.youtube_collect" in out


def test_stop_prints_zero_left_when_everything_is_gone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    table = FakeTable(_collector_pair(100, 101))
    monkeypatch.setattr(procs, "list_processes", table.list)
    monkeypatch.setattr(procs, "kill_process", table.kill)
    assert procs.main(["stop", "--pids", " 100 ", "--locks", str(tmp_path / "x.lock"),
                       "--", *MARKERS]) == 0  # fmt: skip
    assert capsys.readouterr().out.strip() == "남은 프로세스 0개"


def test_lock_pid_reused_by_another_program_is_not_killed(tmp_path: Path) -> None:
    lock = tmp_path / "trending_collect.lock"
    lock.write_text(json.dumps({"pid": 300}) + "\n")  # 수집기는 끝났고 번호가 다른 프로그램에 쓰임
    table = FakeTable([P(300, 1, "chrome.exe")])
    assert procs.stop([], [lock], MARKERS, lister=table.list, killer=table.kill, wait=0) == []
    assert table.killed == []


def test_real_fake_process_is_found_then_stopped_with_zero_left() -> None:
    """진짜 프로세스(표식만 가짜)로: 찾고, 기록한 pid로 끄면(자식까지) 남은 것 0개."""
    marker = "ax-procs-test-marker-7f3a"
    child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(60)  # {marker}"])
    try:
        deadline = time.monotonic() + 15
        while not procs.running([marker]) and time.monotonic() < deadline:
            time.sleep(0.3)
        assert child.pid in {p.pid for p in procs.running([marker])}
        assert procs.stop([child.pid], [], [marker], wait=10) == []
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_run_all_checks_running_before_start_and_no_longer_restarts_silently() -> None:
    """run_all.sh: 켜기 전에 procs.py running, 켜는 길에서 stop_all 없음, .pid는 Windows 번호."""
    text = (BACKEND / "scripts" / "run_all.sh").read_text(encoding="utf-8")
    start = text.index('scripts/procs.py running "${MARKERS[@]}"')
    first_launch = text.index('-m uvicorn "$module"')
    assert start < first_launch
    body = text[start:]
    assert "\nstop_all\n" not in body  # 켜기는 이제 끄고 다시 켜지 않는다 (먼저 stop)
    assert body.count("winpid_of $! >") == 4  # 서버·주문 트래픽·수집기 2개
    assert body.count('echo "$START_LINE" > "$LOGS/') == 4
    assert "export AX_GIT_COMMIT" in text
