"""검색 방식 비교와 선택. 사용: python compare.py [카드 수]

results/<방식>_<카드 수>.json 의 평가용 질의별 결과로, compare-rules.md 의 규칙을 그대로 적용한다.
- 방식 간 차이는 정답 있는 질의의 역순위(RR) 차이를 짝지은 부트스트랩(1000회)으로 신뢰구간을 낸다.
- 뚜렷하다 = 신뢰구간 하한이 0보다 크다.
결과: results/compare_<카드 수>.json
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
ORDER = ("dice", "sparse", "dense", "hybrid")  # 복잡도 순서
BOOT = 1000
NO_ANSWER_DROP = 0.05  # 관련 없음 정확도가 이만큼(5%p) 넘게 떨어지면 채택하지 않는다


def load(method: str, size: int) -> dict:
    return json.loads((HERE / "results" / f"{method}_{size}.json").read_text(encoding="utf-8"))


def paired_ci(a: dict[str, float], b: dict[str, float], seed: int = 0) -> dict:
    """b - a 의 평균 차이와 95% 신뢰구간(질의를 다시 뽑는 짝지은 부트스트랩)."""
    ids = sorted(set(a) & set(b))
    diffs = [b[i] - a[i] for i in ids]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(BOOT))
    return {
        "n": len(diffs),
        "mean": round(sum(diffs) / len(diffs), 4),
        "ci_low": round(means[int(0.025 * BOOT)], 4),
        "ci_high": round(means[int(0.975 * BOOT) - 1], 4),
    }


def rr_by_query(result: dict) -> dict[str, float]:
    return {p["query_id"]: p["rr"] for p in result["per_query"] if p["answerable"]}


def decide(size: int) -> dict:
    res = {m: load(m, size) for m in ORDER}
    rr = {m: rr_by_query(r) for m, r in res.items()}
    current, steps = "dice", []
    for cand in ORDER[1:]:
        if cand == "hybrid" and current != "dense":
            steps.append(
                {
                    "candidate": cand,
                    "against": None,
                    "moved": False,
                    "note": "hybrid는 현재 방식이 dense일 때만 후보",
                }
            )
            continue
        ci = paired_ci(rr[current], rr[cand])
        better = ci["ci_low"] > 0
        na_cur = res[current]["test"]["no_answer_accuracy"] or 0
        na_new = res[cand]["test"]["no_answer_accuracy"] or 0
        blocked = better and cand in ("dense", "hybrid") and na_new < na_cur - NO_ANSWER_DROP
        moved = better and not blocked
        steps.append(
            {
                "candidate": cand,
                "against": current,
                "mrr_diff": ci,
                "moved": moved,
                "blocked_by_no_answer": blocked,
                "no_answer_current": na_cur,
                "no_answer_candidate": na_new,
            }
        )
        if moved:
            current = cand
    return {
        "cards": size,
        "final": current,
        "steps": steps,
        "test": {m: {k: v for k, v in r["test"].items()} for m, r in res.items()},
        "test_by_type": {m: r["test_by_type"] for m, r in res.items()},
        "test_by_function": {m: r["test_by_function"] for m, r in res.items()},
        "params": {m: r["params_chosen_on_dev"] for m, r in res.items()},
    }


def show(d: dict) -> None:
    def f(v: float | None) -> str:
        return "  -  " if v is None else f"{v * 100:5.1f}%"

    print(f"카드 {d['cards']}장, 평가용 질의 기준")
    print(
        f"{'방식':<8}{'Recall@1':>10}{'Recall@5':>10}{'MRR':>9}{'인용정확도':>11}{'관련없음':>10}"
    )
    for m in ORDER:
        t = d["test"][m]
        print(
            f"{m:<8}{f(t['recall_at_1']):>10}{f(t['recall_at_5']):>10}{f(t['mrr']):>9}"
            f"{f(t['citation_precision']):>11}{f(t['no_answer_accuracy']):>10}"
        )
    print("선택 과정(MRR 차이, 95% 신뢰구간):")
    for s in d["steps"]:
        if s["against"] is None:
            print(f"  {s['candidate']}: {s['note']}")
            continue
        c = s["mrr_diff"]
        verdict = (
            "채택"
            if s["moved"]
            else ("차단(무응답 하락)" if s["blocked_by_no_answer"] else "차이 없음")
        )
        print(
            f"  {s['candidate']} vs {s['against']}: {c['mean'] * 100:+.1f}%p "
            f"[{c['ci_low'] * 100:+.1f}, {c['ci_high'] * 100:+.1f}] -> {verdict}"
        )
    print(f"최종 방식: {d['final']}")


if __name__ == "__main__":
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    out = decide(size)
    (HERE / "results" / f"compare_{size}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    show(out)
