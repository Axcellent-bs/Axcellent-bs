"""방해 카드 점검: 정답 카드보다 위에 올라온 방해 카드를 찾아 사람이 읽을 수 있게 모은다.

질의마다 Dense·Sparse 두 방식에서 정답 카드(가장 높은 것)보다 높은 순위의 방해 카드를 모은다.
이 목록을 읽고 "이 질의의 정답이 될 수 있는 카드"가 있으면 방해 카드에서 뺀다(거짓 오답 방지).
점검 대상을 두 방식의 합집합으로 잡아서 어느 한 방식에 유리하게 걸러지지 않게 한다.
사용: python audit_distractors.py [--no-dense]  ->  results/audit_distractors.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict

from run_eval import HERE, build_dense_index, card_text, load_cards

from hr_agent.retrieval.dense import cosine, mask, text_key
from hr_agent.retrieval.sparse import SparseIndex


def main(use_dense: bool) -> None:
    cards = load_cards(620)
    queries = json.loads((HERE / "queries.json").read_text(encoding="utf-8"))
    pools: dict[str, list[dict]] = defaultdict(list)
    for c in cards:
        pools[c["extra"]["function"]].append(c)
    by_id = {c["card_id"]: c for c in cards}
    sparse = {f: SparseIndex({c["card_id"]: card_text(c) for c in p}) for f, p in pools.items()}
    dense = asyncio.run(build_dense_index("dense", cards, queries)) if use_dense else None

    flagged: dict[str, dict] = {}
    for q in queries:
        if not q["expected"]:
            continue
        pool = pools[q["pool"]]
        ids = [c["card_id"] for c in pool]
        rankings = {"sparse": sparse[q["pool"]].scores(q["query"], ids)}
        if dense is not None:
            qv = dense.vector(text_key(mask(q["query"])))
            rankings["dense"] = {i: cosine(qv, dense.vector(i)) for i in ids}
        for name, scores in rankings.items():
            order = [k for k, _ in sorted(scores.items(), key=lambda t: (-t[1], t[0]))]
            best_gold = min(order.index(e) for e in q["expected"])
            for pos, key in enumerate(order[:best_gold]):
                if key.startswith("dist_"):
                    item = flagged.setdefault(
                        f"{q['query_id']}|{key}",
                        {
                            "query_id": q["query_id"],
                            "query": q["query"],
                            "gold": [by_id[e]["title"] for e in q["expected"]],
                            "distractor_id": key,
                            "distractor": card_text(by_id[key]).replace("\n", " / "),
                            "ranks": {},
                        },
                    )
                    item["ranks"][name] = pos + 1
    out = sorted(flagged.values(), key=lambda x: (x["query_id"], x["distractor_id"]))
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "audit_distractors.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        f"정답보다 위에 올라온 방해 카드: {len(out)}건 ({len({o['query_id'] for o in out})}개 질의)"
    )


if __name__ == "__main__":
    main("--no-dense" not in sys.argv)
