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
    # 서버·주문 트래픽·수집기 3개(인기 검색어·YouTube·판매 기록)
    assert body.count("winpid_of $! >") == 5
    assert body.count('echo "$START_LINE" > "$LOGS/') == 5
    # 판매 기록 수집기도 stop이 알아보고(표식) 남은 잠금을 정리한다
    assert '"data_agent.sales_collect"' in text and '"$DATA/sales_collect.lock"' in text
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


def _run(snippet: str, env: dict[str, str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        [_bash(), "-c", snippet],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.environ.get("PATH", ""), **env},
        cwd=cwd,
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


# --- 캣북스 가상 주문: SHOP_TRAFFIC=on일 때만 넣고, 꺼도 배송 표시·재고 보충은 돈다 ----------

TRAFFIC_BLOCK = ("# >>> 주문 트래픽 스위치", "# <<< 주문 트래픽 스위치")
SHOP_LINE = "shop-api 8010 ax_shop_api.main:app"
OFF_LINE = "캣북스 가상 주문 꺼짐(배송 표시·재고 보충만, SHOP_TRAFFIC=on이면 주문도 넣음)"


@pytest.mark.parametrize(
    ("value", "on"),
    [(None, False), ("", False), ("off", False), ("0", False), ("on", True), ("1", True)],
)
def test_virtual_orders_only_when_switched_on(value: str | None, on: bool) -> None:
    block = _between(*TRAFFIC_BLOCK)
    env = {"RUN_APPS": SHOP_LINE} | ({} if value is None else {"SHOP_TRAFFIC": value})
    out = _run(f'{block}\necho "orders=$SHOP_TRAFFIC_ORDERS"', env)
    if on:
        assert out.splitlines() == ["orders=1"]
    else:
        assert out.splitlines() == [OFF_LINE, "orders=0"]


def test_traffic_switch_is_quiet_without_shop_api() -> None:
    """--only로 shop-api를 안 띄우면 꺼짐 안내도 찍지 않는다."""
    block = _between(*TRAFFIC_BLOCK)
    out = _run(f'{block}\necho "orders=$SHOP_TRAFFIC_ORDERS"', {"RUN_APPS": "data 8106 x:app"})
    assert out.splitlines() == ["orders=0"]


def test_traffic_script_still_starts_with_the_switch_off() -> None:
    """꺼도 스크립트는 띄우고(배송 표시·재고 보충), 스위치 값만 넘긴다."""
    text = RUN_ALL.read_text(encoding="utf-8")
    launch = text[text.index(TRAFFIC_BLOCK[1]) :]
    launch = launch[: launch.index('"$CB_BACKEND/scripts/shop_traffic.py"')]
    assert 'SHOP_TRAFFIC_ORDERS="$SHOP_TRAFFIC_ORDERS"' in launch
    assert "SHOP_TRAFFIC_ON" not in text


# --- 가상 주문(shop_traffic.py)도 stop으로 끈다 (2026-11-12: bash 껍데기 pid만 적혀 남았다) -----


def _traffic_pair(launcher: int, child: int) -> list:
    cmd = "python.exe scripts/shop_traffic.py"
    return [P(launcher, 9999, f".venv\\Scripts\\{cmd}"), P(child, launcher, f"Python312\\{cmd}")]


def test_traffic_is_stopped_by_its_own_pid_file_when_the_bash_pid_is_gone(tmp_path: Path) -> None:
    """.pid의 bash 번호(9999)는 이미 없고, shop_traffic.py가 직접 적은 실제 번호(501)로 끈다."""
    pid_file = tmp_path / "shop_traffic.pid.json"
    pid_file.write_text(json.dumps({"pid": 501, "started_at": "2026-11-12T05:59:43+00:00"}) + "\n")
    table = FakeTable([*_traffic_pair(500, 501), P(300, 1, "chrome.exe")])
    left = procs.stop([9999], [pid_file], ["shop_traffic.py"], lister=table.list, killer=table.kill,
                      wait=0)  # fmt: skip
    assert left == []
    assert set(table.killed) == {500, 501}  # 실제 python과 그 venv 런처
    assert [r.pid for r in table.rows] == [300]


def test_traffic_without_a_pid_file_is_left_and_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """기록 없이 떠 있는 가상 주문은 끄지 않고 "남아 있음: pid N"으로 알린다."""
    table = FakeTable(_traffic_pair(700, 701))
    monkeypatch.setattr(procs, "list_processes", table.list)
    monkeypatch.setattr(procs, "kill_process", table.kill)
    monkeypatch.setattr(procs.time, "sleep", lambda _s: None)
    code = procs.main(["stop", "--pids", "", "--locks", str(tmp_path / "none.json"),
                       "--", "shop_traffic.py"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 1
    assert table.killed == []
    assert "남아 있음: pid 700" in out and "남아 있음: pid 701" in out


def test_run_all_passes_the_traffic_pid_file_and_stop_reads_it() -> None:
    text = RUN_ALL.read_text(encoding="utf-8")
    assert 'TRAFFIC_PID_FILE="$DATA/shop_traffic.pid.json"' in text
    locks = text[text.index("COLLECTOR_LOCKS=(") :].splitlines()[0]
    assert '"$TRAFFIC_PID_FILE"' in locks  # stop_all이 procs.py --locks로 넘긴다
    launch = text[text.index('rm -f "$TRAFFIC_PID_FILE"') :]
    launch = launch[: launch.index('"$CB_BACKEND/scripts/shop_traffic.py"')]
    assert 'SHOP_TRAFFIC_PID_FILE="$TRAFFIC_PID_FILE"' in launch
    # pid 파일을 적는 쪽(shop_traffic.write_pid_file)은 캣북스 레포로 옮겼다. 시험도 그쪽에 있다:
    # Catbooks-demo/catbooks-demo backend/shop-api/tests/test_shop_traffic.py
    #   test_write_pid_file_writes_its_real_pid,
    #   test_write_pid_file_without_a_path_or_unwritable_does_not_raise


# --- 캣북스 판매 서버: AXcellent는 띄우지 않는다. 로컬 개발로만 옆 클론에서 (CATBOOKS_LOCAL=on) ---

CB_BLOCK = ("# >>> 캣북스 판매 서버", "# <<< 캣북스 판매 서버")
SKIP_BLOCK = ("# >>> 판매 서버 건너뛰기", "# <<< 판매 서버 건너뛰기")
SKIP_LINE = (
    "shop-api는 외부 판매 서버라 띄우지 않음(주소는 AX_SHOP_API_URL 또는 회사 연결, "
    "로컬 개발로 옆 catbooks-demo에서 띄우려면 CATBOOKS_LOCAL=on)"
)
APPS_TWO = "data 8106 svc_data.main:app\n" + SHOP_LINE


def _clone(tmp_path: Path) -> Path:
    clone = tmp_path / "catbooks-demo"
    (clone / "backend" / "shop-api").mkdir(parents=True)
    return clone


def _decide(env: dict[str, str], cwd: Path) -> tuple[str, list[str]]:
    """판매 서버 결정 + 건너뛰기를 돌려 (CB_LOCAL, 출력 줄들[마지막은 남은 RUN_APPS 이름])."""
    block = _between(*CB_BLOCK) + "\n" + _between(*SKIP_BLOCK)
    snippet = f'RUN_APPS="$(printf "{APPS_TWO}")"\n{block}\necho "cb=$CB_LOCAL"\n' + (
        "echo \"$RUN_APPS\" | awk 'NF {print $1}' | tr '\\n' ' '"
    )
    lines = _run(snippet, env, cwd=cwd).splitlines()
    cb = next(line for line in lines if line.startswith("cb="))
    return cb, lines


@pytest.mark.parametrize("value", [None, "", "off", "1"])
def test_shop_api_is_skipped_by_default(tmp_path: Path, value: str | None) -> None:
    """기본(꺼짐)이면 옆 클론이 있어도 판매 서버를 띄우지 않고 한 줄로 알린다."""
    clone = _clone(tmp_path)
    env = {"CATBOOKS_DEMO_DIR": str(clone)} | ({} if value is None else {"CATBOOKS_LOCAL": value})
    cb, lines = _decide(env, cwd=tmp_path)  # tmp_path에는 .env가 없다
    assert cb == "cb=0"
    assert lines[0] == SKIP_LINE
    assert lines[-1].split() == ["data"]


def test_shop_api_is_skipped_when_on_but_no_clone(tmp_path: Path) -> None:
    env = {"CATBOOKS_LOCAL": "on", "CATBOOKS_DEMO_DIR": str(tmp_path / "missing")}
    cb, lines = _decide(env, cwd=tmp_path)
    assert cb == "cb=0"
    assert lines[0] == SKIP_LINE
    assert lines[-1].split() == ["data"]


def test_shop_api_runs_from_the_clone_when_catbooks_local_on(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    cb, lines = _decide({"CATBOOKS_LOCAL": "on", "CATBOOKS_DEMO_DIR": str(clone)}, cwd=tmp_path)
    assert cb == "cb=1"
    assert SKIP_LINE not in lines
    assert lines[-1].split() == ["data", "shop-api"]


def test_catbooks_local_is_read_from_backend_env_when_unset(tmp_path: Path) -> None:
    """환경변수가 없으면 backend/.env의 CATBOOKS_LOCAL을 본다 (다른 줄은 읽지 않는다)."""
    clone = _clone(tmp_path)
    (tmp_path / ".env").write_text("OTHER=1\nCATBOOKS_LOCAL=on  # 로컬만\n", encoding="utf-8")
    cb, _ = _decide({"CATBOOKS_DEMO_DIR": str(clone)}, cwd=tmp_path)
    assert cb == "cb=1"


def test_default_clone_dir_is_next_to_the_repo() -> None:
    default = 'CATBOOKS_DEMO_DIR="${CATBOOKS_DEMO_DIR:-$PWD/../../catbooks-demo}"'
    assert default in _between(*CB_BLOCK)


def test_shop_api_launch_uses_the_clone_venv_and_traffic_script() -> None:
    """판매 서버는 옆 클론의 venv(없으면 uv sync 한 번)로, 가상 주문은 그 레포 스크립트로 띄운다."""
    text = RUN_ALL.read_text(encoding="utf-8")
    launch = text[text.index('if [ "$name" = "shop-api" ]; then') :]
    launch = launch[: launch.index("started=$((started + 1))")]
    assert "py=$(cb_python)" in launch
    assert '(cd "$CB_BACKEND" && uv sync -q)' in launch
    assert '"$py" -m uvicorn "$module"' in launch
    assert '"$(cb_python)" "$CB_BACKEND/scripts/shop_traffic.py"' in text
    assert '"$PY" scripts/shop_traffic.py' not in text


def test_stop_does_not_own_port_8010_unless_local(tmp_path: Path) -> None:
    """기본이면 8010(외부 판매 서버)을 끄는 포트·표식에서 뺀다."""
    head = _between('APPS="', "# 가상 주문(shop_traffic.py)도")
    snippet = f'cd "{tmp_path.as_posix()}"\n{head}\necho "$PORTS"\nprintf "%s\\n" "${{MARKERS[@]}}"'
    off = _run(snippet, {"CATBOOKS_LOCAL": "off"}).splitlines()
    assert "8010" not in off[0].split(",") and "uvicorn ax_shop_api.main:app" not in off
    assert "shop_traffic.py" not in off
    clone = _clone(tmp_path)
    on = _run(snippet, {"CATBOOKS_LOCAL": "on", "CATBOOKS_DEMO_DIR": str(clone)}).splitlines()
    assert "8010" in on[0].split(",") and "uvicorn ax_shop_api.main:app" in on
    assert "shop_traffic.py" in on


def test_shop_api_source_is_not_in_this_repo() -> None:
    # 판매 서버 소스는 캣북스 레포에 있다 (pull 뒤 남은 __pycache__는 상관없다)
    assert not list((BACKEND / "shared" / "shop-api").rglob("*.py"))
    assert not (BACKEND / "scripts" / "shop_traffic.py").exists()


def test_shop_api_url_is_only_defaulted_when_local() -> None:
    """외부 판매 서버면 AX_SHOP_API_URL을 8010으로 덮지 않는다. 로컬이어도 이미 준 값이 먼저다."""
    text = RUN_ALL.read_text(encoding="utf-8")
    loop = text[text.index("# 컨테이너가 아니라서") :]
    loop = loop[: loop.index('done <<< "$APPS"')]
    assert '[ "$name" = "shop-api" ] && continue' in loop
    assert (
        'if [ "$CB_LOCAL" = 1 ]; then\n'
        '  export AX_SHOP_API_URL="${AX_SHOP_API_URL:-http://127.0.0.1:8010}"\nfi'
    ) in text


# --- shop_fault.sh: 환경의 SHOP_API_URL·SHOP_ADMIN_TOKEN이 먼저, 없을 때만 로컬(web.env·8010) ----


def _fault_env(tmp_path: Path) -> tuple[Path, Path]:
    """가짜 캣북스 클론(받은 주소·토큰만 찍는 shop_fault.sh)과 web.env가 있는 가짜 backend."""
    clone = tmp_path / "catbooks-demo"
    (clone / "backend" / "scripts").mkdir(parents=True)
    (clone / "backend" / "scripts" / "shop_fault.sh").write_text(
        'echo "url=$SHOP_API_URL token=$SHOP_ADMIN_TOKEN args=$*"\n', encoding="utf-8"
    )
    backend = tmp_path / "ax" / "backend"
    (backend / "scripts").mkdir(parents=True)
    (backend / ".data").mkdir()
    script = BACKEND / "scripts" / "shop_fault.sh"
    (backend / "scripts" / "shop_fault.sh").write_bytes(script.read_bytes())
    return clone, backend


def _fault(backend: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), str(backend / "scripts" / "shop_fault.sh"), "status"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.environ.get("PATH", ""), **env},
        timeout=30,
    )


def test_shop_fault_prefers_env_url_and_token(tmp_path: Path) -> None:
    clone, backend = _fault_env(tmp_path)
    (backend / ".data" / "web.env").write_text(
        "VITE_SHOP_ADMIN_TOKEN=local-tok\n", encoding="utf-8"
    )
    env = {
        "CATBOOKS_DEMO_DIR": str(clone),
        "SHOP_API_URL": "https://shop.example",
        "SHOP_ADMIN_TOKEN": "cb-admin",
    }
    out = _fault(backend, env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "url=https://shop.example token=cb-admin args=status"


def test_shop_fault_falls_back_to_local_web_env(tmp_path: Path) -> None:
    clone, backend = _fault_env(tmp_path)
    (backend / ".data" / "web.env").write_text(
        "VITE_SHOP_ADMIN_TOKEN=local-tok\n", encoding="utf-8"
    )
    out = _fault(backend, {"CATBOOKS_DEMO_DIR": str(clone)})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "url=http://127.0.0.1:8010 token=local-tok args=status"


@pytest.mark.parametrize(
    "given",
    [{"SHOP_API_URL": "https://shop.example"}, {"SHOP_ADMIN_TOKEN": "cb-admin"}],
    ids=["url-only", "token-only"],
)
def test_shop_fault_with_only_one_of_url_and_token_stops(
    tmp_path: Path, given: dict[str, str]
) -> None:
    """하나만 주면 멈춘다: 로컬 토큰을 다른 주소로도, 토큰만 127.0.0.1로도 보내지 않는다."""
    clone, backend = _fault_env(tmp_path)
    (backend / ".data" / "web.env").write_text(
        "VITE_SHOP_ADMIN_TOKEN=local-tok\n", encoding="utf-8"
    )
    out = _fault(backend, {"CATBOOKS_DEMO_DIR": str(clone), **given})
    assert out.returncode == 1
    assert out.stdout == ""  # 캣북스 스크립트를 부르지 않았다
    assert "SHOP_API_URL" in out.stderr and "SHOP_ADMIN_TOKEN" in out.stderr
    assert "local-tok" not in out.stderr


def test_shop_fault_without_any_token_stops(tmp_path: Path) -> None:
    clone, backend = _fault_env(tmp_path)
    out = _fault(backend, {"CATBOOKS_DEMO_DIR": str(clone)})
    assert out.returncode == 1
    assert "SHOP_ADMIN_TOKEN" in out.stderr and out.stdout == ""
