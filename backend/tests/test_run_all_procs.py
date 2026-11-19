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
    # 서버·수집기 3개(인기 검색어·YouTube·판매 기록). 주문 트래픽은 캣북스 레포로 옮겼다
    assert body.count("winpid_of $! >") == 4
    assert body.count('echo "$START_LINE" > "$LOGS/') == 4
    # 판매 기록 수집기도 stop이 알아보고(표식) 남은 잠금을 정리한다
    assert '"data_agent.sales_collect"' in text and '"$DATA/sales_collect.lock"' in text
    assert "export AX_GIT_COMMIT" in text


def test_run_all_exports_tool_connections_file() -> None:
    """도구 연결 보관함 경로를 회사 연결 파일과 같은 꼴로 내보낸다."""
    text = (BACKEND / "scripts" / "run_all.sh").read_text(encoding="utf-8")
    line = (
        "export AX_TOOL_CONNECTIONS_FILE="
        '"${AX_TOOL_CONNECTIONS_FILE:-$PWD/$DATA/tool_connections.json}"'
    )
    assert line in text
    assert text.index(line) < text.index('-m uvicorn "$module"')


# --- dejafix-proxy (8030): 계정이 없어도 run_all이 띄운다 ---------------------

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


def test_dejafix_proxy_starts_without_a_username() -> None:
    """계정이 없어도 띄운다(연결 전 503). 보관함에서 연결하면 재시작 없이 쓴다."""
    text = RUN_ALL.read_text(encoding="utf-8")
    assert "DEJAFIX_USERNAME" not in text
    assert "dejafix-proxy 안 띄움" not in text


def test_health_check_uses_healthz_for_dejafix_proxy() -> None:
    """총괄 대기(띄운 서버 수 세기)와 마지막 상태 출력이 dejafix-proxy는 /healthz를 본다."""
    func = _between("health_url() {", "# bash의 $!는")
    out = _run(f"{func}\nhealth_url dejafix-proxy 8030\nhealth_url data 8106", {})
    assert out.splitlines() == ["http://127.0.0.1:8030/healthz", "http://127.0.0.1:8106/health"]
    text = RUN_ALL.read_text(encoding="utf-8")
    assert text.count('"$(health_url "$n" "$p")"') == 1  # 총괄 전 대기
    assert text.count('"$(health_url "$name" "$port")"') == 1  # 마지막 상태 출력
    assert ':$p/health"' not in text and ':$port/health"' not in text  # 예전 직접 주소가 없다


# --- procs.py: 자기 pid 파일을 적는 프로세스도 끈다 (2026-11-12 가상 주문 사례) ---


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


# --- 캣북스 판매 서버: AXcellent는 띄우지도 끄지도 않고, 주소만 알려 준다 (레포 분리 5단계) ---

SHOP_BLOCK = ("# >>> 판매 서버 주소", "# <<< 판매 서버 주소")


def test_shop_api_is_not_in_apps_or_groups() -> None:
    assert not any(line.split()[0] == "shop-api" for line in _apps())
    text = RUN_ALL.read_text(encoding="utf-8")
    assert "ax_shop_api" not in text
    assert "shop_traffic.py" not in text and "TRAFFIC_PID_FILE" not in text
    groups = [line for line in text.splitlines() if line.startswith("GROUP_")]
    assert groups and not any("shop-api" in line for line in groups)


def test_stop_does_not_own_port_8010_or_shop_markers(tmp_path: Path) -> None:
    """따로 띄운 캣북스를 끄지 않게 PORTS·MARKERS·잠금에 판매 서버가 없다."""
    text = RUN_ALL.read_text(encoding="utf-8")
    head = text[text.index('APPS="') : text.index("# --only 묶음 이름")]
    head = head.replace("MARKERS=()", "DATA=.data\nMARKERS=()", 1)
    tail = (
        '\necho "ports=$PORTS"\nprintf "%s\n" "${MARKERS[@]}"'
        '\nprintf "%s\n" "${COLLECTOR_LOCKS[@]}"'
    )
    snippet = f'cd "{tmp_path.as_posix()}"\n{head}{tail}'
    for env in ({}, {"SHOP_API_EXTERNAL": "on"}, {"SHOP_API_EXTERNAL": "off"}):
        out = _run(snippet, env).splitlines()
        assert "8010" not in out[0].split("=")[1].split(",")
        assert "uvicorn ax_safety.main:app" in out  # 나머지는 그대로
        assert not any("shop" in line for line in out[1:])


