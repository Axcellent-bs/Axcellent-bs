"""LLM 같은 요청 중복 계산 막기 평가 정의(`eval/llm_dedup/`)의 약속.

- 같은 요청 3개 동시 → LLM 1회 (다른 요청은 묶지 않음)
- 먼저 부른 쪽이 실패 → 그 요청만 실패, 기다리던 쪽 하나가 대신 1번 (중복 없음)
- "계산 중" 표시를 남긴 프로세스가 죽음 → 만료까지 기다린 뒤 대신 1번
- 서버·수집기(서로 다른 프로세스)에서도 같은 요청은 1회
- 수집기 새 숏츠 형식 분류는 장부에 skill "fresh_shorts" ("unknown" 아님)
- 각 케이스는 별도 프로세스(`worker.py`)로 실제 TimedLLMClient + 가짜 LLM 서버. 실제 LLM은 안 부름
- 관문 켬 (평가 정의 PR #833은 관찰만: dev 0.2 / 0.8 / 0.0)
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "llm_dedup"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"llm_dedup_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_rules() -> None:
    cases = {c["name"]: c for c in make.cases()}
    assert cases["same_three_at_once"]["expected"]["calls"] == 1
    assert cases["different_two_at_once"]["expected"]["calls"] == 2
    assert cases["owner_fails_waiter_takes_over"]["expected"] == {"calls": 2, "ok": 2, "errors": 1}
    assert cases["crashed_owner_expires"]["expected"]["min_wait"] >= 1.0
    assert len(cases["server_and_collector"]["workers"]) == 2
    assert cases["fresh_shorts_named"]["expected"] == {"skills": ["fresh_shorts"]}
    assert make.cases() == list(cases.values())


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = {}
    for c in cases:
        want = c["expected"]
        if c["kind"] == "calls":
            perfect[c["name"]] = {**want, "max_wait": want.get("min_wait", 0.0)}
        else:
            perfect[c["name"]] = dict(want)
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)
    early = {**perfect, "crashed_owner_expires": {**perfect["crashed_owner_expires"],
                                                  "max_wait": 0.1}}  # fmt: skip
    assert score.score(cases, early)["llm_dedup.call_count_accuracy"] < 1.0


# ---------------------------------------------------------------- 실행 (별도 프로세스)


def _worker(spec: dict, tmp: Path, env: dict[str, str], name: str) -> subprocess.Popen:
    path = tmp / f"{name}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    return subprocess.Popen([sys.executable, str(HERE / "worker.py"), str(path)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8")  # fmt: skip


def _out(proc: subprocess.Popen) -> dict | None:
    stdout, stderr = proc.communicate(timeout=120)
    lines = [line for line in stdout.splitlines() if line.strip().startswith("{")]
    if proc.returncode != 0 or not lines:
        print(stderr[-2000:])
        return None
    return json.loads(lines[-1])


def _run(case: dict, tmp: Path) -> dict | None:
    tmp.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("AX_LLM_USAGE_DB",)}
    env["DATA_LLM_BUDGET_DB"] = str(tmp / "budget.db")
    env["PYTHONIOENCODING"] = "utf-8"
    if case.get("lease"):
        env["DATA_LLM_INFLIGHT_SECONDS"] = str(case["lease"])
    calls = tmp / "calls.txt"
    base = {"calls_file": str(calls), "shorts_db": str(tmp / "shorts.db")}
    if case.get("crash_first"):
        crash = {**base, **case["workers"][0], "mode": "crash"}
        got = _out(_worker(crash, tmp, env, "crash"))
        if not got or got.get("unsupported"):
            return None
    start_at = time.time() + 8 if case.get("together") else None
    procs = [_worker({**base, **w, "start_at": start_at}, tmp, env, f"w{i}")
             for i, w in enumerate(case["workers"])]  # fmt: skip
    outs = [_out(p) for p in procs]
    if any(o is None for o in outs):
        return None
    answers = [a for o in outs for a in o.get("answers", [])]
    waits = [w for o in outs for w in o.get("waits", [])]
    return {
        "calls": calls.read_text().count("call") if calls.exists() else 0,
        "ok": sum(1 for a in answers if a == make.ANSWER),
        "errors": sum(1 for a in answers if isinstance(a, str) and a.startswith("error:")),
        "max_wait": max(waits) if waits else None,
        "skills": sorted({s for o in outs for s in o.get("skills", [])}),
    }


def observe(tmp_path: Path) -> tuple[dict[str, float], dict[str, dict | None]]:
    cases = make.cases()
    got = {c["name"]: _run(c, tmp_path / c["name"]) for c in cases}
    return score.score(cases, got), got


GATES = dict.fromkeys(score.METRICS, 1.0)  # 모두 맞아야 함 (평가 정의 PR #833 dev: 0.2 / 0.8 / 0.0)


def test_llm_dedup_passes_the_gates(tmp_path: Path) -> None:
    metrics, got = observe(tmp_path)
    print("llm_dedup eval:", json.dumps(metrics, ensure_ascii=False))
    print("llm_dedup cases:", json.dumps(got, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
    for name, gate in GATES.items():
        assert metrics[name] >= gate, (name, metrics[name], got)
