"""총괄 holdout 시험. 사용: python holdout_eval.py <train|test>

규칙과 판정 기준은 holdout-rules.md. 가짜 부서에 응답 순서를 정해 주고 총괄이 처리한 결과를
독립적으로 다시 짠 기준표(오라클)와 비교한다. 결과: results/holdout_<split>.json
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.plans.scenario_a import scenario_a

HERE = Path(__file__).parent
SEEDS = {"train": 20261110, "test": 2026111002}
N_CASES = 60

# 장애를 넣을 수 있는 단계: id → (부서, skill). 같은 (부서, skill)을 두 단계가 쓰는 것은 뺐다
FAULTABLE = {
    "diagnose": ("infra", "diagnose_incident"),
    "similar_cases": ("hr", "find_similar_cases"),
    "impact": ("data", "impact_analysis"),
    "root_cause": ("data", "root_cause_hypotheses"),
    "cause_check": ("planning", "create_ticket"),
    "rollback": ("infra", "rollback_deployment"),
    "related_prs": ("dev", "find_related_prs"),
    "link_incident": ("dev", "link_incident"),
    "compensation": ("support", "request_incident_compensation"),
    "finance_record": ("finance", "record_incident_finance"),
    "kpi": ("data", "kpi_summary"),
    "resolution": ("hr", "record_resolution"),
}
PATTERNS = [
    ["fail"],
    ["fail", "fail"],
    ["fail", "fail", "fail"],
    ["timeout"],
    ["timeout", "timeout"],
    ["fail", "timeout"],
    ["timeout", "fail", "fail", "fail"],
    ["timeout", "ok"],
    ["fail", "ok"],
    ["fail", "fail", "ok"],
]

# ---- 오라클: plan 설계에서 따로 옮겨 적은 단계 관계 (엔진 코드를 부르지 않는다) ----
# id: (after = 모두 성공해야 실행, also_after = 끝나기만 하면 실행)
ORDER = {
    "diagnose": ((), ()),
    "similar_cases": ((), ()),
    "impact": ((), ()),
    "root_cause": (("impact",), ()),
    "cause_check": ((), ("root_cause",)),
    "rollback": (("diagnose", "impact"), ()),
    "related_prs": (("diagnose", "impact"), ()),
    "link_incident": (("related_prs",), ()),
    "notice": (("diagnose", "impact"), ()),
    "publish_notice": (("notice",), ()),
    "compensation": (("impact",), ()),
    "recovery": (("rollback",), ()),
    "recovered_draft": (("recovery",), ()),
    "recovered_notice": (("recovered_draft",), ()),
    "finance_record": ((), ("compensation", "recovery")),
    "kpi": ((), ("recovered_notice",)),
    "record": (
        (),
        (
            "recovered_notice",
            "link_incident",
            "compensation",
            "finance_record",
            "kpi",
            "root_cause",
        ),
    ),
    "resolution": ((), ("record",)),
}
REQUIRED = {
    "diagnose", "impact", "rollback", "notice", "publish_notice",
    "recovery", "recovered_draft", "recovered_notice", "record",
}  # fmt: skip


def make_cases(split: str) -> list[dict[str, list[str]]]:
    rng = random.Random(SEEDS[split])
    cases = []
    for _ in range(N_CASES):
        picked = rng.sample(sorted(FAULTABLE), rng.randint(1, 3))
        cases.append({sid: list(rng.choice(PATTERNS)) for sid in picked})
    return cases


def run_script(script: list[str]) -> tuple[int, str]:
    """응답 순서대로 호출했을 때 (호출 횟수, 최종 상태). 실패는 2번, 마감 초과는 1번 다시 시도한다."""
    failed = canceled = attempts = 0
    for behavior in [*script, *["ok"] * 10]:
        attempts += 1
        if behavior == "ok":
            return attempts, "completed"
        if behavior == "timeout":
            if canceled < 1:
                canceled += 1
                continue
            return attempts, "failed"
        if failed < 2:
            failed += 1
            continue
        return attempts, "failed"
    raise AssertionError("unreachable")


def oracle(faults: dict[str, list[str]], base: dict[str, str]) -> dict:
    status: dict[str, str] = {}
    attempts: dict[str, int] = {}
    for sid, (after, _also) in ORDER.items():
        if any(status[d] != "completed" for d in after):
            status[sid], attempts[sid] = "skipped", 0
        elif sid == "cause_check" and status["root_cause"] != "completed":
            # 원인 후보가 없으면(원인 추정이 실패·건너뜀) 사람에게 원인 확인을 요청한다
            attempts[sid], status[sid] = run_script(faults.get(sid, []))
        elif base[sid] != "completed" and sid not in FAULTABLE:
            status[sid], attempts[sid] = base[sid], 0  # 기준 실행에서 이미 달랐던 단계는 그대로
        elif base[sid] == "skipped":
            status[sid], attempts[sid] = "skipped", 0  # 판단 단계로 건너뛰는 단계
        elif sid in faults:
            attempts[sid], status[sid] = run_script(faults[sid])
        else:
            status[sid], attempts[sid] = "completed", 1
    ok = all(status[s] == "completed" for s in REQUIRED)
    return {"status": status, "attempts": attempts, "incident": "resolved" if ok else "degraded"}


async def run_engine(faults: dict[str, list[str]]) -> dict:
    harness = Harness()
    engine = await harness.start()
    for sid, script in faults.items():
        dept, skill = FAULTABLE[sid]
        harness.fakes.script(dept, skill, *script)
    inc = new_incident()
    await engine.run(inc, scenario_a())
    return {
        "status": {s: r.status for s, r in inc.steps.items() if s in ORDER},
        "attempts": {s: r.attempts for s, r in inc.steps.items() if s in ORDER},
        "incident": inc.status,
    }


async def main(split: str) -> dict:
    base_run = await run_engine({})
    base = base_run["status"]
    cases = make_cases(split)
    step_ok = step_n = att_ok = att_n = case_ok = inc_ok = 0
    mismatches = []
    for i, faults in enumerate(cases):
        want, got = oracle(faults, base), await run_engine(faults)
        diffs = []
        for sid in ORDER:
            step_n += 1
            step_ok += want["status"][sid] == got["status"].get(sid)
            if want["status"][sid] != got["status"].get(sid):
                diffs.append((sid, "status", want["status"][sid], got["status"].get(sid)))
            if want["status"][sid] != "skipped":
                att_n += 1
                att_ok += want["attempts"][sid] == got["attempts"].get(sid)
                if want["attempts"][sid] != got["attempts"].get(sid):
                    diffs.append((sid, "attempts", want["attempts"][sid], got["attempts"].get(sid)))
        inc_ok += want["incident"] == got["incident"]
        if want["incident"] != got["incident"]:
            diffs.append(("incident", "status", want["incident"], got["incident"]))
        case_ok += not diffs
        if diffs:
            mismatches.append({"case": i, "faults": faults, "diffs": diffs})
    result = {
        "split": split,
        "seed": SEEDS[split],
        "cases": len(cases),
        "baseline_statuses": base,
        "step_status_accuracy": round(step_ok / step_n, 4),
        "step_status": f"{step_ok}/{step_n}",
        "attempts_accuracy": round(att_ok / att_n, 4),
        "attempts": f"{att_ok}/{att_n}",
        "incident_status_accuracy": round(inc_ok / len(cases), 4),
        "incident_status": f"{inc_ok}/{len(cases)}",
        "case_exact_match": round(case_ok / len(cases), 4),
        "case_exact": f"{case_ok}/{len(cases)}",
        "mismatches": mismatches,
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"holdout_{split}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "train"
    if which not in SEEDS:
        raise SystemExit("train 또는 test")
    out = asyncio.run(main(which))
    print({k: v for k, v in out.items() if k not in ("mismatches", "baseline_statuses")})
    print("baseline:", out["baseline_statuses"])
    for m in out["mismatches"][:8]:
        print("불일치", m["case"], m["faults"], m["diffs"])
