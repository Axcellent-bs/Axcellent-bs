"""run_all.sh 로그 보관 (`keep_log`).

켤 때 지난 로그를 덮어쓰지 않고 .logs/prev/로 옮겨 이름마다 최근 5개만 남긴다.
2026-11-12: 로컬 서버가 어느새 모두 꺼졌는데, 다시 켜면서 .logs/<서버>.log를 새로 써서
꺼지던 순간의 줄이 사라졌다. 서버를 띄우지 않고 run_all.sh의 함수 블록만 꺼내 bash로 돌린다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
RUN_ALL = BACKEND / "scripts" / "run_all.sh"
BLOCK = ("# >>> 로그 보관", "# <<< 로그 보관")


def _bash() -> str:
    found = shutil.which("bash")
    if found is None or "system32" in found.lower():  # Windows의 WSL bash는 쓰지 않는다
        pytest.skip("bash 없음")
    return found


def _block() -> str:
    text = RUN_ALL.read_text(encoding="utf-8")
    return text[text.index(BLOCK[0]) : text.index(BLOCK[1])]


def _start(name: str, minute: int, body: str) -> str:
    """name을 10시 minute분에 켠 것처럼: 지난 로그를 옮기고(keep_log) 새 로그를 쓴다.

    run_all.sh와 같은 순서다. 로그 수정 시각도 켠 시각으로 맞춘다.
    """
    stamp = f"2026-11-12 10:{minute:02d}:00"
    return (
        f"keep_log {name}\n"
        f"printf '[start] 코드 abc (dev) · 시작 {stamp} +0900\\n{body}\\n' > \"$LOGS/{name}.log\"\n"
        f"touch -d '{stamp}' \"$LOGS/{name}.log\"\n"
    )


def _run(logs: Path, *steps: str) -> None:
    subprocess.run(
        [_bash(), "-c", f'LOGS="{logs.as_posix()}"\n{_block()}\n' + "".join(steps)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.environ.get("PATH", "")},
        timeout=30,
        check=True,
    )


def _prev(logs: Path, name: str) -> list[str]:
    return sorted(p.name for p in (logs / "prev").glob(f"{name}-[0-9]*.log"))


def test_second_start_keeps_the_previous_log(tmp_path: Path) -> None:
    _run(tmp_path, _start("dev", 1, "첫 실행 마지막 줄"), _start("dev", 2, "둘째"))
    assert _prev(tmp_path, "dev") == ["dev-20261112-100100.log"]
    kept = (tmp_path / "prev" / "dev-20261112-100100.log").read_text(encoding="utf-8")
    assert "첫 실행 마지막 줄" in kept  # 덮어쓰지 않았다
    assert "둘째" in (tmp_path / "dev.log").read_text(encoding="utf-8")


def test_only_the_latest_five_are_kept_and_the_oldest_goes_first(tmp_path: Path) -> None:
    # 7번 켬 → 지난 로그 6개. 6번째로 옮겨질 때 가장 오래된 것(10:01)이 지워진다
    _run(tmp_path, *(_start("dev", m, f"실행 {m}") for m in range(1, 8)))
    assert _prev(tmp_path, "dev") == [f"dev-20261112-10{m:02d}00.log" for m in range(2, 7)]
    assert "실행 7" in (tmp_path / "dev.log").read_text(encoding="utf-8")


def test_names_sharing_a_prefix_are_counted_separately(tmp_path: Path) -> None:
    api = [_start("shop-api", m, "api") for m in range(1, 8)]
    traffic = [_start("shop-traffic", 1, "t1"), _start("shop-traffic", 2, "t2")]
    _run(tmp_path, *api, *traffic)
    assert len(_prev(tmp_path, "shop-api")) == 5
    assert _prev(tmp_path, "shop-traffic") == ["shop-traffic-20261112-100100.log"]


def test_log_without_start_line_uses_its_time_and_missing_log_is_fine(tmp_path: Path) -> None:
    (tmp_path / "data.log").write_text("시작 줄 없는 예전 로그\n", encoding="utf-8")
    _run(tmp_path, "touch -d '2026-11-11 09:30:00' \"$LOGS/data.log\"\n", "keep_log data\n")
    _run(tmp_path, "keep_log nothing\n")
    assert _prev(tmp_path, "data") == ["data-20261111-093000.log"]
    assert _prev(tmp_path, "nothing") == []


def test_same_start_time_does_not_overwrite(tmp_path: Path) -> None:
    _run(tmp_path, _start("org", 1, "a"), _start("org", 1, "b"), _start("org", 2, "c"))
    assert _prev(tmp_path, "org") == ["org-20261112-100100-2.log", "org-20261112-100100.log"]


def test_every_log_start_keeps_the_previous_one_first() -> None:
    """서버·주문 트래픽·수집기 2개: 새로 쓰기 바로 앞 줄에서 keep_log를 부른다."""
    lines = RUN_ALL.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if 'echo "$START_LINE" > "$LOGS/' in line]
    assert len(starts) == 4
    for i in starts:
        assert lines[i - 1].strip().startswith("keep_log "), lines[i]


def test_logs_folder_is_not_committed() -> None:
    ignore = (BACKEND / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".logs/" in [line.strip() for line in ignore]
