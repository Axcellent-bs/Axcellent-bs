"""카드 연결 평가 v2 (합성, 15번 개정). 사용: python link_eval_v2.py

`link-rules.md` 5장(개정 2)에 결과 전에 적어 둔 방법 그대로다. 시연에서 실제 카드(긴 본문)의
코사인이 평가보다 낮게 나온 문제를 고친 뒤, 카드마다 다른 교훈 문단을 붙여 실제 조건으로 시험한다.
개발용 토픽 1~20으로 기준값을 정하고, 새로 쓴 평가용 토픽 21~30은 마지막에 한 번만 본다.
v1(카드 전체 글)과 v2(결정 문장만)를 같은 카드로 같이 잰다. 결과: results/link_eval_v2.json
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

DEV_MAX_TOPIC = 20
CALL_LIMIT = 60
EMBED_LIMIT = 400
POSITIVE = ("duplicate", "update")
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
    cands = sorted({r[key] for r in rows})
    scored = [(score(rows, lambda r, t=t: r[key] >= t)["accuracy"], t) for t in cands]
    top = max(a for a, _ in scored)
    return [t for a, t in scored if a == top]


def lesson(text: str) -> str:
    """실제 카드처럼 카드마다 다른 교훈 문단(결정 문장을 포함)을 본문에 붙인다."""
    return (
        f"\n교훈: 교훈: {text}는 시연용으로 주입된 합성 결정이라 실제 결정이 아니다. "
        "재발 방지: 합성 결정은 실제 결정과 구분해 표시하고 확인 후 반영한다."
    )


async def make_cards(svc, topics: list[dict]) -> dict:
    cards = {}
    for t in topics:
        ctx = f"ctx_link2_{t['id']:02d}"
        body = {
            "context_id": ctx,
            "kind": "decision",
            "summary": t["orig"],
            "decision_reason": "시연 주입: 평가용 합성 결정(실제 결정 아님)",
            "alternatives_considered": ["그대로 유지"],
            "source_refs": [f"seed:{ctx}"],
            "author": "시연 주입",
            "area": t["area"],
        }
        out = (await record_case(svc, req("record_case", body, ctx, "rc"))).output
        card = await svc.memory.get_card(out["card_id"])
        if card.extra.get("candidate"):
            await svc.memory.set_candidate(card.card_id, False)
            card = await svc.memory.get_card(card.card_id)
        card = card.model_copy(update={"body": card.body + lesson(t["orig"])})
        await svc.memory.write_card(card)
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
        v1 = await dense.search(r["new"], [r["old"]], k=1, min_cosine=-1.0)  # 카드 전체 글
        r["v1"] = v1[0][1]
        scores, _ = await topic_scores(r["new"], [r["old"]], dense)  # v2: 결정 문장만
        r["v2"] = scores[r["old"].card_id]
        r["dice"] = similarity(_decision_text(r["old"]), r["new"])
        r["same_topic"] = r["label"] in POSITIVE
        r["split"] = "dev" if r["topic"] <= DEV_MAX_TOPIC else "test"
    dev = [r for r in rows if r["split"] == "dev"]
    test = [r for r in rows if r["split"] == "test"]

    best2 = best_thresholds(dev, "v2")
    t_low = best2[len(best2) // 2]
    best1 = best_thresholds(dev, "v1")
    t_low_v1 = best1[len(best1) // 2]
    t_auto = round(max(r["v2"] for r in dev if r["label"] != "duplicate") + 0.005, 4)
    t_auto_v1 = round(max(r["v1"] for r in dev if r["label"] != "duplicate") + 0.005, 4)

    def dice_now(r):
        return r["dice"] >= 0.5 and r["new"] != _decision_text(r["old"])

    def span(label: str, split: list[dict], key: str):
        xs = [r[key] for r in split if r["label"] == label]
        return [round(min(xs), 3), round(max(xs), 3)] if xs else None

    result: dict = {
        "pairs": {
            "dev": len(dev),
            "test": len(test),
            "labels": {k: sum(1 for r in rows if r["label"] == k) for k in LABELS},
        },
        "thresholds": {
            "v2_T_low": round(t_low, 4),
            "v2_T_auto": t_auto,
            "v2_best_candidates": len(best2),
            "v1_T_low": round(t_low_v1, 4),
            "v1_T_auto": t_auto_v1,
        },
        "dev": {
            "dice_current": score(dev, dice_now),
            "v1": score(dev, lambda r: r["v1"] >= t_low_v1),
            "v2": score(dev, lambda r: r["v2"] >= t_low),
            "v2_wrong_auto_merge_by_cosine": sum(
                1 for r in dev if r["v2"] >= t_auto and r["label"] != "duplicate"
            ),
            "v2_duplicates_over_auto": sum(
                1 for r in dev if r["v2"] >= t_auto and r["label"] == "duplicate"
            ),
            "v2_cosine_span": {lab: span(lab, dev, "v2") for lab in LABELS},
            "v1_cosine_span": {lab: span(lab, dev, "v1") for lab in LABELS},
        },
        "test1": {
            "dice_current": score(test, dice_now),
            "v1": score(test, lambda r: r["v1"] >= t_low_v1),
            "v2": score(test, lambda r: r["v2"] >= t_low),
            "v2_cosine_span": {lab: span(lab, test, "v2") for lab in LABELS},
        },
    }

    llm = LLMClient("hr", LLMSettings.from_env("hr", env))
    judge, titles = SameDecisionJudge(llm), TitleSuggester(llm)
    candidates = [r for r in test if r["v2"] >= t_low]
    for r in candidates:
        if judge.calls >= CALL_LIMIT:
            break
        r["verdict"] = await judge.judge(r["new"], _decision_text(r["old"]))
    judged = [r for r in candidates if r.get("verdict")]
    auto = [r for r in candidates if r["v2"] >= t_auto and r.get("verdict") == "same"]
    queue = [r for r in candidates if r not in auto and r["label"] == "duplicate"]
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
        "duplicates_to_human": len(queue),
        "updates_over_auto_by_cosine": sum(
            1 for r in test if r["label"] == "update" and r["v2"] >= t_auto
        ),
    }
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
        "samples": samples[:3],
    }
    result["totals"] = {
        "embedding_texts": counter.texts,
        "claude_calls": judge.calls + titles.calls,
    }
    result["test_rows"] = [
        {
            "topic": r["topic"],
            "label": r["label"],
            "v1": round(r["v1"], 3),
            "v2": round(r["v2"], 3),
            "dice": round(r["dice"], 3),
            "verdict": r.get("verdict"),
        }
        for r in test
    ]
    svc.store.close()
    (HERE / "results" / "link_eval_v2.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "test_rows"}, ensure_ascii=False, indent=2
        )
    )


asyncio.run(main())
