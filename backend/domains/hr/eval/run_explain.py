"""계획 점검(`check_plan`) 끝에서 끝까지 평가: Dense 검색 + LLM 설명 문장.

골든 셋의 계획 점검 카드 50장을 `record_case`로 hr에 쌓고, 계획 점검 질의 45개를 `check_plan`으로
점검한다. 실제 Gemini 임베딩과 Claude 호출을 쓴다(키는 backend/.env 에서 읽고 출력하지 않는다).
사용: python run_explain.py
결과: results/explain.json (읽어 볼 표본 포함)
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault(
    "AGENT_TEMPLATE_MOCK", "1"
)  # 테스트용 토큰(mock:planning)을 쓴다. LLM·임베딩은 실제 호출이다
os.environ.setdefault("HR_MOCK", "1")

from fastapi.testclient import TestClient  # noqa: E402
from run_eval import HERE, load_env  # noqa: E402

from ax_agent_template.llm import LLMClient, LLMSettings  # noqa: E402
from ax_contracts.schemas import CheckPlanOutput  # noqa: E402
from hr_agent.config import HrSettings, ManualClock  # noqa: E402
from hr_agent.main import app  # noqa: E402
from hr_agent.narrative import Narrator  # noqa: E402
from hr_agent.retrieval.cards import build_dense_search  # noqa: E402
from hr_agent.services import build_services, set_services  # noqa: E402

REJECTED_IDS = {"gold_A1_05", "gold_A4_01", "gold_A4_03", "gold_A4_05", "gold_A6_03"}
SAMPLES = 10


def _body(skill: str, inp: dict, key: str, ctx: str) -> dict:
    return {
        "meta": {
            "context_id": ctx,
            "token": "mock:planning",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
            "idempotency_key": f"{ctx}:{key}",
        },
        "skill": skill,
        "input": inp,
    }


def _field(body: str, label: str) -> str:
    m = re.search(rf"^{label}: (.*)$", body, flags=re.M)
    return m.group(1).strip() if m else ""


def main() -> dict:
    env = load_env()
    cards = [c for c in json.loads((HERE / "cards.json").read_text("utf-8"))]
    cards = [c for c in cards if c["extra"]["function"] == "plan_check"]
    queries = [
        q
        for q in json.loads((HERE / "queries.json").read_text("utf-8"))
        if q["function"] == "plan_check"
    ]

    tmp = tempfile.mkdtemp()
    clock = ManualClock()
    dense = build_dense_search(str(Path(tmp) / "hr.db"), env)
    if dense is None:
        raise SystemExit("임베딩 설정이 없다: MEMORY_LLM_*·MEMORY_EMBED_MODEL")
    svc = build_services(HrSettings(mock=True), clock=clock, dense=dense)
    set_services(svc)
    client = TestClient(app)

    created: dict[str, str] = {}
    for n, c in enumerate(cards, 1):
        decision = _field(c["body"], "결정") or _field(c["body"], "정책")
        alts = [a.strip() for a in _field(c["body"], "검토한 대안").split(",") if a.strip()]
        rec = {
            "context_id": f"ctx_rec_{n:04d}",
            "kind": "decision_rejected" if c["card_id"] in REJECTED_IDS else "spec_confirmed",
            "summary": f"{c['title']}: {decision}",
            "decision_reason": _field(c["body"], "이유") or _field(c["body"], "예외"),
            "alternatives_considered": alts,
            "source_refs": [f"req:{n}", "area:payment"],
            "author": "planning",
        }
        res = client.post("/a2a", json=_body("record_case", rec, f"r{n}", rec["context_id"])).json()
        assert res["status"] == "completed", res
        created[c["card_id"]] = res["output"]["card_id"]

    svc.narrator = Narrator(LLMClient("hr", LLMSettings.from_env("hr", env)))  # 적재 후에 켠다

    rows, started = [], time.time()
    for n, q in enumerate(queries, 1):
        draft = {"title": q["query"][:60], "summary": q["query"], "area": "payment", "kind": "spec"}
        t0 = time.time()
        res = client.post(
            "/a2a", json=_body("check_plan", {"draft": draft}, f"c{n}", f"ctx_chk_{n:04d}")
        ).json()
        out = CheckPlanOutput.model_validate(res["output"])
        expected = [created[e] for e in q["expected"] if e in created]
        shown = [c.card_id for c in out.similar_cards]
        rows.append(
            {
                "query_id": q["query_id"],
                "type": q["type"],
                "query": q["query"],
                "expected": expected,
                "similar": shown,
                "no_related": out.no_related_cards,
                "verdict": out.verdict,
                "findings": [f.model_dump() for f in out.findings],
                "seconds": round(time.time() - t0, 2),
            }
        )

    answerable = [r for r in rows if r["expected"]]
    none = [r for r in rows if not r["expected"]]
    findings = [f for r in rows for f in r["findings"]]
    explained = [f for f in findings if f.get("explanation")]
    result = {
        "queries": len(rows),
        "answerable": len(answerable),
        "no_answer": len(none),
        "retrieval_recall_at_5": round(
            sum(1 for r in answerable if set(r["expected"]) & set(r["similar"])) / len(answerable),
            4,
        ),
        "no_answer_no_related": round(sum(1 for r in none if r["no_related"]) / len(none), 4),
        "findings": len(findings),
        "explained": len(explained),
        "explained_rate": round(len(explained) / len(findings), 4) if findings else None,
        "fallback_rate": round(1 - len(explained) / len(findings), 4) if findings else None,
        "avg_seconds_per_check": round(sum(r["seconds"] for r in rows) / len(rows), 2),
        "total_seconds": round(time.time() - started, 1),
        "samples": [
            {"query": r["query"], "findings": r["findings"]}
            for r in rows
            if any(f.get("explanation") for f in r["findings"])
        ][:SAMPLES],
    }
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "explain.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    set_services(None)
    return result


if __name__ == "__main__":
    r = main()
    for k, v in r.items():
        if k != "samples":
            print(f"{k}: {v}")
    for s in r["samples"]:
        print("\n질의:", s["query"])
        for f in s["findings"]:
            print(f"  [{f['code']}] {f['message']}")
            print(f"    설명: {f.get('explanation')}")
