"""카드 연결(같은 주제 판단)과 자동 병합 평가 (합성, 15번). 사용: python link_eval.py

`link-rules.md`에 결과 전에 적어 둔 방법 그대로다. 개발용 토픽 1~10으로 기준값을 정하고
평가용 토픽 11~20은 마지막에 한 번만 본다(LLM 판정도 평가용에만, 한 번).
임베딩은 backend/.env 설정으로 부른다(키는 출력하지 않는다). 결과: results/link_eval.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("AGENT_TEMPLATE_MOCK", "1")
os.environ.setdefault("HR_MOCK", "1")

from run_eval import load_env  # noqa: E402

from ax_agent_template.llm import LLMClient, LLMSettings  # noqa: E402
from ax_contracts.schemas import AgentRequest  # noqa: E402
from hr_agent.cards.link import _decision_text, topic_scores  # noqa: E402
from hr_agent.cards.service import record_case  # noqa: E402
from hr_agent.config import HrSettings  # noqa: E402
from hr_agent.retrieval.cards import build_dense_search  # noqa: E402
from hr_agent.retrieval.duplicates import SameDecisionJudge, TitleSuggester  # noqa: E402
from hr_agent.services import build_services  # noqa: E402
from hr_agent.util import similarity  # noqa: E402

DEV_MAX_TOPIC = 10
CALL_LIMIT = 60
EMBED_LIMIT = 400
POSITIVE = ("duplicate", "update")  # 같은 주제로 보는 쌍
LABELS = ("duplicate", "update", "related", "unrelated")
EXPECTED = {
    "duplicate": "same",
    "update": "changed",
    "related": "separate",
    "unrelated": "separate",
}


def req(skill: str, inp: dict, ctx: str, key: str) -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": ctx,
                "token": "mock:orchestrator",
                "deadline": datetime.now(UTC) + timedelta(seconds=120),
                "idempotency_key": f"{ctx}:{skill}:{key}",
            },
            "skill": skill,
            "input": inp,
        }
    )


class Counting:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.texts = 0

    async def embed(self, texts, *args, **kwargs):
        self.texts += len(texts)
        if self.texts > EMBED_LIMIT:
            raise RuntimeError(f"임베딩 상한 {EMBED_LIMIT}건을 넘는다")
        return await self.inner.embed(texts, *args, **kwargs)


def score(rows: list[dict], predict) -> dict:
    tp = sum(1 for r in rows if predict(r) and r["same_topic"])
    fp = sum(1 for r in rows if predict(r) and not r["same_topic"])
    fn = sum(1 for r in rows if not predict(r) and r["same_topic"])
    n = len(rows)
    tn = n - tp - fp - fn
    return {
        "n": n,
        "accuracy": round((tp + tn) / n, 4) if n else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def best_thresholds(rows: list[dict], key: str) -> list[float]:
    """개발용에서 정확도가 가장 큰 하한들(점수 >= 하한이면 같은 주제)."""
    cands = sorted({r[key] for r in rows})
    scored = [(score(rows, lambda r, t=t: r[key] >= t)["accuracy"], t) for t in cands]
    top = max(a for a, _ in scored)
    return [t for a, t in scored if a == top]


async def make_cards(svc, topics: list[dict]) -> dict:
    cards = {}
    for t in topics:
        ctx = f"ctx_link_{t['id']:02d}"
        body = {
            "context_id": ctx,
            "kind": "decision",
            "summary": t["orig"],
            "decision_reason": "합성 평가용 결정",
            "alternatives_considered": ["그대로 유지"],
            "source_refs": [f"eval:{ctx}"],
            "author": "eval",
            "area": t["area"],
        }
        out = (await record_case(svc, req("record_case", body, ctx, "rc"))).output
        card = await svc.memory.get_card(out["card_id"])
        if card.extra.get("candidate"):
            await svc.memory.set_candidate(card.card_id, False)
            card = await svc.memory.get_card(card.card_id)
        cards[t["id"]] = card
    return cards


def make_rows(topics: list[dict], cards: dict) -> list[dict]:
    rows = []
    for t in topics:
        for label in ("duplicate", "update", "related"):
            rows.append({"topic": t["id"], "label": label, "new": t[label], "old": cards[t["id"]]})
    by_area: dict[str, list[dict]] = {}
    for t in topics:
        by_area.setdefault(t["area"], []).append(t)
    for group in by_area.values():
        for a, b in combinations(group, 2):
            if (a["id"] <= DEV_MAX_TOPIC) == (b["id"] <= DEV_MAX_TOPIC):
                rows.append(
                    {
                        "topic": a["id"],
                        "label": "unrelated",
                        "new": a["orig"],
                        "old": cards[b["id"]],
                    }
                )
    return rows


async def main() -> None:
    env = load_env()
    topics = json.loads((HERE / "link_pairs.json").read_text(encoding="utf-8"))["topics"]
    dense = build_dense_search(str(Path(tempfile.mkdtemp()) / "hr.db"), env)
    assert dense is not None, "임베딩 설정이 없다"
    counter = Counting(dense._index._embedder)
    dense._index._embedder = counter
    svc = build_services(HrSettings(mock=True, dense_min_cosine=0.0), dense=dense)

    cards = await make_cards(svc, topics)
    rows = make_rows(topics, cards)
    for r in rows:
        scores, _ = await topic_scores(r["new"], [r["old"]], dense)
        r["dense"] = scores[r["old"].card_id]
        r["dice"] = similarity(_decision_text(r["old"]), r["new"])
        r["same_topic"] = r["label"] in POSITIVE
        r["split"] = "dev" if r["topic"] <= DEV_MAX_TOPIC else "test"
    dev = [r for r in rows if r["split"] == "dev"]
    test = [r for r in rows if r["split"] == "test"]

    # 기준값은 개발용에서만 정한다
    dense_best = best_thresholds(dev, "dense")
    t_low = dense_best[len(dense_best) // 2]
    dice_best = best_thresholds(dev, "dice")
    dice_tuned = dice_best[len(dice_best) // 2]
    t_auto = round(max(r["dense"] for r in dev if r["label"] != "duplicate") + 0.005, 4)
    auto_open = any(r["label"] == "duplicate" and r["dense"] >= t_auto for r in dev)

    def dice_now(r):  # 지금 규칙: 유사도 0.5 이상이고 문장이 다르면 같은 주제
        return r["dice"] >= 0.5 and r["new"] != _decision_text(r["old"])

    result: dict = {
        "pairs": {
            "dev": len(dev),
            "test": len(test),
            "labels": {k: sum(1 for r in rows if r["label"] == k) for k in LABELS},
        },
        "thresholds": {
            "dense_T_low": round(t_low, 4),
            "dense_best_candidates": len(dense_best),
            "dice_current": 0.5,
            "dice_tuned_on_dev": round(dice_tuned, 4),
            "T_auto": t_auto,
            "auto_open": auto_open,
        },
        "dev": {
            "dice_current": score(dev, dice_now),
            "dice_tuned": score(dev, lambda r: r["dice"] >= dice_tuned),
            "dense": score(dev, lambda r: r["dense"] >= t_low),
            "cosine_by_label": {
                lab: [round(r["dense"], 3) for r in dev if r["label"] == lab] for lab in LABELS
            },
            "wrong_auto_merge_by_cosine": sum(
                1 for r in dev if r["dense"] >= t_auto and r["label"] != "duplicate"
            ),
        },
        "test1": {
            "dice_current": score(test, dice_now),
            "dice_tuned": score(test, lambda r: r["dice"] >= dice_tuned),
            "dense": score(test, lambda r: r["dense"] >= t_low),
        },
    }

    # 평가용: LLM 판정은 코사인이 T_low 이상인 쌍에만 한 번
    llm = LLMClient("hr", LLMSettings.from_env("hr", env))
    judge = SameDecisionJudge(llm)
    titles = TitleSuggester(llm)
    candidates = [r for r in test if r["dense"] >= t_low]
    for r in candidates:
        if judge.calls >= CALL_LIMIT:
            break
        r["verdict"] = await judge.judge(r["new"], _decision_text(r["old"]))
    judged = [r for r in candidates if r.get("verdict")]
    auto = [
        r for r in candidates if auto_open and r["dense"] >= t_auto and r.get("verdict") == "same"
    ]
    queue = [r for r in candidates if r not in auto]
    result["test2"] = {
        "candidates_over_T_low": len(candidates),
        "llm_calls": judge.calls,
        "llm_verdict_accuracy": round(
            sum(1 for r in judged if r["verdict"] == EXPECTED[r["label"]]) / len(judged), 4
        )
        if judged
        else None,
        "llm_confusion": {
            k: sum(1 for r in judged if f"{r['label']}->{r['verdict']}" == k)
            for k in sorted({f"{r['label']}->{r['verdict']}" for r in judged})
        },
        "auto_merged": len(auto),
        "wrong_auto_merge": sum(1 for r in auto if r["label"] != "duplicate"),
        "duplicates_in_test": sum(1 for r in test if r["label"] == "duplicate"),
        "duplicates_auto_merged": sum(1 for r in auto if r["label"] == "duplicate"),
        "human_queue": len(queue),
        "human_queue_by_label": {lab: sum(1 for r in queue if r["label"] == lab) for lab in LABELS},
    }

    # 대표 제목: 평가용에서 중복 후보가 된 묶음
    samples = []
    for r in [r for r in candidates if r["label"] == "duplicate"]:
        if judge.calls + titles.calls >= CALL_LIMIT:
            break
        title = await titles.suggest(
            [(r["old"].card_id, _decision_text(r["old"])), ("new", r["new"])]
        )
        samples.append({"old": _decision_text(r["old"]), "new": r["new"], "title": title})
    result["titles"] = {
        "groups": len(samples),
        "valid": sum(1 for s in samples if s["title"]),
        "samples": samples[:4],
    }
    result["totals"] = {
        "embedding_texts": counter.texts,
        "claude_calls": judge.calls + titles.calls,
    }
    result["test_rows"] = [
        {
            "topic": r["topic"],
            "label": r["label"],
            "dense": round(r["dense"], 3),
            "dice": round(r["dice"], 3),
            "verdict": r.get("verdict"),
        }
        for r in test
    ]
    svc.store.close()
    out_path = HERE / "results" / "link_eval.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "test_rows"}, ensure_ascii=False, indent=2
        )
    )


asyncio.run(main())
