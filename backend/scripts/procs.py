"""run_all.sh가 띄우는 서버·수집기 프로세스를 찾고, 끄고, 남은 것을 확인한다.

    python scripts/procs.py running <표식>...
        # 떠 있으면 "이미 실행 중 (pid …), 먼저 stop", 끝 코드 1
    python scripts/procs.py stop --pids <pid 목록> --locks <잠금 파일>... -- <표식>...
        # 기록한 pid·수집기 잠금의 pid와 그 자식까지 끄고, 남은 프로세스가 0개인지 확인 (남으면 1)

표식은 명령줄에 들어 있는 글자(예: "uvicorn ax_safety.main:app", "data_agent.youtube_collect").
Windows의 venv python.exe는 런처라서 실제 python이 자식으로 하나 더 뜬다. 그래서 pid 하나만 끄지
않고 자식까지 끄고, 끈 뒤에는 표식으로 다시 찾아 남은 것이 없는지 본다(기록에 없는 옛 프로세스도
여기서 드러난다).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    cmd: str


Lister = Callable[[], list[Proc]]
Killer = Callable[[int], None]


def list_processes() -> list[Proc]:
    """지금 떠 있는 프로세스 (Windows는 python.exe만, 그 밖은 전부)."""
    if os.name == "nt":
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |"
            " Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
        )
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        ).stdout.strip()  # fmt: skip
        if not out:
            return []
        rows = json.loads(out)
        rows = rows if isinstance(rows, list) else [rows]
        return [
            Proc(int(r["ProcessId"]), int(r["ParentProcessId"] or 0), r.get("CommandLine") or "")
            for r in rows
        ]
    out = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, check=False
    ).stdout
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            procs.append(Proc(int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
    return procs


def kill_process(pid: int) -> None:
    """한 프로세스를 끈다 (없으면 조용히 넘어감)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def matching(procs: Iterable[Proc], markers: Sequence[str]) -> list[Proc]:
    """명령줄에 표식이 들어 있는 프로세스. 이 스크립트(표식을 인자로 받음)와 그 런처는 뺀다."""
    me = os.getpid()
    return [
        p
        for p in procs
        if p.pid != me and "procs.py" not in p.cmd and any(m in p.cmd for m in markers)
    ]


def with_children(roots: Iterable[int], procs: Sequence[Proc]) -> list[int]:
    """roots와 그 자식·손자 (자식 먼저 끄도록 깊은 것부터)."""
    children: dict[int, list[int]] = {}
    for p in procs:
        children.setdefault(p.ppid, []).append(p.pid)
    alive = {p.pid for p in procs}
    order: list[int] = []
    seen: set[int] = set()

    def visit(pid: int) -> None:
        if pid in seen:
            return
        seen.add(pid)
        for child in children.get(pid, []):
            visit(child)
        order.append(pid)

    for root in roots:
        if root in alive:
            visit(root)
    return order


def lock_pids(paths: Iterable[str | Path]) -> list[int]:
    """수집기 잠금 파일에 적힌 pid (수집기가 직접 적은 실제 프로세스 번호)."""
    out = []
    for path in paths:
        try:
            first = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[0]
            out.append(int(json.loads(first)["pid"]))
        except (OSError, IndexError, ValueError, KeyError, TypeError):
            continue
    return out


def running(markers: Sequence[str], lister: Lister | None = None) -> list[Proc]:
    return matching((lister or list_processes)(), markers)


def stop(
    pids: Iterable[int],
    locks: Iterable[str | Path],
    markers: Sequence[str],
    lister: Lister | None = None,
    killer: Killer | None = None,
    wait: float = 3.0,
) -> list[Proc]:
    """기록한 pid·잠금 pid와 자식까지 끄고, 남은(표식이 맞는) 프로세스를 돌려준다."""
    lister = lister or list_processes  # 부를 때 찾는다 (시험에서 바꿔 끼울 수 있게)
    killer = killer or kill_process
    procs = lister()
    ours = {p.pid for p in matching(procs, markers)}
    # 잠금 pid는 번호가 다른 프로세스에 다시 쓰였을 수 있어 표식이 맞을 때만 끈다
    roots = [*pids, *(pid for pid in lock_pids(locks) if pid in ours)]
    # 잠금에는 실제 python(자식) 번호가 적힌다. 표식이 같은 부모(venv 런처)도 같이 끈다
    parent = {p.pid: p.ppid for p in procs}
    for pid in list(roots):
        up = parent.get(pid)
        while up in ours and up not in roots:
            roots.append(up)
            up = parent.get(up)
    for pid in with_children(roots, procs):
        killer(pid)
    deadline = time.monotonic() + wait
    while True:
        left = running(markers, lister)
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(0.5)


def _describe(procs: Sequence[Proc]) -> str:
    return "\n".join(f"  pid {p.pid} (부모 {p.ppid}): {p.cmd[:200]}" for p in procs)


def main(argv: Sequence[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔·로그에서도 한글이 깨지지 않게
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    if not argv or argv[0] not in ("running", "stop"):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] == "running":
        found = running(argv[1:])
        if found:
            print(f"이미 실행 중 (pid {', '.join(str(p.pid) for p in found)}), 먼저 stop")
            print(_describe(found))
            return 1
        return 0
    rest = list(argv[1:])
    markers = rest[rest.index("--") + 1 :] if "--" in rest else []
    head = rest[: rest.index("--")] if "--" in rest else rest
    pids: list[int] = []
    locks: list[str] = []
    target: list | None = None
    for item in head:
        if item == "--pids":
            target = pids
        elif item == "--locks":
            target = locks
        elif target is pids:
            pids += [int(x) for x in item.replace(",", " ").split() if x.isdigit()]
        elif target is locks:
            locks.append(item)
    left = stop(pids, locks, markers)
    if left:
        print(f"남은 프로세스 {len(left)}개 (끄지 못함):")
        print(_describe(left))
        return 1
    print("남은 프로세스 0개")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
