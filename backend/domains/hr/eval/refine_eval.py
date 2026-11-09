"""8-2 Dense 보완 평가. 사용: python refine_eval.py <dev|test|holdout>

변형 V0~V3은 refine-rules.md 에 정의돼 있다. 기준값은 항상 개발용 질의로만 정하고,
선택한 split(dev, test, holdout)의 결과를 낸다. holdout(검증용)은 마지막에 한 번만 돌린다.
V3는 Claude를 부른다(질의 하나에 한 번, 결과는 .data/verify_cache.json 에 저장해 다시 부르지 않는다).
결과: results/refine_<split>.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict

from run_eval import (
    BACKEND,
    HERE,
    K,
    build_dense_index,
    candidate_scores,
    card_text,
    load_cards,
    load_env,
    per_query,
    summarize,
)

from ax_agent_template.llm import LLMClient, LLMSettings
from hr_agent.retrieval.verify import RelevanceVerifier

CACHE = BACKEND / ".data" / "verify_cache.json"
CALL_LIMIT = 200
DELTAS = [round(0.01 * i, 2) for i in range(1, 21)]


def top_ranked(scores: dict[str, float], threshold: float) -> list[tuple[str, float]]:
    order = sorted(scores.items(), key=lambda t: (-round(t[1], 4), t[0]))[:K]
    return [(k, v) for k, v in order if v > 0 and round(v, 4) >= threshold]


def objective(rows: list[dict], threshold: float) -> float:
    pq = [per_query(r, [k for k, _ in top_ranked(r["cos"], threshold)]) for r in rows]
    m = summarize(pq)
    return ((m["recall_at_5"] or 0) + (m["no_answer_accuracy"] or 0)) / 2


def pick_thresholds(dev: list[dict]) -> tuple[float, float]:
    """(V0, V1) 코사인 하한. 목적함수가 가장 큰 하한들의 가장 낮은 값과 가운데 값."""
    cands = sorted({0.0} | {round(v, 4) for r in dev for _, v in top_ranked(r["cos"], 0.0)})
    scored = [(t, objective(dev, t)) for t in cands]
    best = max(s for _, s in scored)
    plateau = [t for t, s in scored if s > best - 1e-9]
    return plateau[0], plateau[len(plateau) // 2]


def cut(ranked: list[tuple[str, float]], delta: float | None) -> list[tuple[str, float]]:
    if not ranked or delta is None:
        return ranked
    top = ranked[0][1]
    return [(k, v) for k, v in ranked if v >= top - delta - 1e-9]


def pick_delta(dev: list[dict], t1: float) -> float | None:
    """개발용에서 Recall@5가 V1과 같게 유지되는 가장 작은 δ. 없으면 자르지 않는다."""
    base = summarize([per_query(r, [k for k, _ in top_ranked(r["cos"], t1)]) for r in dev])
    for d in DELTAS:
        pq = [per_query(r, [k for k, _ in cut(top_ranked(r["cos"], t1), d)]) for r in dev]
        if summarize(pq)["recall_at_5"] >= base["recall_at_5"] - 1e-9:
            return d
    return None


def load_cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


async def verify_all(rows: list[dict], cands: dict[str, list[str]], texts: dict[str, str]) -> dict:
    """질의마다 한 번 Claude로 후보를 검증한다. 이미 저장된 것은 다시 부르지 않는다."""
    cache = load_cache()
    llm = LLMClient("hr", LLMSettings.from_env("hr", load_env()))
    verifier = RelevanceVerifier(llm)
    sem = asyncio.Semaphore(4)
    todo = [
        r
        for r in rows
        if cands[r["query_id"]] and f"{r['query_id']}|{','.join(cands[r['query_id']])}" not in cache
    ]
    if len(cache) + len(todo) > CALL_LIMIT:
        raise SystemExit(
            f"Claude 호출 상한 {CALL_LIMIT}회를 넘는다: 저장 {len(cache)} + 새로 {len(todo)}"
        )

    async def one(r: dict) -> None:
        async with sem:
            ids = cands[r["query_id"]]
            verdict = await verifier.verify(r["query"], [(i, texts[i]) for i in ids])
        if verdict is not None:  # 실패한 판정은 저장하지 않는다(다시 부를 수 있게)
            cache[f"{r['query_id']}|{','.join(ids)}"] = verdict

    await asyncio.gather(*(one(r) for r in todo))
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Claude 호출 {verifier.calls}회 (저장된 판정 누적 {len(cache)}건, 상한 {CALL_LIMIT})")
    return cache


def counts(pq: list[dict]) -> dict:
    ans = [p for p in pq if p["answerable"]]
    none = [p for p in pq if not p["answerable"]]
    return {
        "top1_correct": f"{sum(1 for p in ans if p['top1'])}/{len(ans)}",
        "no_answer_correct": f"{sum(1 for p in none if p['returned'] == 0)}/{len(none)}",
    }


def main(split: str) -> dict:
    cards = load_cards(620)
    queries = json.loads((HERE / "queries.json").read_text(encoding="utf-8"))
    holdout = json.loads((HERE / "queries_holdout.json").read_text(encoding="utf-8"))
    pools: dict[str, list[dict]] = defaultdict(list)
    for c in cards:
        pools[c["extra"]["function"]].append(c)
    texts = {c["card_id"]: card_text(c) for c in cards}
    dense = asyncio.run(build_dense_index("dense", cards, queries + holdout))

    def rows_of(qs: list[dict]) -> list[dict]:
        out = []
        for q in qs:
            sc = candidate_scores("dense", q["query"], pools[q["pool"]], dense, None)
            out.append({**q, "cos": sc["cosine"], "domain": "-"})
        return out

    dev = rows_of([q for q in queries if q["split"] == "dev"])
    t0, t1 = pick_thresholds(dev)
    delta = pick_delta(dev, t1)
    target = {
        "dev": dev,
        "test": rows_of([q for q in queries if q["split"] == "test"]),
        "holdout": rows_of(holdout),
    }[split]

    v0 = {r["query_id"]: top_ranked(r["cos"], t0) for r in target}
    v1 = {r["query_id"]: top_ranked(r["cos"], t1) for r in target}
    v2 = {r["query_id"]: cut(v1[r["query_id"]], delta) for r in target}
    cands = {q: [k for k, _ in v[:3]] for q, v in v2.items()}
    cache = asyncio.run(verify_all(target, cands, texts))
    v3, failed = {}, 0
    for r in target:
        qid = r["query_id"]
        key = f"{qid}|{','.join(cands[qid])}"
        if not cands[qid]:
            v3[qid] = []
            continue
        verdict = cache.get(key)
        if verdict is None:
            failed += 1
            v3[qid] = v2[qid]  # 검증 실패: V2 결과를 그대로 쓴다
        else:
            v3[qid] = [(k, s) for k, s in v2[qid][:3] if verdict.get(k)]

    variants = {"V0": v0, "V1": v1, "V2": v2, "V3": v3}
    result = {
        "split": split,
        "cards": len(cards),
        "params_chosen_on_dev": {"v0_threshold": t0, "v1_threshold": t1, "delta": delta},
        "verify_failures": failed,
        "variants": {},
    }
    for name, ranked in variants.items():
        pq = [per_query(r, [k for k, _ in ranked[r["query_id"]]]) for r in target]
        result["variants"][name] = {**summarize(pq), **counts(pq)}
    (HERE / "results" / f"refine_{split}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return result


def show(result: dict) -> None:
    p = result["params_chosen_on_dev"]
    print(
        f"split={result['split']}  기준값(개발용으로 선택) V0={p['v0_threshold']} V1={p['v1_threshold']} δ={p['delta']}"
    )
    print(f"검증 실패(V2로 대체): {result['verify_failures']}건")
    print(
        f"{'변형':<5}{'n':>4}{'Recall@1':>10}{'Recall@5':>10}{'MRR':>8}{'인용정확도':>10}{'관련없음':>9}  1위정답  무응답"
    )
    for name, m in result["variants"].items():

        def f(v):
            return "  -  " if v is None else f"{v * 100:5.1f}%"

        print(
            f"{name:<5}{m['n']:>4}{f(m['recall_at_1']):>10}{f(m['recall_at_5']):>10}{f(m['mrr']):>8}"
            f"{f(m['citation_precision']):>10}{f(m['no_answer_accuracy']):>9}"
            f"  {m['top1_correct']:>6}  {m['no_answer_correct']:>6}"
        )


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if which not in ("dev", "test", "holdout"):
        raise SystemExit("dev, test, holdout 중 하나")
    show(main(which))

# ruff: noqa: E501
