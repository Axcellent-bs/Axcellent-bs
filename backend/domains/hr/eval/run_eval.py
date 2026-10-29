"""검색 방식 평가 (합성 골든 셋). 사용: python run_eval.py <방식> [카드 수]

- 방식: `dice`(현재 방식), `sparse`(BM25), `dense`(임베딩 코사인), `hybrid`(Dense + Sparse, RRF).
  `dense-mock`은 네트워크 없이 배선만 확인한다.
- 카드 수: 120(기본, `cards.json`)부터 620(`cards.json` + 방해 카드 500장)까지.
  방해 카드는 기능별로 같은 비율로 앞에서부터 잘라 쓴다(작은 규모가 큰 규모에 포함된다).
- 질의는 `pool`(같은 기능의 카드 풀) 안에서만 검색한다. 풀은 카드 수만큼 커진다.
- 기준값은 개발용(dev) 질의로만 정한다. 최종 수치는 평가용(test) 질의로 낸다.
- 카드 비교 글은 제목과 본문이다. `tags`에는 군집 이름이 들어 있어 정답이 새어 나가므로 쓰지 않는다.
- `dense`·`hybrid`는 backend/.env 의 MEMORY_LLM_*·MEMORY_EMBED_MODEL(없으면 LLM_*·EMBED_MODEL)을
  읽고, 만든 벡터는 backend/.data/eval_vectors.db 에 저장해 다시 쓴다(키는 출력하지 않는다).
- 결과: results/<방식>_<카드 수>.json (질의별 결과 포함, 방식 간 비교는 compare.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from ax_agent_template.llm import LLMClient, LLMSettings, MockLLMClient
from hr_agent.retrieval.dense import DenseIndex, LLMEmbedder, cosine, mask, text_key
from hr_agent.retrieval.hybrid import fuse
from hr_agent.retrieval.sparse import SparseIndex
from hr_agent.util import similarity

HERE = Path(__file__).parent
BACKEND = HERE.parents[2]
K = 5
METHODS = ("dice", "sparse", "dense", "hybrid", "dense-mock")
GRID = 25  # 기준값 후보 수(개발용 점수의 분위수)


def card_text(card: dict) -> str:
    return f"{card['title']}\n{card['body']}"


def load_env() -> dict[str, str]:
    """프로세스 환경에 backend/.env 의 값을 얹는다. 파일 안의 값은 출력하지 않는다."""
    env = dict(os.environ)
    path = BACKEND / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #")[0].strip().strip('"')
            if value:
                env[key.strip()] = value
    return env


def load_cards(size: int) -> list[dict]:
    """골든 카드 120장에 방해 카드를 더해 `size`장으로 만든다.

    방해 카드는 기능(계획 점검·총괄 이관·인수인계)마다 같은 비율로 앞에서부터 자른다.
    그래서 작은 규모의 카드는 큰 규모에 그대로 들어가고, 한 번 만든 벡터를 다시 쓴다.
    """
    cards = json.loads((HERE / "cards.json").read_text(encoding="utf-8"))
    # 620장까지는 기본 방해 카드 500장, 그 이상은 규모 비교 실험용 880장 파일을 쓴다
    extra_name = "cards_distractors.json" if size <= 620 else "cards_distractors_880.json"
    extra_all = json.loads((HERE / extra_name).read_text(encoding="utf-8"))
    want = size - len(cards)
    if want < 0 or want > len(extra_all):
        raise SystemExit(f"카드 수는 {len(cards)}~{len(cards) + len(extra_all)} 사이")
    by_func: dict[str, list[dict]] = defaultdict(list)
    for c in extra_all:
        by_func[c["extra"]["function"]].append(c)
    picked: list[dict] = []
    left = want
    funcs = list(by_func)
    for i, f in enumerate(funcs):
        take = left if i == len(funcs) - 1 else round(want * len(by_func[f]) / len(extra_all))
        picked += by_func[f][:take]
        left -= take
    return cards + picked


async def build_dense_index(name: str, cards: list[dict], queries: list[dict]) -> DenseIndex:
    """카드와 질의를 색인(이미 있으면 재사용)한다."""
    data = BACKEND / ".data"
    data.mkdir(exist_ok=True)
    if name == "dense-mock":
        client, model, path = MockLLMClient(), "mock", data / "eval_vectors_mock.db"
    else:
        settings = LLMSettings.from_env("memory", load_env())
        if not settings.embed_model:
            raise SystemExit("임베딩 모델 설정이 없다: MEMORY_LLM_EMBED_MODEL 또는 EMBED_MODEL")
        client, model = LLMClient("memory", settings), settings.embed_model
        path = data / "eval_vectors.db"
    index = DenseIndex(str(path), LLMEmbedder(client), model)
    items = [(c["card_id"], card_text(c)) for c in cards]
    items += [(text_key(mask(q["query"])), mask(q["query"])) for q in queries]
    created = await index.index(items)
    print(f"임베딩 {created}건 새로 만듦 (전체 {len(items)}건, 모델 {model})")
    return index


# --- 점수 계산 ------------------------------------------------------------------------


def candidate_scores(
    method: str, query: str, pool: list[dict], dense: DenseIndex | None, sparse: SparseIndex | None
) -> dict[str, dict[str, float]]:
    """질의 하나에 대한 후보별 점수. 방식에 필요한 점수만 채운다."""
    ids = [c["card_id"] for c in pool]
    out: dict[str, dict[str, float]] = {}
    if method == "dice":
        out["score"] = {c["card_id"]: similarity(query, card_text(c)) for c in pool}
    if method in ("sparse", "hybrid"):
        out["sparse"] = sparse.scores(query, ids)
    if method in ("dense", "dense-mock", "hybrid"):
        q = dense.vector(text_key(mask(query)))
        out["cosine"] = {i: cosine(q, dense.vector(i)) for i in ids}
    return out


def ranked_for(method: str, scores: dict[str, dict[str, float]], params: tuple) -> list[str]:
    """기준값(params)을 적용한 결과 카드 id 목록(순위순, 최대 K개)."""
    if method == "hybrid":
        min_cos, min_sp = params
        cos, sp = scores["cosine"], scores["sparse"]
        # RRF 순위는 기준값과 무관해서 한 번만 계산해 두고,
        # 기준값은 걸러내는 데만 쓴다(fuse와 같은 결과)
        order = scores.get("_order")
        if order is None:
            order = [h.key for h in fuse(cos, sp, k=len(cos) + len(sp))]
            scores["_order"] = order
        keep = [
            k
            for k in order
            if (cos.get(k, 0) >= min_cos and cos.get(k, 0) > 0)
            or (sp.get(k, 0) >= min_sp and sp.get(k, 0) > 0)
        ]
        return keep[:K]
    only = scores.get("score") or scores.get("sparse") or scores["cosine"]
    ordered = sorted(only.items(), key=lambda t: (-round(t[1], 4), t[0]))[:K]
    return [k for k, s in ordered if s > 0 and round(s, 4) >= params[0]]


def param_grid(method: str, dev_rows: list[dict]) -> list[tuple]:
    def quantiles(values: list[float]) -> list[float]:
        vals = sorted(set(round(v, 4) for v in values))
        if len(vals) <= GRID:
            return vals
        return sorted({vals[round(i * (len(vals) - 1) / (GRID - 1))] for i in range(GRID)})

    def top_values(key: str) -> list[float]:
        out = []
        for r in dev_rows:
            vals = sorted(r["scores"][key].values(), reverse=True)[:K]
            out += vals
        return out

    if method == "hybrid":
        cos = [0.0] + quantiles(top_values("cosine")) + [2.0]  # 2.0은 "코사인으로는 안 거름"
        sp = [0.0] + quantiles(top_values("sparse")) + [1e9]  # 1e9는 "BM25로는 안 거름"
        return [(c, s) for c in cos for s in sp if not (c == 2.0 and s == 1e9)]
    key = {"dice": "score", "sparse": "sparse"}.get(method, "cosine")
    # 점수가 하나인 방식은 개발용 점수 전부를 후보로 쓴다(조합이 없어서 분위수로 줄일 필요가 없다)
    return [(t,) for t in [0.0] + sorted(set(round(v, 4) for v in top_values(key)))]


# --- 지표 ------------------------------------------------------------------------------


def per_query(row: dict, got: list[str]) -> dict:
    exp = set(row["expected"])
    return {
        "query_id": row["query_id"],
        "split": row["split"],
        "function": row["function"],
        "type": row["type"],
        "domain": row["domain"],
        "answerable": bool(exp),
        "returned": len(got),
        "rr": next((1.0 / i for i, c in enumerate(got, 1) if c in exp), 0.0),
        "top1": bool(got and got[0] in exp),
        "hit5": bool(exp & set(got)),
        "precision": (len(exp & set(got)) / len(got)) if got and exp else 0.0,
    }


def summarize(pq: list[dict]) -> dict:
    ans = [r for r in pq if r["answerable"]]
    none = [r for r in pq if not r["answerable"]]

    def mean(rows: list[dict], key: str) -> float | None:
        return round(sum(float(r[key]) for r in rows) / len(rows), 4) if rows else None

    return {
        "n": len(pq),
        "n_answerable": len(ans),
        "n_no_answer": len(none),
        "recall_at_1": mean(ans, "top1"),
        "recall_at_5": mean(ans, "hit5"),
        "mrr": mean(ans, "rr"),
        "citation_precision": mean(ans, "precision"),
        "no_answer_accuracy": round(sum(1 for r in none if r["returned"] == 0) / len(none), 4)
        if none
        else None,
    }


def objective(pq: list[dict]) -> float:
    m = summarize(pq)
    return ((m["recall_at_5"] or 0) + (m["no_answer_accuracy"] or 0)) / 2


def pick_params(method: str, dev_rows: list[dict]) -> tuple:
    """개발용 질의에서 (Recall@5 + 관련 없음 정확도) / 2가 가장 큰 기준값. 같으면 먼저 나온 쪽."""
    best, best_p = -1.0, None
    for params in param_grid(method, dev_rows):
        pq = [per_query(r, ranked_for(method, r["scores"], params)) for r in dev_rows]
        score = objective(pq)
        if score > best + 1e-9:
            best, best_p = score, params
    return best_p


def main(method: str, size: int = 120) -> dict:
    cards = load_cards(size)
    queries = json.loads((HERE / "queries.json").read_text(encoding="utf-8"))
    pools: dict[str, list[dict]] = defaultdict(list)
    for c in cards:
        pools[c["extra"]["function"]].append(c)
    domain = {c["card_id"]: c["extra"]["domain"] for c in cards}

    dense = (
        asyncio.run(build_dense_index(method, cards, queries))
        if method in ("dense", "dense-mock", "hybrid")
        else None
    )
    sparse_by_pool = {
        f: SparseIndex({c["card_id"]: card_text(c) for c in p}) for f, p in pools.items()
    }

    rows = []
    for q in queries:
        pool = pools[q["pool"]]
        scores = candidate_scores(method, q["query"], pool, dense, sparse_by_pool[q["pool"]])
        doms = {domain[e] for e in q["expected"]}
        rows.append(
            {
                **q,
                "scores": scores,
                "domain": doms.pop() if len(doms) == 1 else ("혼합" if doms else "-"),
            }
        )

    dev = [r for r in rows if r["split"] == "dev"]
    params = pick_params(method, dev)
    pq_all = [per_query(r, ranked_for(method, r["scores"], params)) for r in rows]
    pq_test = [p for p in pq_all if p["split"] == "test"]

    def by(key: str, only_answerable: bool = False) -> dict:
        groups: dict[str, list[dict]] = defaultdict(list)
        for p in pq_test:
            if only_answerable and not p["answerable"]:
                continue
            groups[p[key]].append(p)
        return {k: summarize(v) for k, v in sorted(groups.items())}

    result = {
        "method": method,
        "cards": len(cards),
        "k": K,
        "params_chosen_on_dev": list(params),
        "dev": summarize([p for p in pq_all if p["split"] == "dev"]),
        "test": summarize(pq_test),
        "test_by_function": by("function"),
        "test_by_type": by("type"),
        "test_by_domain": by("domain", only_answerable=True),
        "per_query": pq_test,
    }
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{method}_{size}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return result


def show(result: dict) -> None:
    print(
        f"방식: {result['method']}  카드 {result['cards']}장  "
        f"기준값(개발용으로 선택): {result['params_chosen_on_dev']}"
    )

    def line(label: str, m: dict) -> None:
        def fmt(v: float | None) -> str:
            return "  -  " if v is None else f"{v * 100:5.1f}%"

        print(
            f"  {label:<14} n={m['n']:<3} Recall@1 {fmt(m['recall_at_1'])}"
            f"  Recall@5 {fmt(m['recall_at_5'])}  MRR {fmt(m['mrr'])}"
            f"  인용정확도 {fmt(m['citation_precision'])}"
            f"  관련없음 {fmt(m['no_answer_accuracy'])}"
        )

    print("[개발용 (기준값을 정한 데이터, 참고용)]")
    line("dev", result["dev"])
    print("[평가용 (최종 수치)]")
    line("test", result["test"])
    for title, key in (
        ("기능별", "test_by_function"),
        ("유형별", "test_by_type"),
        ("도메인별(정답 있음)", "test_by_domain"),
    ):
        print(f"[평가용 {title}]")
        for k, m in result[key].items():
            line(k, m)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "dice"
    if name not in METHODS:
        raise SystemExit(f"방식은 {METHODS} 중 하나")
    show(main(name, int(sys.argv[2]) if len(sys.argv) > 2 else 120))