def test_reset_keeps_the_old_shop_db() -> None:
    """예전 판매 서버 파일(.data/shop.db)은 되돌릴 때를 위해 reset도 지우지 않는다."""
    reset = _between('if [ "$CMD" = "reset" ]; then', 'if [ "$CMD" = "mock" ]; then')
    assert "shop.db | shop.db-wal | shop.db-shm) continue" in reset
    assert 'rm -f "$DATA"/*.db ' not in reset


def _url_run(env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """판매 서버 주소 블록 + AX_<이름>_URL 내보내기를 tmp cwd에서 돌려 URL 변수를 돌려준다."""
    text = RUN_ALL.read_text(encoding="utf-8")
    start = text.index("# 컨테이너가 아니라서 서비스 이름 대신")
    export_part = text[start : text.index("# 부서 이름으로 부르는 코드가 있어도")]
    snippet = (
        f"cd \"{tmp_path.as_posix()}\"\nAPPS=$(cat <<'EOF'\n" + "\n".join(_apps()) + "\nEOF\n)\n"
        f"{_between(*SHOP_BLOCK)}\n{export_part}\n"
        'echo "shop=${AX_SHOP_API_URL-UNSET}"\necho "safety=${AX_SAFETY_URL-UNSET}"'
    )
    return dict(line.split("=", 1) for line in _run(snippet, env).splitlines())


@pytest.mark.parametrize("external", [None, "on", "off"])
def test_shop_api_url_has_no_catbooks_default(tmp_path: Path, external: str | None) -> None:
    """캣북스 기본값 없음: 운영자가 주지 않으면 AX_SHOP_API_URL을 내보내지 않는다.

    서비스들은 회사 연결 파일을 따르고, 연결 전이면 사이트 변환기가 "연결 안 됨"(503)으로 답한다.
    (#1220의 컨테이너 이름 빠짐은 agent_url이 연결 안 됨으로 보내 막는다.)
    """
    env = {} if external is None else {"SHOP_API_EXTERNAL": external}
    out = _url_run(env, tmp_path)
    assert out["shop"] == "UNSET"
    assert out["safety"].startswith("http://127.0.0.1:")  # 나머지 서비스 주소는 그대로


def test_shop_api_url_keeps_an_operator_set_value(tmp_path: Path) -> None:
    out = _url_run({"AX_SHOP_API_URL": "http://shop.example:9000"}, tmp_path)
    assert out["shop"] == "http://shop.example:9000"


def test_shop_api_source_is_not_in_this_repo() -> None:
    # 판매 서버 소스는 캣북스 레포에 있다 (pull 뒤 남은 __pycache__는 상관없다)
    assert not list((BACKEND / "shared" / "shop-api").rglob("*.py"))
    assert not (BACKEND / "scripts" / "shop_traffic.py").exists()
    assert not (BACKEND / "infra" / "fault-injection").exists()


# --- shop_fault.sh: SHOP_API_URL·SHOP_ADMIN_TOKEN을 둘 다 받아 캣북스 레포 스크립트로 넘긴다 ----


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
        "AX_WEB_ADMIN_TOKEN=local-tok\nVITE_SHOP_ADMIN_TOKEN=local-tok\n", encoding="utf-8"
    )
    env = {
        "CATBOOKS_DEMO_DIR": str(clone),
        "SHOP_API_URL": "https://shop.example",
        "SHOP_ADMIN_TOKEN": "cb-admin",
    }
    out = _fault(backend, env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "url=https://shop.example token=cb-admin args=status"


def test_shop_fault_does_not_fall_back_to_local_web_env(tmp_path: Path) -> None:
    """AXcellent는 판매 서버를 띄우지 않으니 web.env의 토큰을 판매 서버로 보내지 않는다."""
    clone, backend = _fault_env(tmp_path)
    (backend / ".data" / "web.env").write_text(
        "AX_WEB_ADMIN_TOKEN=local-tok\nVITE_SHOP_ADMIN_TOKEN=local-tok\n", encoding="utf-8"
    )
    out = _fault(backend, {"CATBOOKS_DEMO_DIR": str(clone)})
    assert out.returncode == 1
    assert out.stdout == ""
    assert "local-tok" not in out.stderr


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
        "AX_WEB_ADMIN_TOKEN=local-tok\nVITE_SHOP_ADMIN_TOKEN=local-tok\n", encoding="utf-8"
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
