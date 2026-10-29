"""데이터 분석 에이전트 정확도 평가: uv run python domains/data/eval/run.py

- 정답지 `cases/*.json`의 시나리오마다 쇼핑몰 데이터를 실제 shop-api 코드로 다시 만들고
  (`eval_world`), data의 실제 skill 코드로 돌려 정답과 비교한다.
  - 이상 탐지: `detect_anomalies` (shop-api 지표 시계열을 data의 `HttpMetricSource`로 읽는다)
  - 영향 분석: `analyze_impact` (실패 주문을 data의 `HttpShopClient`로 읽는다)
  - 사기 점수: `fraud_score` 핸들러. 아직 미구현이면 "확인할 수 없음"으로 두고, 참고로 규칙 점수
    (`metrics/fraud.score_seller`)를 라벨과 비교한다.
  - 원인 추정: `estimate_root_cause` (다른 부서 응답은 주입한 원인으로 만든 가짜 A2A)
- LLM은 평가용 클라이언트(`eval_llm.ScriptedLLM`)라 CI에서도 결과가 같다.
- 결과: 표를 출력하고 `results/latest.json`에 저장한다(커밋하지 않음).
- `--write-baseline`: 지금 값을 기준(`baseline.json`, 커밋)으로 저장한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eval_llm  # noqa: E402
import eval_world  # noqa: E402
import httpx  # noqa: E402

from ax_contracts.schemas import (  # noqa: E402
    TENANT_ID_DEFAULT,
    AgentRequest,
    AgentResult,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.data import (  # noqa: E402
    AnomalyDetectionInput,
    ImpactAnalysisInput,
    RootCauseInput,
)
from data_agent import handlers  # noqa: E402
from data_agent.clients.shop_client import HttpMetricSource, HttpShopClient, ShopApi  # noqa: E402
from data_agent.metrics.fraud import SellerFacts, score_seller  # noqa: E402
from data_agent.skills.anomaly_detection import detect_anomalies  # noqa: E402
from data_agent.skills.impact_analysis import analyze_impact  # noqa: E402
from data_agent.skills.root_cause import estimate_root_cause  # noqa: E402

CASES = HERE / "cases"
RESULTS = HERE / "results"
BASELINE = HERE / "baseline.json"
POLL_WINDOW = timedelta(minutes=15)  # 주기 감시가 묻는 구간 길이
POLL_STEP = timedelta(minutes=5)  # 주기 감시 간격
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# 지표 이름 → (설명, 좋아지는 방향). 방향이 None이면 기준 비교에서 빼는 참고값이다
METRICS: dict[str, tuple[str, str | None]] = {
    "anomaly.detection_rate": (
        "이상 탐지 탐지율 (장애 시나리오 중 맞는 지표로 잡은 비율)",
        "higher",
    ),
    "anomaly.false_positive_rate": (
        "이상 탐지 오탐률 (장애 없는 시나리오 중 이상이라고 한 비율)",
        "lower",
    ),
    "anomaly.mean_delay_minutes": ("탐지 지연 평균 (분, 5분 간격 감시 기준)", "lower"),
    "anomaly.max_delay_minutes": ("탐지 지연 최대 (분)", "lower"),
    "anomaly.unknown_handled_rate": ("빈 구간을 '확인할 수 없음'으로 답한 비율", "higher"),
    "impact.match_rate": ("영향 분석 일치율 (건수·고객·금액 모두 정답과 같음)", "higher"),
    "impact.mean_abs_amount_error": ("영향 분석 금액 오차 평균 (원)", "lower"),
    "impact.mean_abs_amount_error_pct": (
        "영향 분석 금액 오차 평균 (%, 정답 0원 케이스 제외)",
        "lower",
    ),
    "fraud.skill_available": ("사기 점수 skill이 답하는지 (1=예, 0=미구현)", "higher"),
    "fraud.precision": ("사기 점수 skill 등급 정밀도 (high=사기)", "higher"),
    "fraud.recall": ("사기 점수 skill 등급 재현율", "higher"),
    "fraud.rule_precision": ("참고: 규칙 점수(metrics/fraud) 정밀도", "higher"),
    "fraud.rule_recall": ("참고: 규칙 점수 재현율", "higher"),
    "llm.fabricated_number_rate": ("LLM 설명에 지어낸 숫자가 남은 비율 (최종 문장 기준)", "lower"),
    "llm.fallback_rate": ("설명이 템플릿으로 대체된 비율", None),
    "root_cause.top1_rate": ("원인 추정: 주입한 원인이 1순위 후보인 비율", "higher"),
    "root_cause.ungrounded_survived_rate": (
        "원인 추정: 일부러 넣은 근거 없는 후보가 남은 비율",
        "lower",
    ),
    "root_cause.needs_confirmation_rate": ("원인 추정: '확인 필요'로 끝난 비율", None),
}


def load_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scenarios = []
    for path in sorted(CASES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "sellers.json":
            sellers = data["sellers"]
        else:
            scenarios.append(data)
    return scenarios, sellers


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text)}


def _fabricated(summary: str, allowed: set[float]) -> bool:
    """최종 설명 문장에 결과 숫자에 없는 숫자가 있는지 (평가 쪽에서 따로 확인한다)."""
    return any(all(abs(n - a) > 1e-6 for a in allowed) for n in _numbers(summary))


def _meta(context_id: str) -> RequestMeta:
    return RequestMeta(
        context_id=context_id,
        token="mock:orchestrator",
        path=["orchestrator"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _sources(case: dict[str, Any]):  # noqa: ANN202
    """주입한 원인으로 다른 부서의 A2A 응답을 만든다 (배포 → planning, 공지·캠페인 → marketing)."""
    cause = case["cause"]
    start = eval_world.parse(case["injection"].get("from") or case["window"]["from"])
    prs, notices = [], []
    if cause["type"] == "deploy":
        if "older_pr_number" in cause:  # planning은 병합 순서(오래된 것 먼저)로 준다
            older = start - timedelta(minutes=cause["older_deploy_minutes_before"])
            prs.append(
                {"pr_number": cause["older_pr_number"], "title": "상품 목록 문구 수정",
                 "merged_at": eval_world.iso(older)}
            )  # fmt: skip
        merged = start - timedelta(minutes=cause["minutes_before"])
        prs.append(
            {"pr_number": cause["pr_number"], "title": "결제 설정 변경",
             "merged_at": eval_world.iso(merged)}
        )  # fmt: skip
    if cause["type"] in ("notice", "campaign"):
        published = start - timedelta(minutes=cause["minutes_before"])
        notices.append(
            {"found": True, "notice_id": cause["notice_id"], "channel_code": "banner",
             "context_id": f"ctx_mkt_{cause['type']}", "published_at": eval_world.iso(published)}
        )  # fmt: skip
    outputs = {
        "find_related_prs": {"candidates": prs},
        "list_active_notices": {"items": notices, "total": len(notices)},
        "detect_inquiry_surge": {"evaluated_at": case["window"]["to"], "surges": []},
        "card_search": {"hits": []},
    }

    async def send(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=outputs[req.skill])

    return send


async def _delay(case: dict[str, Any], source: HttpMetricSource) -> float | None:
    """5분마다 최근 15분을 묻는 감시로 처음 잡힌 시각 − 장애를 넣은 시각 (분). 못 잡으면 None."""
    expected = case["expected"]["anomaly"]
    onset = eval_world.parse(expected["onset"])
    end = eval_world.parse(case["window"]["to"])
    moment = onset + POLL_STEP
    llm = eval_llm.ScriptedLLM("fail")  # 감시는 LLM을 쓰지 않는다
    while moment <= end:
        data = AnomalyDetectionInput(
            window_from=moment - POLL_WINDOW, window_to=moment, metrics=[expected["metric"]]
        )
        result = await detect_anomalies(data, source, llm)
        if any(v.is_anomaly for v in result.output.verdicts):
            return (moment - onset).total_seconds() / 60
        moment += POLL_STEP
    return None


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    world = eval_world.build(case)
    truth = eval_world.truth(world)
    expected = case["expected"]
    drift = (
        truth.fault_failed_orders != expected["impact"]["failed_order_count"]
        or truth.fault_amount != expected["impact"]["damage_amount"]
    )
    window_from, window_to = world.window
    async with httpx.AsyncClient(transport=world.transport(), base_url="http://shop") as client:
        api = ShopApi("http://shop", token="eval", client=client)
        source, shop = HttpMetricSource(api), HttpShopClient(api)
        llm = eval_llm.ScriptedLLM(case["llm"])
        out: dict[str, Any] = {"id": case["id"], "kind": case["kind"], "answer_drift": drift}

        anomaly = await detect_anomalies(
            AnomalyDetectionInput(window_from=window_from, window_to=window_to), source, llm
        )
        flagged = [v.metric for v in anomaly.output.verdicts if v.is_anomaly]
        allowed = {
            float(x)
            for v in anomaly.output.verdicts
            for x in (v.observed, v.baseline, v.ratio)
            if x is not None
        }
        metric = expected["anomaly"]["metric"]
        out["anomaly"] = {
            "expected": expected["anomaly"]["expected"],
            "metric": metric,
            "flagged": flagged,
            "detected": metric in flagged if metric else None,
            "any_anomaly": anomaly.output.any_anomaly,
            "summary": anomaly.output.summary,
            "said_unknown": "확인할 수 없음" in anomaly.output.summary,
            "llm_used": anomaly.output.any_anomaly,
            "fallback": anomaly.fallback,
            "fabricated": _fabricated(anomaly.output.summary, allowed)
            if anomaly.output.any_anomaly
            else False,
            "delay_minutes": await _delay(case, source) if metric else None,
        }

        if case["kind"] in ("payment_fault", "empty"):
            impact = await analyze_impact(
                ImpactAnalysisInput(window_from=window_from, window_to=window_to, scope="payment"),
                TENANT_ID_DEFAULT,
                llm,
                shop=shop,
            )
            got = impact.output
            want = expected["impact"]
            numbers = {
                float(got.failed_order_count),
                float(got.affected_customer_count),
                float(got.damage_amount),
            }
            out["impact"] = {
                "want": {
                    k: want[k]
                    for k in ("failed_order_count", "affected_customer_count", "damage_amount")
                },
                "got": {
                    "failed_order_count": got.failed_order_count,
                    "affected_customer_count": got.affected_customer_count,
                    "damage_amount": got.damage_amount,
                },
                "declined_in_window": want["declined_payments"],
                "fallback": impact.fallback,
                "fabricated": _fabricated(got.summary, numbers),
            }

        if case["kind"] in ("payment_fault", "order_drop", "sale"):
            injection = case["injection"]
            rc_llm = eval_llm.ScriptedLLM(case["llm"])
            data = RootCauseInput(
                context_id=f"ctx_inc_eval_{case['id']}",
                window_from=eval_world.parse(injection["from"]),
                window_to=eval_world.parse(injection["to"]),
                metric=metric or "order_count",
            )
            rc = await estimate_root_cause(
                data, _meta(data.context_id), rc_llm, send=_sources(case), now=window_to
            )
            facts = {f.fact_id: f for f in rc.output.facts}
            first = rc.output.hypotheses[0] if rc.output.hypotheses else None
            first_refs = [facts[i].ref for i in (first.fact_ids if first else []) if i in facts]
            survived = sum(
                1
                for h in rc.output.hypotheses
                if "f99" in h.fact_ids or h.explanation == eval_llm.MADE_UP_CAUSE_NUMBER
            )
            out["root_cause"] = {
                "injected": expected["root_cause"]["injected"],
                "want_ref": expected["root_cause"]["ref"],
                "first_refs": first_refs,
                "first_explanation": first.explanation if first else None,
                "top1": expected["root_cause"]["ref"] in first_refs
                if expected["root_cause"]["ref"]
                else None,
                "candidates": len(rc.output.hypotheses),
                "planted_ungrounded": rc_llm.planted_ungrounded,
                "ungrounded_survived": survived,
                "needs_confirmation": rc.output.needs_confirmation,
                "fallback": rc.fallback,
            }
    return out


async def run_sellers(sellers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for seller in sellers:
        request = AgentRequest(
            meta=_meta("ctx_eval_fraud"), skill="fraud_score", input={"seller_id": seller["id"]}
        )
        result = await handlers.HANDLERS["fraud_score"](request)
        level = result.output.get("level") if result.status == TaskStatus.COMPLETED else None
        rule = score_seller(SellerFacts(**seller["facts"]))
        rows.append(
            {
                "id": seller["id"],
                "label": seller["label"],
                "skill_status": result.status.value,
                "skill_outcome": result.outcome,
                "skill_level": level,
                "rule_score": rule.score,
                "rule_level": rule.level,
            }
        )
    return rows


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def _precision_recall(rows: list[dict[str, Any]], key: str) -> tuple[float | None, float | None]:
    predicted = [r for r in rows if r[key] == "high"]
    actual = [r for r in rows if r["label"] == "fraud"]
    true_pos = [r for r in predicted if r["label"] == "fraud"]
    if not any(r[key] is not None for r in rows):
        return None, None
    return _rate(len(true_pos), len(predicted)), _rate(len(true_pos), len(actual))


def summarize(cases: list[dict[str, Any]], sellers: list[dict[str, Any]]) -> dict[str, Any]:
    faults = [c for c in cases if c["anomaly"]["expected"]]
    quiet = [c for c in cases if not c["anomaly"]["expected"]]
    empty = [c for c in cases if c["kind"] == "empty"]
    delays = [
        c["anomaly"]["delay_minutes"] for c in faults if c["anomaly"]["delay_minutes"] is not None
    ]
    impacts = [c["impact"] for c in cases if "impact" in c]
    errors = [abs(i["got"]["damage_amount"] - i["want"]["damage_amount"]) for i in impacts]
    pct = [
        abs(i["got"]["damage_amount"] - i["want"]["damage_amount"])
        / i["want"]["damage_amount"]
        * 100
        for i in impacts
        if i["want"]["damage_amount"]
    ]
    explanations = [c["anomaly"] for c in cases if c["anomaly"]["llm_used"]] + impacts
    root = [c["root_cause"] for c in cases if "root_cause" in c]
    with_cause = [r for r in root if r["top1"] is not None]
    planted = sum(r["planted_ungrounded"] for r in root)
    skill_p, skill_r = _precision_recall(sellers, "skill_level")
    rule_p, rule_r = _precision_recall(sellers, "rule_level")
    return {
        "anomaly.detection_rate": _rate(
            sum(1 for c in faults if c["anomaly"]["detected"]), len(faults)
        ),
        "anomaly.false_positive_rate": _rate(
            sum(1 for c in quiet if c["anomaly"]["any_anomaly"]), len(quiet)
        ),
        "anomaly.mean_delay_minutes": round(sum(delays) / len(delays), 2) if delays else None,
        "anomaly.max_delay_minutes": max(delays) if delays else None,
        "anomaly.unknown_handled_rate": _rate(
            sum(1 for c in empty if c["anomaly"]["said_unknown"]), len(empty)
        ),
        "impact.match_rate": _rate(sum(1 for i in impacts if i["got"] == i["want"]), len(impacts)),
        "impact.mean_abs_amount_error": round(sum(errors) / len(errors), 1) if errors else None,
        "impact.mean_abs_amount_error_pct": round(sum(pct) / len(pct), 2) if pct else None,
        "fraud.skill_available": 1.0
        if any(s["skill_status"] == "completed" for s in sellers)
        else 0.0,
        "fraud.precision": skill_p,
        "fraud.recall": skill_r,
        "fraud.rule_precision": rule_p,
        "fraud.rule_recall": rule_r,
        "llm.fabricated_number_rate": _rate(
            sum(1 for e in explanations if e["fabricated"]), len(explanations)
        ),
        "llm.fallback_rate": _rate(
            sum(1 for e in explanations if e["fallback"]), len(explanations)
        ),
        "root_cause.top1_rate": _rate(sum(1 for r in with_cause if r["top1"]), len(with_cause)),
        "root_cause.ungrounded_survived_rate": _rate(
            sum(r["ungrounded_survived"] for r in root), planted
        ),
        "root_cause.needs_confirmation_rate": _rate(
            sum(1 for r in root if r["needs_confirmation"]), len(root)
        ),
    }


def wrong_cases(cases: list[dict[str, Any]], sellers: list[dict[str, Any]]) -> list[dict[str, str]]:
    """틀린 케이스와 무엇이 틀렸는지. 원인 추정은 사람이 PR에 적는다(측정만)."""
    wrong: list[dict[str, str]] = []

    def add(cid: str, area: str, what: str) -> None:
        wrong.append({"id": cid, "area": area, "what": what})

    for c in cases:
        a = c["anomaly"]
        if c["answer_drift"]:
            add(c["id"], "정답지", "다시 만든 데이터가 정답과 다르다 (make_cases.py 다시 실행)")
        if a["expected"] and not a["detected"]:
            add(
                c["id"],
                "이상 탐지",
                f"{a['metric']} 이상을 못 잡음 (잡은 지표: {a['flagged'] or '없음'})",
            )
        if not a["expected"] and a["any_anomaly"]:
            add(c["id"], "이상 탐지", f"장애가 아닌데 이상이라고 함: {', '.join(a['flagged'])}")
        if c["kind"] == "empty" and not a["said_unknown"]:
            add(c["id"], "이상 탐지", f"데이터가 없는데 '확인할 수 없음' 대신: {a['summary']}")
        if a["fabricated"]:
            add(c["id"], "LLM 설명", f"지어낸 숫자가 남음: {a['summary']}")
        if "impact" in c:
            i = c["impact"]
            if i["got"] != i["want"]:
                add(
                    c["id"],
                    "영향 분석",
                    f"정답 {i['want']} / 결과 {i['got']} "
                    f"(구간 안 카드 거절 {i['declined_in_window']}건)",
                )
            if i["fabricated"]:
                add(c["id"], "LLM 설명", "영향 분석 설명에 지어낸 숫자가 남음")
        if "root_cause" in c:
            r = c["root_cause"]
            if r["top1"] is False:
                add(
                    c["id"],
                    "원인 추정",
                    f"주입한 원인 {r['want_ref']}가 1순위가 아님 "
                    f"(1순위: {r['first_refs'] or '후보 없음'})",
                )
            if r["ungrounded_survived"]:
                add(c["id"], "원인 추정", f"근거 없는 후보 {r['ungrounded_survived']}개가 남음")
    for s in sellers:
        if s["skill_status"] != "completed":
            add(
                s["id"], "사기 점수", f"skill이 답하지 않음 ({s['skill_outcome']}) — 확인할 수 없음"
            )
        if (s["rule_level"] == "high") != (s["label"] == "fraud"):
            add(
                s["id"],
                "사기 점수(규칙)",
                f"라벨 {s['label']} / 규칙 등급 {s['rule_level']} ({s['rule_score']}점)",
            )
    return wrong


async def evaluate() -> dict[str, Any]:
    scenarios, sellers = load_cases()
    cases = [await run_case(case) for case in scenarios]
    seller_rows = await run_sellers(sellers)
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "llm": "eval_llm.ScriptedLLM (평가용, 네트워크 없음)",
        "case_count": len(cases),
        "seller_count": len(seller_rows),
        "metrics": summarize(cases, seller_rows),
        "wrong": wrong_cases(cases, seller_rows),
        "cases": cases,
        "sellers": seller_rows,
    }


def compare(current: dict[str, Any], baseline: dict[str, Any], eps: float = 1e-6) -> list[str]:
    """기준보다 나빠진 지표. 기준에 값이 있는데 지금 값이 없어도 나빠진 것이다."""
    worse = []
    for name, (_, direction) in METRICS.items():
        if direction is None or name not in baseline:
            continue
        before, now = baseline[name], current.get(name)
        if before is None:
            continue
        if now is None:
            worse.append(f"{name}: 기준 {before} → 지금 확인할 수 없음")
        elif direction == "higher" and now < before - eps:
            worse.append(f"{name}: 기준 {before} → 지금 {now} (낮아짐)")
        elif direction == "lower" and now > before + eps:
            worse.append(f"{name}: 기준 {before} → 지금 {now} (높아짐)")
    return worse


def table(metrics: dict[str, Any]) -> str:
    rows = [f"{'지표':<40} {'값':>10}  설명"]
    for name, (desc, _) in METRICS.items():
        value = metrics.get(name)
        shown = "확인할 수 없음" if value is None else str(value)
        rows.append(f"{name:<40} {shown:>10}  {desc}")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-baseline", action="store_true", help="지금 값을 기준으로 저장")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # 윈도 콘솔(cp949)에서도 한국어가 깨지지 않게
    report = asyncio.run(evaluate())
    print(table(report["metrics"]))
    print(f"\n틀린 케이스 {len(report['wrong'])}개")
    for w in report["wrong"]:
        print(f"- [{w['area']}] {w['id']}: {w['what']}")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.write_baseline:
        baseline = {
            "generated_at": report["generated_at"],
            "case_count": report["case_count"],
            "metrics": report["metrics"],
        }
        BASELINE.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n기준 저장: {BASELINE}")
    elif BASELINE.exists():
        worse = compare(
            report["metrics"], json.loads(BASELINE.read_text(encoding="utf-8"))["metrics"]
        )
        print("\n기준 대비: " + ("나빠진 지표 없음" if not worse else "; ".join(worse)))


if __name__ == "__main__":
    main()
