"""이벤트 유형 섞임 평가 (합성, 14-2). 사용: python type_eval.py

`type-rules.md`에 결과 전에 적어 둔 방법 그대로다. 변형 A(14번까지의 제목)·B(읽기 쉬운 제목)·
C(B + 유형 표시)를 같은 카드 15장·질의 19개로 시험하고, S2에서는 "같은 유형 카드가 없을 때"
잘못된 추천이 나오는지 본다.
기준값은 운영 설정 그대로(코사인 0.71, LLM 검증 없음)이고 이 평가로 조정하지 않는다.
임베딩은 backend/.env 설정으로 부른다(키는 출력하지 않는다).
B와 C는 같은 카드 글이라 벡터를 같이 쓴다.
결과: results/type_eval.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("AGENT_TEMPLATE_MOCK", "1")
os.environ.setdefault("HR_MOCK", "1")

from run_eval import load_env  # noqa: E402

from ax_contracts.schemas import AgentRequest, FindSimilarCasesOutput  # noqa: E402
from ax_orchestrator.naming import event_title, event_type  # noqa: E402
from ax_orchestrator.recommend import recommend  # noqa: E402
from hr_agent.cards.service import record_case  # noqa: E402
from hr_agent.case_memory.service import find_similar_cases, record_resolution  # noqa: E402
from hr_agent.config import HrSettings  # noqa: E402
from hr_agent.retrieval.cards import build_dense_search  # noqa: E402
from hr_agent.services import build_services  # noqa: E402

SIGNALS = {  # 유형: (구성요소, 지표)
    "fail": ("payment", "error_rate_5xx"),
    "slow": ("payment", "p95_latency_ms"),
    "refund": ("finance", "repeat_refund"),
}
OLD_TITLE = {
    "fail": "payment 장애 감지 (error_rate_5xx)",
    "slow": "payment 장애 감지 (p95_latency_ms)",
    "refund": "finance 장애 감지",
}
PAST = {
    "fail": [
        ("배포 후 결제 설정 누락", "직전 버전으로 롤백", 14),
        ("결제 대행사 인증서 만료", "인증서 교체", 22),
        ("DB 연결 풀 고갈", "연결 수 상향과 재시작", 31),
        ("결제 서버 메모리 부족", "서버 증설", 18),
    ],
    "slow": [
        ("결제 대행사 응답 지연", "타임아웃 조정과 재시도", 40),
        ("결제 DB 슬로우 쿼리", "인덱스 추가", 55),
        ("외부 사기 점검 API 지연", "점검 호출 비동기로 변경", 35),
        ("트래픽 급증으로 대기열 적체", "워커 증설", 27),
    ],
    "refund": [
        ("같은 고객이 주문 직후 전부 환불", "고객 환불 보류와 이용 제한 검토", 45),
        ("쿠폰 중복 사용 후 환불", "쿠폰 중복 방지 규칙 추가", 38),
        ("판매자와 짜고 반복 환불", "정산 보류와 판매자 확인", 60),
        ("자동 환불 규칙 오작동", "규칙 수정과 잘못된 환불 회수", 30),
    ],
}
HUMAN = [  # (유형, 질의)
    ("fail", "결제가 서버 오류로 자꾸 실패해요"),
    ("fail", "결제 승인 5xx 오류율이 올랐어요"),
    ("fail", "결제 API가 500 에러를 반환합니다"),
    ("fail", "주문 결제가 계속 실패하는 장애"),
    ("slow", "결제 응답이 너무 느려요"),
    ("slow", "결제 승인 지연 p95 상승"),
    ("slow", "결제 화면이 오래 걸리고 타임아웃이 나요"),
    ("slow", "결제 API 응답 시간이 길어졌어요"),
    ("refund", "같은 고객이 환불을 반복해요"),
    ("refund", "환불 남용 의심 건이 나왔어요"),
    ("refund", "주문하고 바로 전부 환불하는 이상 거래"),
    ("refund", "재무 사후 감사에서 이상 환불이 감지됐어요"),
]
UNRELATED = [
    "회의실 예약 화면 색상",
    "신입 사원 교육 일정",
    "사무실 에어컨 온도 조절",
    "점심 메뉴 추천",
]


def title_of(variant: str, kind: str) -> str:
    return OLD_TITLE[kind] if variant == "A" else event_title(*SIGNALS[kind])


def type_of(kind: str) -> str | None:
    return event_type(*SIGNALS[kind])


def queries_of(variant: str) -> list[tuple[str | None, str, str]]:
    """(유형, 종류, 질의). 총괄이 보내는 글은 변형마다 형식이 다르다."""
    out: list[tuple[str | None, str, str]] = []
    for kind, (comp, _) in SIGNALS.items():
        text = f"{OLD_TITLE[kind]} {comp}" if variant == "A" else event_title(*SIGNALS[kind])
        out.append((kind, "총괄", text))
    out += [(k, "사람말", q) for k, q in HUMAN]
    out += [(None, "무관", q) for q in UNRELATED]
    return out


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
    """임베딩 호출 횟수(텍스트 수)를 센다."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.texts = 0

    async def embed(self, texts, *args, **kwargs):
        self.texts += len(texts)
        return await self.inner.embed(texts, *args, **kwargs)


