"""인사이트 브리프 다시 추천·판 저장 평가 정의(`eval/brief_regen/`)의 약속.

- 하루 3번·1시간(자동 판 포함)·LLM 70%·이미 만드는 중·같은 감지 스냅샷(no_new_data)이면 막는다
- 막힘·실패·자동 판은 횟수를 차감하지 않고, 실패하면 이전 판이 최신으로 남는다
- "만드는 중" 잠금은 sqlite(서버·수집기 공용), 10분 뒤 만료. 두 번 눌러도·두 프로세스여도 판 1개
- 판은 지우지 않고 기본은 최신 판, 규칙 버전별로 따로. 같은 주제는 같은 insight_id
- briefs 표는 그날 최신 판 (대화 read_insight 호환). diff는 새·빠진 주제
- LLM은 부르지 않는다 (만들기는 가짜 함수). 관문 켬 (평가 정의 PR #851은 관찰만: dev 모두 0.0)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "brief_regen"
NOW = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)  # 한국 12:00


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"brief_regen_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_rules() -> None:
    by = {c["name"]: c for c in make.cases()}
    assert by["daily_limit"]["expected"] == {"blocked": "daily_limit", "used": 3}
    assert by["auto_not_counted"]["expected"] == {"used": 1}
    assert by["two_processes"]["expected"]["versions"] == 1
    assert by["diff_counts"]["expected"] == {"new": ["D", "E"], "gone": ["B"]}
    assert make.cases() == list(by.values())


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = {c["name"]: dict(c["expected"]) for c in cases}
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)


# ---------------------------------------------------------------- 실행 (모듈이 없으면 None = 0점)


def _out(topics: list[str], tenant: str = "catbooks"):  # noqa: ANN202
    from ax_contracts.schemas.data import TrendInsight, TrendInsightBriefOutput
    from data_agent.skills.trend_insight_brief import stable_insight_id

    return TrendInsightBriefOutput(
        status="known", generated_at=NOW,
        insights=[TrendInsight(insight_id=stable_insight_id(tenant, t), rank=n, topic=t, line="x")
                  for n, t in enumerate(topics, 1)],
    )  # fmt: skip


def _run(case: dict, tmp: Path) -> dict | None:  # noqa: C901, PLR0911, PLR0912, PLR0915
    try:
        from data_agent import brief_versions as bv
    except ImportError:
        return None
    from data_agent.skills import trend_insight_brief as tib

    tmp.mkdir(parents=True, exist_ok=True)
    env = {tib.DB_ENV: str(tmp / "brief.db")}
    day, rules = tib.today_kst(NOW), tib.rules_version(tib.load_config(env))
    store = tib.BriefStore(env[tib.DB_ENV])

    def put(topics: list[str], ago: timedelta, trigger: str, key: str, who: str | None = None,
            rules_: str | None = None) -> int:  # fmt: skip
        return store.put(day, rules_ or rules, _out(topics), NOW - ago, trigger=trigger,
                         requested_by=who, detect_as_of=NOW - ago, detect_key=key)  # fmt: skip

    def saver(topics: list[str]):  # noqa: ANN202
        async def build(ctx) -> None:  # noqa: ANN001
            await asyncio.sleep(0.2)
            ctx.save(_out(topics), tokens=1000)

        return build

    async def failing(ctx) -> None:  # noqa: ANN001
        raise RuntimeError("만들기 실패")

    def start(build, now: datetime = NOW, key: str = "K-new", share: float = 0.1) -> dict:  # noqa: ANN001
        return bv.start(env, "재현", build, now=now, llm_share=share,
                        detect=bv.Detect(as_of=now - timedelta(minutes=5), key=key))  # fmt: skip

    name = case["name"]
    try:
        if name == "daily_limit":
            put(["A"], timedelta(hours=6), "auto", "K1")
            for h, k in ((5, "K2"), (4, "K3"), (3, "K4")):
                put(["A"], timedelta(hours=h), "manual", k, "재현")
            got = start(saver(["A"]))
            return {"blocked": (got.get("blocked") or {}).get("code"),
                    "used": bv.regen_used(env, NOW)}  # fmt: skip
        if name == "cooldown_after_auto":
            put(["A"], timedelta(minutes=30), "auto", "K1")
            got = start(saver(["A"]))
            blocked = got.get("blocked") or {}
            text = blocked.get("text", "")
            return {"blocked": blocked.get("code"), "text_has_time": "12:30" in text}
        if name == "llm_70":
            got = start(saver(["A"]), share=0.72)
            return {"blocked": (got.get("blocked") or {}).get("code")}
        if name == "no_new_data":
            put(["A"], timedelta(hours=2), "auto", "K-same")
            got = start(saver(["B"]), key="K-same")
            return {"blocked": (got.get("blocked") or {}).get("code"),
                    "used": bv.regen_used(env, NOW)}  # fmt: skip
        if name == "failure_keeps_previous":
            put(["책추천"], timedelta(hours=2), "auto", "K1")

            async def run() -> dict:
                start(failing, key="K2")
                await bv.wait_all()
                return bv.status(env, NOW)

            st = asyncio.run(run())
            latest = store.get(day, rules, NOW)
            return {"state": st.get("state"), "used": bv.regen_used(env, NOW),
                    "latest_topic": latest.insights[0].topic if latest else None}  # fmt: skip
        if name == "auto_not_counted":
            put(["A"], timedelta(hours=3), "auto", "K1")

            async def run() -> None:
                start(saver(["B"]), key="K2")
                await bv.wait_all()

            asyncio.run(run())
            return {"used": bv.regen_used(env, NOW)}
        if name == "lock_expired":
            store.claim_build("죽은 프로세스", "manual", NOW - timedelta(minutes=11))

            async def run() -> dict:
                got = start(saver(["A"]))
                await bv.wait_all()
                return got

            return {"started": asyncio.run(run()).get("status") == "started"}
        if name == "double_click":
            async def run() -> tuple[dict, dict]:
                first = start(saver(["A"]))
                second = start(saver(["A"]))
                await bv.wait_all()
                return first, second

            _, second = asyncio.run(run())
            return {"second": (second.get("blocked") or {}).get("code"),
                    "versions": len(store.versions(day, rules))}  # fmt: skip
        if name == "two_processes":
            at = time.time() + 6
            procs = []
            for who in ("server", "collector"):
                spec = tmp / f"{who}.json"
                spec.write_text(json.dumps({"name": who, "start_at": at, "env": env}), "utf-8")
                procs.append(subprocess.Popen(
                    [sys.executable, str(HERE / "worker.py"), str(spec)],
                    # 워커도 시험의 NOW로 판을 저장한다 (실제 날짜가 NOW와 달라도 같은 날짜로 센다)
                    env={**os.environ, **env, "PYTHONIOENCODING": "utf-8",
                         "BRIEF_REGEN_EVAL_NOW": NOW.isoformat()},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                ))  # fmt: skip
            outs = []
            for p in procs:
                stdout, stderr = p.communicate(timeout=120)
                lines = [x for x in stdout.splitlines() if x.startswith("{")]
                if not lines:
                    print(stderr[-1500:])
                    return None
                outs.append(json.loads(lines[-1]))
            if any(o.get("unsupported") for o in outs):
                return None
            return {"versions": len(store.versions(day, rules)),
                    "started": sum(o.get("status") == "started" for o in outs)}  # fmt: skip
        if name == "versions_kept":
            put(["A"], timedelta(hours=5), "auto", "K1")

            async def run() -> None:
                start(saver(["B"]), now=NOW - timedelta(hours=3), key="K2")
                await bv.wait_all()
                start(saver(["C"]), now=NOW, key="K3")
                await bv.wait_all()

            asyncio.run(run())
            rows = store.versions(day, rules)
            return {"versions": len(rows), "latest_version": max(r["version"] for r in rows)}
        if name == "rules_split":
            put(["A"], timedelta(hours=2), "auto", "K1", rules_="r1")
            put(["A"], timedelta(hours=1), "auto", "K2", rules_="r2")
            return {"r1": len(store.versions(day, "r1")), "r2": len(store.versions(day, "r2"))}
        if name == "same_insight_id":
            put(["책추천", "A"], timedelta(hours=3), "auto", "K1")
            put(["B", "책추천"], timedelta(hours=1), "manual", "K2", "재현")
            ids = [next(i.insight_id for i in store.version(day, rules, v).insights
                        if i.topic == "책추천") for v in (1, 2)]  # fmt: skip
            return {"same": ids[0] == ids[1]}
        if name == "briefs_table_latest":
            from data_agent import data_chat

            put(["첫판주제"], timedelta(hours=3), "auto", "K1")
            put(["두번째판주제"], timedelta(hours=1), "manual", "K2", "재현")
            part = data_chat.read_insight(env, NOW)
            return {"topic": part["facts"]["인사이트"][0]["주제"] if part else None}
        if name == "diff_counts":
            d = bv.diff(_out(["A", "B", "C"]), _out(["A", "C", "D", "E"]))
            return {"new": sorted(d["new"]), "gone": sorted(d["gone"])}
    finally:
        store.close()
    return None


def observe(tmp_path: Path) -> tuple[dict[str, float], dict]:
    cases = make.cases()
    got = {c["name"]: _run(c, tmp_path / c["name"]) for c in cases}
    return score.score(cases, got), got


GATES = dict.fromkeys(score.METRICS, 1.0)  # 모두 맞아야 함 (평가 정의 PR #851 dev: 0.0 / 0.0 / 0.0)


def test_brief_regen_passes_the_gates(tmp_path: Path) -> None:
    metrics, got = observe(tmp_path)
    print("brief_regen eval:", json.dumps(metrics, ensure_ascii=False))
    print("brief_regen cases:", json.dumps(got, ensure_ascii=False, default=str))
    assert set(metrics) == set(score.METRICS)
    for name, gate in GATES.items():
        assert metrics[name] >= gate, (name, metrics[name], got)
