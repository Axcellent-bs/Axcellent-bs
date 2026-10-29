"""개발용:평가용 비율(5:5, 4:6)과 카드 규모(620장, 1000장)에 따라 "올바르게 나온 개수"가 어떻게 달라지는지 본다.

사용: python split_eval.py  ->  results/split_eval.json (임베딩 호출 없음, 저장된 벡터만 사용)

올바름의 기준(질의 하나마다):
- 정답이 있는 질의: 1위 결과가 정답 카드이면 올바름
- 정답이 없는 질의: 아무것도 내놓지 않으면 올바름
기준값은 개발용 질의로만 정하고, 올바른 개수는 평가용 질의에서만 센다.
같은 비율에서 나누는 방식을 바꿔 가며 REPEAT번 반복하고 평균과 최소·최대를 보고한다.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from statistics import mean

from run_eval import (
    HERE,
    SparseIndex,
    asyncio,
    build_dense_index,
    candidate_scores,
    card_text,
    load_cards,
    per_query,
    pick_params,
    ranked_for,
)

SIZES = (620, 1000)
METHODS = ("dice", "sparse", "dense", "hybrid")
RATIOS = ((5, 5), (4, 6))  # 개발용 : 평가용
REPEAT = 30
SEED = 11


def split_ids(queries: list[dict], dev_share: float, rng: random.Random) -> set[str]:
    """기능×유형 묶음마다 같은 비율로 개발용 질의를 뽑는다."""
    groups: dict[tuple, list[str]] = defaultdict(list)
    for q in queries:
        groups[(q["function"], q["type"])].append(q["query_id"])
    dev: set[str] = set()
    for ids in groups.values():
        ids = sorted(ids)
        rng.shuffle(ids)
        dev |= set(ids[: max(1, round(dev_share * len(ids)))])
    return dev


def collect(method: str, size: int, queries: list[dict]) -> list[dict]:
    cards = load_cards(size)
    pools: dict[str, list[dict]] = defaultdict(list)
    for c in cards:
        pools[c["extra"]["function"]].append(c)
    dense = (
        asyncio.run(build_dense_index("dense", cards, queries))
        if method in ("dense", "hybrid")
        else None
    )
    sparse = {f: SparseIndex({c["card_id"]: card_text(c) for c in p}) for f, p in pools.items()}
    rows = []
    for q in queries:
        scores = candidate_scores(method, q["query"], pools[q["pool"]], dense, sparse[q["pool"]])
        rows.append({**q, "scores": scores, "domain": "-"})
    return rows


def run() -> dict:
    queries = json.loads((HERE / "queries.json").read_text(encoding="utf-8"))
    out: dict = {"repeat": REPEAT, "results": {}}
    for size in SIZES:
        for method in METHODS:
            rows = collect(method, size, queries)
            for dev_n, test_n in RATIOS:
                rng = random.Random(SEED)
                counts, accs = [], []
                n_test = 0
                for _ in range(REPEAT):
                    dev_ids = split_ids(queries, dev_n / (dev_n + test_n), rng)
                    dev = [r for r in rows if r["query_id"] in dev_ids]
                    test = [r for r in rows if r["query_id"] not in dev_ids]
                    params = pick_params(method, dev)
                    pq = [per_query(r, ranked_for(method, r["scores"], params)) for r in test]
                    ok = sum(
                        1 for p in pq if (p["top1"] if p["answerable"] else p["returned"] == 0)
                    )
                    counts.append(ok)
                    accs.append(ok / len(pq))
                    n_test = len(pq)
                out["results"][f"{size}|{method}|{dev_n}:{test_n}"] = {
                    "test_queries": n_test,
                    "correct_mean": round(mean(counts), 1),
                    "correct_min": min(counts),
                    "correct_max": max(counts),
                    "accuracy_mean": round(mean(accs), 4),
                    "per_1000": round(mean(accs) * 1000),
                }
                print(f"{size}장 {method} {dev_n}:{test_n} 완료", flush=True)
    (HERE / "results" / "split_eval.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return out


if __name__ == "__main__":
    res = run()
    print(
        "\n카드 규모 | 방식 | 개발:평가 | 평가 질의 수 | 올바른 개수(평균, 최소~최대) | 정확도 | 1000개당"
    )
    for key, v in res["results"].items():
        size, method, ratio = key.split("|")
        print(
            f"{size}장 | {method} | {ratio} | {v['test_queries']}개 | "
            f"{v['correct_mean']} ({v['correct_min']}~{v['correct_max']}) | "
            f"{v['accuracy_mean'] * 100:.1f}% | {v['per_1000']}개"
        )

# ruff: noqa: E501
