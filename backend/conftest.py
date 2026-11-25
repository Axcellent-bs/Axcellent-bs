"""backend 시험 전체 공통: 나눠 돌릴 때(pytest-xdist worksteal) 무거운 파일을 먼저 돌린다.

worksteal은 처음에 시험 목록을 워커 수만큼 연속 구간으로 나눠 주고, 먼저 끝난 워커는 가장 바쁜
워커 대기열의 **뒤쪽** 절반을 가져간다. 그래서 무거운 파일을 구간마다 맨 앞에 하나씩 둔다.
- 무거운 파일이 처음부터 워커마다 나뉘어 돌아 끝에 혼자 남지 않는다
- 앞쪽은 빼앗기지 않아 파일 단위 준비(module fixture)가 여러 워커에서 다시 돌지 않는다

순서만 바꾼다(고르거나 빼는 시험 없음). 한 프로세스(`-n 0`)면 그대로 둔다.
목록은 CI 로그 끝 "slowest 20 durations"를 보고 고친다. 대략 무거운 순서.
"""

from __future__ import annotations

import pytest

HEAVY_FIRST = (
    "domains/data/tests/test_aggregate_snapshots.py",
    "domains/data/tests/test_aggregate_daily.py",
    "domains/data/tests/test_aggregate_daily_run.py",
    "domains/data/tests/test_aggregate_verify.py",
    "domains/archive/tests/test_fuzz.py",
    "domains/data/tests/test_aggregate_read_sales.py",
    "domains/data/tests/test_aggregate_read_buyers.py",
    "domains/data/tests/test_aggregate_more.py",
    "domains/data/tests/test_campaign_eval_scorer.py",
    "domains/data/tests/test_fault_history_cache.py",
    "domains/data/tests/test_sales_memory.py",
    "domains/hr/tests/test_fuzz.py",
)


def _rel(item: pytest.Item) -> str:
    return item.nodeid.split("::", 1)[0].replace("\\", "/")


def _chunk_sizes(total: int, workers: int) -> list[int]:
    """worksteal이 처음 나눠 주는 구간 크기 (`check_schedule`과 같은 계산)."""
    sizes = []
    for i in range(workers):
        size = total // (workers - i)
        sizes.append(size)
        total -= size
    return sizes


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    workerinput = getattr(config, "workerinput", None)  # xdist 워커에서만 있다
    if not workerinput or config.getoption("dist", None) != "worksteal":
        return
    workers = int(workerinput.get("workercount", 1))
    if workers < 2:
        return
    rank = {path: n for n, path in enumerate(HEAVY_FIRST)}
    heavy: dict[str, list[pytest.Item]] = {}
    rest: list[pytest.Item] = []
    for item in items:
        path = _rel(item)
        (heavy.setdefault(path, []) if path in rank else rest).append(item)
    if not heavy:
        return
    sizes = _chunk_sizes(len(items), workers)
    lanes: list[list[pytest.Item]] = [[] for _ in range(workers)]
    # 무거운 파일을 무거운 순서로, 지금까지 무거운 파일이 가장 적은 구간 앞에 하나씩
    for path in sorted(heavy, key=rank.__getitem__):
        lane = min(range(workers), key=lambda k: (_heavy_files(lanes[k], rank), k))
        lanes[lane].extend(heavy[path])
    # 나머지는 원래 순서대로 구간 크기에 맞춰 채운다 (넘치는 구간이 있으면 뒤 구간이 받는다)
    it = iter(rest)
    for k in range(workers):
        room = sizes[k] - len(lanes[k])
        for _ in range(max(room, 0)):
            nxt = next(it, None)
            if nxt is None:
                break
            lanes[k].append(nxt)
    lanes[-1].extend(it)
    items[:] = [item for lane in lanes for item in lane]


def _heavy_files(lane: list[pytest.Item], rank: dict[str, int]) -> int:
    return len({_rel(i) for i in lane if _rel(i) in rank})
