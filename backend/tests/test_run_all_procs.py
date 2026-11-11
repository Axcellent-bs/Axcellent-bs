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
import os
import shutil
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
    child = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep(60)  # {marker}"],
        cwd=BACKEND,  # run_all.sh는 backend에서 띄운다 (우리 것 판정은 작업 폴더를 본다)
    )
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


UVICORN = "uvicorn ax_memory.main:app"
CONTAINER = P(900, 880, f"/app/.venv/bin/{UVICORN} --host 0.0.0.0 --port 8002", uid=0, cwd="")
MINE = P(100, 1, f".venv/bin/python -m {UVICORN} --port 8002", uid=1000, cwd=str(BACKEND))


def test_container_process_is_not_counted_as_already_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """251: 컨테이너 안 uvicorn이 호스트 ps에 보여도 시작을 막지 않는다."""
    inner = P(901, 1, f"/app/.venv/bin/python -m {UVICORN}", uid=0, cwd="/app")
    table = FakeTable([CONTAINER, inner])
    monkeypatch.setattr(procs, "list_processes", table.list)
    monkeypatch.setattr(procs.os, "getuid", lambda: 1000, raising=False)
    assert procs.main(["running", UVICORN]) == 0
    assert capsys.readouterr().out == ""


def test_container_process_is_never_a_stop_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procs.os, "getuid", lambda: 1000, raising=False)
    # 같은 사용자여도 cwd가 이 체크아웃 밖이면 제외
    root_cwd_visible = P(902, 1, f"/app/.venv/bin/{UVICORN}", uid=1000, cwd="/app")
    table = FakeTable([CONTAINER, root_cwd_visible, MINE])
    left = procs.stop([100], [], [UVICORN], lister=table.list, killer=table.kill, wait=0)
    assert left == []
    assert table.killed == [100]  # 컨테이너 프로세스(900·902)는 건드리지 않는다
    assert {r.pid for r in table.rows} == {900, 902}


def test_container_root_cwd_is_not_ours_even_when_we_run_as_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(procs.os, "getuid", lambda: 0, raising=False)
    assert procs.matching([CONTAINER], [UVICORN]) == []
    visible = P(903, 880, f"/app/.venv/bin/{UVICORN}", uid=0, cwd="/app")
    assert procs.matching([visible], [UVICORN]) == []


def test_this_checkouts_process_is_still_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procs.os, "getuid", lambda: 1000, raising=False)
    sub = P(101, 100, f"python -m {UVICORN}", uid=1000, cwd=str(BACKEND / "services"))
    assert procs.matching([CONTAINER, MINE, sub], [UVICORN]) == [MINE, sub]
    other_user = P(104, 1, f".venv/bin/python -m {UVICORN}", uid=1001, cwd=str(BACKEND))
    other_checkout = P(105, 1, f"python -m {UVICORN}", uid=1000, cwd="/home/x/other/backend")
    assert procs.matching([other_user, other_checkout], [UVICORN]) == []


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


# --- dejafix-proxy (8030): DejaFix 계정이 있을 때만 run_all이 같이 띄운다 ---------------------

RUN_ALL = BACKEND / "scripts" / "run_all.sh"
DEJAFIX_LINE = "dejafix-proxy 8030 ax_dejafix_proxy.main:app"


def _apps() -> list[str]:
    text = RUN_ALL.read_text(encoding="utf-8")
    block = text[text.index('APPS="') + len('APPS="') :]
    return [line for line in block[: block.index('"')].splitlines() if line.strip()]


def _bash() -> str:
    found = shutil.which("bash")
    if found is None or "system32" in found.lower():  # Windows의 WSL bash는 쓰지 않는다
        pytest.skip("bash 없음")
    return found


def _run(snippet: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [_bash(), "-c", snippet],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.environ.get("PATH", ""), **env},
        timeout=30,
        check=True,
    )
    return result.stdout


def _between(start: str, end: str) -> str:
    text = RUN_ALL.read_text(encoding="utf-8")
    return text[text.index(start) : text.index(end)]


def test_dejafix_proxy_is_in_apps_before_orchestrator() -> None:
    apps = _apps()
    names = [line.split()[0] for line in apps]
    assert DEJAFIX_LINE in apps
    assert names.index("dejafix-proxy") < names.index("orchestrator")
    assert names[-1] == "orchestrator"  # 총괄은 여전히 맨 뒤 (부서 카드를 모은다)


