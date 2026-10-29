"""검색 방식 평가 (합성 골든 셋). 사용: python run_eval.py [방식 이름]

- 방식은 SCORERS에 이름으로 등록한다(지금은 현재 방식인 `dice`만).
  Dense·Sparse·Hybrid는 같은 자리에 더한다.
- 질의는 `pool`(같은 기능의 카드 풀) 안에서만 검색한다.
  상위 5개 중 점수가 임계값 이상인 것만 결과로 본다.
- 임계값은 개발용(dev) 질의로만 정한다. 최종 수치는 평가용(test) 질의로 낸다.
- 카드 비교 글은 제목과 본문이다.
  `tags`에는 군집 이름이 들어 있어 정답이 새어 나가므로 쓰지 않는다.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from hr_agent.util import similarity

HERE = Path(__file__).parent
K = 5

Scorer = Callable[[str, dict], float]


def card_text(card: dict) -> str:
    return f"{card['title']}\n{card['body']}"


SCORERS: dict[str, Scorer] = {
    "dice": lambda query, card: similarity(query, card_text(card)),
}


def rank(scorer: Scorer, query: str, pool: list[dict]) -> list[tuple[str, float]]:
    scored = [(c["card_id"], round(scorer(query, c), 4)) for c in pool]
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored[:K]


def returned(ranked: list[tuple[str, float]], threshold: float) -> list[str]:
    return [cid for cid, score in ranked if score >= threshold and score > 0]


def metrics(rows: list[dict], threshold: float) -> dict:
    ans = [r for r in rows if r["expected"]]
    none = [r for r in rows if not r["expected"]]
    hit = prec = top1 = mrr = 0.0
    for r in ans:
        got = returned(r["ranked"], threshold)
        exp = set(r["expected"])
        hit += 1.0 if exp & set(got) else 0.0
        top1 += 1.0 if got and got[0] in exp else 0.0
        mrr += next((1.0 / i for i, cid in enumerate(got, 1) if cid in exp), 0.0)
        prec += (len(exp & set(got)) / len(got)) if got else 0.0
    empty = sum(1 for r in none if not returned(r["ranked"], threshold))
    return {
        "n": len(rows),
        "n_answerable": len(ans),
        "n_no_answer": len(none),
        "recall_at_1": round(top1 / len(ans), 4) if ans else None,
        "recall_at_5": round(hit / len(ans), 4) if ans else None,
        "mrr": round(mrr / len(ans), 4) if ans else None,
        "citation_precision": round(prec / len(ans), 4) if ans else None,
        "no_answer_accuracy": round(empty / len(none), 4) if none else None,
    }


def pick_threshold(rows: list[dict]) -> float:
    """개발용 질의에서 (Recall@5 + 관련 없음 정확도) / 2가 가장 큰 임계값. 같으면 작은 쪽."""
    candidates = sorted({0.0} | {s for r in rows for _, s in r["ranked"]})
    best, best_t = -1.0, 0.0
    for t in candidates:
        m = metrics(rows, t)
        score = ((m["recall_at_5"] or 0) + (m["no_answer_accuracy"] or 0)) / 2
        if score > best + 1e-9:
            best, best_t = score, t
    return best_t


def main(name: str) -> dict:
    scorer = SCORERS[name]
    cards = json.loads((HERE / "cards.json").read_text(encoding="utf-8"))
    queries = json.loads((HERE / "queries.json").read_text(encoding="utf-8"))
    pools: dict[str, list[dict]] = defaultdict(list)
    for c in cards:
        pools[c["extra"]["function"]].append(c)
    domain = {c["card_id"]: c["extra"]["domain"] for c in cards}

    rows = []
    for q in queries:
        ranked = rank(scorer, q["query"], pools[q["pool"]])
        doms = {domain[e] for e in q["expected"]}
        rows.append(
            {
                **q,
                "ranked": ranked,
                "domain": doms.pop() if len(doms) == 1 else ("혼합" if doms else "-"),
            }
        )

    dev = [r for r in rows if r["split"] == "dev"]
    test = [r for r in rows if r["split"] == "test"]
    t = pick_threshold(dev)

    def by(rows_: list[dict], key: str) -> dict:
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows_:
            groups[r[key]].append(r)
        return {k: metrics(v, t) for k, v in sorted(groups.items())}

    result = {
        "method": name,
        "k": K,
        "threshold_chosen_on_dev": t,
        "dev": metrics(dev, t),
        "test": metrics(test, t),
        "test_without_threshold": metrics(test, 0.0),
        "test_by_function": by(test, "function"),
        "test_by_type": by(test, "type"),
        "test_by_domain": by([r for r in test if r["expected"]], "domain"),
    }
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{name}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return result


def show(result: dict) -> None:
    print(
        f"방식: {result['method']}   임계값(개발용으로 선택): {result['threshold_chosen_on_dev']}"
    )

    def line(label: str, m: dict) -> None:
        fmt = lambda v: "  -  " if v is None else f"{v * 100:5.1f}%"  # noqa: E731
        print(
            f"  {label:<22} n={m['n']:<3} Recall@1 {fmt(m['recall_at_1'])}"
            f"  Recall@5 {fmt(m['recall_at_5'])}  MRR {fmt(m['mrr'])}"
            f"  인용정확도 {fmt(m['citation_precision'])}"
            f"  관련없음 {fmt(m['no_answer_accuracy'])}"
        )

    print("[개발용 (임계값을 정한 데이터, 참고용)]")
    line("dev", result["dev"])
    print("[평가용 (최종 수치)]")
    line("test", result["test"])
    line("test, 임계값 없음", result["test_without_threshold"])
    for title, key in (
        ("기능별", "test_by_function"),
        ("유형별", "test_by_type"),
        ("도메인별(정답 있음)", "test_by_domain"),
    ):
        print(f"[평가용 {title}]")
        for k, m in result[key].items():
            line(k, m)


if __name__ == "__main__":
    show(main(sys.argv[1] if len(sys.argv) > 1 else "dice"))
