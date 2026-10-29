"""반복 장애 적중 평가 (합성). 사용: python repeat_eval.py

장애 유형 3종(결제 실패·결제 지연·환불 남용)의 과거 해결 사건을 인사 AI에 쌓고, 새 사건이 났을 때
`find_similar_cases`가 같은 유형의 과거 사건을 찾는지, 다른 유형이 섞이는지,
상관없는 질의에는 비는지를 본다.

- 과거 사건 카드는 `record_case`와 `record_resolution`으로 만든다(총괄이 남기는 글 모양: 제목은
  "<부품> 장애 감지 (<지표>)", 요약은 단계 수). 질의는 두 종류다.
  1) 총괄이 실제로 보내는 글(`<제목> <부품>`)  2) 사람이 쓴 말투의 설명
- 정답 유형은 질의마다 사람이 정했다(작성자 라벨, 사용자 확인 전). 합성이라 운영 성능이 아니다.
- 기준은 운영 설정 그대로(코사인 0.71, LLM 검증 없음)이고 이 평가로 값을 조정하지 않는다.
- 임베딩은 backend/.env 설정으로 부르고(키는 출력하지 않는다) 카드 15장 + 질의 19개 정도만 쓴다.
- 결과: results/repeat_eval.json
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
from hr_agent.cards.service import record_case  # noqa: E402
from hr_agent.case_memory.service import find_similar_cases, record_resolution  # noqa: E402
from hr_agent.config import HrSettings  # noqa: E402
from hr_agent.retrieval.cards import build_dense_search  # noqa: E402
from hr_agent.services import build_services  # noqa: E402

PAY, SLOW, REFUND = (
    "payment 장애 감지 (error_rate_5xx)",
    "payment 장애 감지 (p95_latency_ms)",
    "finance 장애 감지",
)
TYPES = {
    "fail": {
        "title": PAY,
        "past": [
            ("배포 후 결제 설정 누락", "직전 버전으로 롤백", 14),
            ("결제 대행사 인증서 만료", "인증서 교체", 22),
            ("DB 연결 풀 고갈", "연결 수 상향과 재시작", 31),
            ("결제 서버 메모리 부족", "서버 증설", 18),
        ],
    },
    "slow": {
        "title": SLOW,
        "past": [
            ("결제 대행사 응답 지연", "타임아웃 조정과 재시도", 40),
            ("결제 DB 슬로우 쿼리", "인덱스 추가", 55),
            ("외부 사기 점검 API 지연", "점검 호출 비동기로 변경", 35),
            ("트래픽 급증으로 대기열 적체", "워커 증설", 27),
        ],
    },
    "refund": {
        "title": REFUND,
        "past": [
            ("같은 고객이 주문 직후 전부 환불", "고객 환불 보류와 이용 제한 검토", 45),
            ("쿠폰 중복 사용 후 환불", "쿠폰 중복 방지 규칙 추가", 38),
            ("판매자와 짜고 반복 환불", "정산 보류와 판매자 확인", 60),
            ("자동 환불 규칙 오작동", "규칙 수정과 잘못된 환불 회수", 30),
        ],
    },
}
UNRESOLVED = {
    "fail": PAY,
    "slow": SLOW,
    "refund": REFUND,
}  # 유형마다 미해결 사건 1건(근거로 안 쓰임)

QUERIES = [  # (유형, 종류, 질의)
    ("fail", "총괄", f"{PAY} payment"),
    ("slow", "총괄", f"{SLOW} payment"),
    ("refund", "총괄", f"{REFUND} finance"),
    ("fail", "사람말", "결제가 서버 오류로 자꾸 실패해요"),
    ("fail", "사람말", "결제 승인 5xx 오류율이 올랐어요"),
    ("fail", "사람말", "결제 API가 500 에러를 반환합니다"),
    ("fail", "사람말", "주문 결제가 계속 실패하는 장애"),
    ("slow", "사람말", "결제 응답이 너무 느려요"),
    ("slow", "사람말", "결제 승인 지연 p95 상승"),
    ("slow", "사람말", "결제 화면이 오래 걸리고 타임아웃이 나요"),
    ("slow", "사람말", "결제 API 응답 시간이 길어졌어요"),
    ("refund", "사람말", "같은 고객이 환불을 반복해요"),
    ("refund", "사람말", "환불 남용 의심 건이 나왔어요"),
    ("refund", "사람말", "주문하고 바로 전부 환불하는 이상 거래"),
    ("refund", "사람말", "재무 사후 감사에서 이상 환불이 감지됐어요"),
    (None, "무관", "회의실 예약 화면 색상"),
    (None, "무관", "신입 사원 교육 일정"),
    (None, "무관", "사무실 에어컨 온도 조절"),
    (None, "무관", "점심 메뉴 추천"),
]


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


async def build(svc) -> dict[str, str]:
    """과거 사건 카드를 만든다. {card_id: 유형}을 돌려준다."""
    kinds: dict[str, str] = {}
    n = 0
    for kind, spec in TYPES.items():
        for cause, action, minutes in spec["past"]:
            n += 1
            ctx = f"ctx_inc_{n:04d}"
            steps = 10 + n % 3
            record = {
                "context_id": ctx,
                "kind": "incident",
                "summary": f"{spec['title']} — 완료 {steps}단계, 미완료 {13 - steps + 3}단계",
                "decision_reason": "원인을 확정하지 못해 사람에게 넘김",
                "alternatives_considered": ["즉시 롤백"],
                "source_refs": [f"inc:{ctx}", "area:infra"],
                "author": "orchestrator",
            }
            out = (await record_case(svc, req("record_case", record, ctx, "rc"))).output
            kinds[out["card_id"]] = kind
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
    for kind, title in UNRESOLVED.items():
        n += 1
        ctx = f"ctx_inc_{n:04d}"
        record = {
            "context_id": ctx,
            "kind": "incident",
            "summary": f"{title} — 완료 10단계, 미완료 3단계",
            "decision_reason": "원인을 확정하지 못해 사람에게 넘김",
            "alternatives_considered": ["즉시 롤백"],
            "source_refs": [f"inc:{ctx}", "area:infra"],
            "author": "orchestrator",
        }
        out = (await record_case(svc, req("record_case", record, ctx, "rc"))).output
        kinds[out["card_id"]] = kind
    return kinds


async def main() -> None:
    env = load_env()
    tmp = tempfile.mkdtemp()
    dense = build_dense_search(str(Path(tmp) / "hr.db"), env)
    assert dense is not None, "임베딩 설정이 없다"
    svc = build_services(HrSettings(mock=True, dense_min_cosine=0.71), dense=dense)
    kinds = await build(svc)

    rows = []
    for truth, source, query in QUERIES:
        res = await find_similar_cases(
            svc, req("find_similar_cases", {"query": query, "limit": 3}, "ctx_q", query)
        )
        out = FindSimilarCasesOutput.model_validate(res.output)
        found = [(kinds.get(c.card_id), c.resolved, round(c.similarity, 3)) for c in out.cases]
        rows.append(
            {"truth": truth, "source": source, "query": query, "method": out.method, "top3": found}
        )
    svc.store.close()

    def stats(sub: list[dict]) -> dict:
        typed = [r for r in sub if r["truth"]]
        top1 = sum(1 for r in typed if r["top3"] and r["top3"][0][0] == r["truth"])
        mixed = sum(1 for r in typed if any(k != r["truth"] for k, _, _ in r["top3"]))
        empty = sum(1 for r in typed if not r["top3"])
        return {
            "n": len(typed),
            "same_type_top1": top1,
            "any_other_type_in_top3": mixed,
            "no_result": empty,
        }

    summary = {
        "all_typed": stats(rows),
        "총괄 형식": stats([r for r in rows if r["source"] == "총괄"]),
        "사람말": stats([r for r in rows if r["source"] == "사람말"]),
        "무관 질의 n": sum(1 for r in rows if r["source"] == "무관"),
        "무관 질의 중 결과 없음": sum(1 for r in rows if r["source"] == "무관" and not r["top3"]),
        "무관 질의 중 결과가 나온 것": [
            r["query"] for r in rows if r["source"] == "무관" and r["top3"]
        ],
        "cards": len(kinds),
        "queries": len(rows),
    }
    out_path = HERE / "results" / "repeat_eval.json"
    out_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in rows:
        print(r["truth"], r["source"], r["query"][:34], "->", r["top3"])


asyncio.run(main())