@pytest.mark.parametrize(
    ("username", "started"),
    [(None, False), ("", False), ("viewer-test", True)],
)
def test_dejafix_proxy_starts_only_with_a_username(username: str | None, started: bool) -> None:
    """DEJAFIX_USERNAME이 없으면 띄울 목록에서 빼고 한 줄만 찍는다. 값은 출력하지 않는다."""
    block = _between("# >>> dejafix-proxy 계정 확인", "# <<< dejafix-proxy 계정 확인")
    apps = "\n".join(_apps())
    env = {"RUN_APPS": apps} | ({} if username is None else {"DEJAFIX_USERNAME": username})
    out = _run(f'{block}\nprintf "%s\\n" "$RUN_APPS" | awk "NF {{print \\$1}}"', env)
    lines = out.splitlines()
    others = [line.split()[0] for line in _apps() if not line.startswith("dejafix-proxy ")]
    if started:
        assert lines == [line.split()[0] for line in _apps()]
    else:
        assert lines[0] == "dejafix-proxy 안 띄움(DEJAFIX_USERNAME 없음)"
        assert lines[1:] == others
    if username:
        assert username not in out


def test_dejafix_proxy_block_keeps_only_list_without_it() -> None:
    """--only로 고른 목록에 dejafix-proxy가 없으면 아무것도 찍지 않는다."""
    block = _between("# >>> dejafix-proxy 계정 확인", "# <<< dejafix-proxy 계정 확인")
    out = _run(f'{block}\nprintf "%s\\n" "$RUN_APPS"', {"RUN_APPS": "data 8106 svc_data.main:app"})
    assert out.splitlines() == ["data 8106 svc_data.main:app"]


def test_health_check_uses_healthz_for_dejafix_proxy() -> None:
    """총괄 대기(띄운 서버 수 세기)와 마지막 상태 출력이 dejafix-proxy는 /healthz를 본다."""
    func = _between("health_url() {", "# bash의 $!는")
    out = _run(f"{func}\nhealth_url dejafix-proxy 8030\nhealth_url data 8106", {})
    assert out.splitlines() == ["http://127.0.0.1:8030/healthz", "http://127.0.0.1:8106/health"]
    text = RUN_ALL.read_text(encoding="utf-8")
    assert text.count('"$(health_url "$n" "$p")"') == 1  # 총괄 전 대기
    assert text.count('"$(health_url "$name" "$port")"') == 1  # 마지막 상태 출력
    assert ':$p/health"' not in text and ':$port/health"' not in text  # 예전 직접 주소가 없다


# --- 캣북스 가상 주문 트래픽: SHOP_TRAFFIC=on일 때만 띄운다(기본 꺼짐) ----------------------------

TRAFFIC_BLOCK = ("# >>> 주문 트래픽 스위치", "# <<< 주문 트래픽 스위치")
SHOP_LINE = "shop-api 8010 ax_shop_api.main:app"


@pytest.mark.parametrize(
    ("value", "on"),
    [(None, False), ("", False), ("off", False), ("0", False), ("on", True), ("1", True)],
)
def test_shop_traffic_runs_only_when_switched_on(value: str | None, on: bool) -> None:
    block = _between(*TRAFFIC_BLOCK)
    env = {"RUN_APPS": SHOP_LINE} | ({} if value is None else {"SHOP_TRAFFIC": value})
    out = _run(f'{block}\necho "on=$SHOP_TRAFFIC_ON"', env)
    if on:
        assert out.splitlines() == ["on=1"]
    else:
        assert out.splitlines() == ["캣북스 주문 트래픽 안 띄움(SHOP_TRAFFIC=on이면 띄움)", "on=0"]


def test_shop_traffic_switch_is_quiet_without_shop_api() -> None:
    """--only로 shop-api를 안 띄우면 꺼짐 안내도 찍지 않는다."""
    out = _run(
        f'{_between(*TRAFFIC_BLOCK)}\necho "on=$SHOP_TRAFFIC_ON"',
        {"RUN_APPS": "data 8106 svc_data.main:app"},
    )
    assert out.splitlines() == ["on=0"]


def test_shop_traffic_launch_waits_for_the_switch() -> None:
    text = RUN_ALL.read_text(encoding="utf-8")
    launch = text[text.index(TRAFFIC_BLOCK[1]) :]
    guard = launch[: launch.index("scripts/shop_traffic.py")]
    assert '[ "$SHOP_TRAFFIC_ON" = 1 ]' in guard