async def build(svc, variant: str, tagged: bool) -> dict[str, str]:
    """과거 이벤트 카드를 만든다. {card_id: 유형}을 돌려준다."""
    kinds: dict[str, str] = {}
    n = 0
    for kind in SIGNALS:
        title = title_of(variant, kind)
        rows = [(*p, True) for p in PAST[kind]] + [(None, None, None, False)]  # 해결 4 + 미해결 1
        for cause, action, minutes, resolved in rows:
            n += 1
            ctx = f"ctx_inc_{n:04d}"
            steps = 10 + n % 3
            summary = (
                f"{title} — 완료 {steps}단계, 미완료 {13 - steps + 3}단계"
                if variant == "A"
                else title
            )
            record = {
                "context_id": ctx,
                "kind": "incident",
                "summary": summary,
                "decision_reason": "원인을 확정하지 못해 사람에게 넘김",
                "alternatives_considered": ["즉시 롤백"],
                "source_refs": [f"inc:{ctx}", "area:infra"],
                "author": "orchestrator",
                "event_type": type_of(kind) if tagged else None,
            }
            out = (await record_case(svc, req("record_case", record, ctx, "rc"))).output
            kinds[out["card_id"]] = kind
            if resolved:
                await record_resolution(
                    svc,
                    req(
                        "record_resolution",
                        {
                            "context_id": ctx,
                            "outcome": "resolved",
                            "root_cause": cause,
                            "action": action,
                            "minutes_to_resolve": minutes,
                        },
                        ctx,
                        "rr",
                    ),
                )
    return kinds


async def run_variant(variant: str, env: dict, vectors_dir: str) -> dict:
    tagged = variant == "C"
    dense = build_dense_search(str(Path(vectors_dir) / "hr.db"), env)
    assert dense is not None, "임베딩 설정이 없다"
    counter = Counting(dense._index._embedder)
    dense._index._embedder = counter
    svc = build_services(HrSettings(mock=True, dense_min_cosine=0.71), dense=dense)
    kinds = await build(svc, variant, tagged)

    async def ask(truth: str | None, query: str, limit: int) -> list[dict]:
        inp: dict = {"query": query, "limit": limit}
        if tagged and truth:
            inp["event_type"] = type_of(truth)
        res = await find_similar_cases(svc, req("find_similar_cases", inp, "ctx_q", query))
        out = FindSimilarCasesOutput.model_validate(res.output)
        return [
            {**c.model_dump(mode="json"), "kind": kinds.get(c.card_id), "card_id": c.card_id}
            for c in out.cases
        ]

    s1 = []
    s2 = []
    for truth, source, query in queries_of(variant):
        top = await ask(truth, query, 3)
        s1.append(
            {
                "truth": truth,
                "source": source,
                "query": query,
                "top3": [
                    (c["kind"], c["resolved"], round(c["similarity"], 3), c["same_type"])
                    for c in top
                ],
            }
        )
        if truth:  # S2: 같은 유형 카드가 없다고 보고 그 유형의 카드를 뺀다
            wide = await ask(truth, query, 10)
            rest = [c for c in wide if c["kind"] != truth][:3]
            rec = recommend({"cases": rest}) if rest else recommend({"cases": []})
            s2.append(
                {
                    "truth": truth,
                    "source": source,
                    "query": query,
                    "shown": [(c["kind"], round(c["similarity"], 3), c["same_type"]) for c in rest],
                    "recommendation": rec["status"],
                }
            )
    svc.store.close()

    typed = [r for r in s1 if r["truth"]]
    unrelated = [r for r in s1 if not r["truth"]]
    summary = {
        "S1": {
            "typed_n": len(typed),
            "same_type_top1": sum(1 for r in typed if r["top3"] and r["top3"][0][0] == r["truth"]),
            "no_result": sum(1 for r in typed if not r["top3"]),
            "other_type_in_top3": sum(
                1 for r in typed if any(t[0] != r["truth"] for t in r["top3"])
            ),
            "unrelated_n": len(unrelated),
            "unrelated_no_result": sum(1 for r in unrelated if not r["top3"]),
        },
        "S2": {
            "n": len(s2),
            "wrong_recommendation": sum(1 for r in s2 if r["recommendation"] == "found"),
            "other_type_shown": sum(1 for r in s2 if r["shown"]),
        },
        "embedding_texts_sent": counter.texts,
        "cards": len(kinds),
    }
    return {"summary": summary, "S1": s1, "S2": s2}


async def main() -> None:
    env = load_env()
    only = sys.argv[1:] or ["A", "B", "C"]
    shared = tempfile.mkdtemp()  # B와 C는 같은 카드 글이라 벡터를 같이 쓴다
    results: dict[str, dict] = {}
    for variant in only:
        folder = shared if variant in ("B", "C") else tempfile.mkdtemp()
        results[variant] = await run_variant(variant, env, folder)
        print(variant, json.dumps(results[variant]["summary"], ensure_ascii=False))
    path = HERE / "results" / "type_eval.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


asyncio.run(main())
