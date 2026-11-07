"""성과 결과 → 기획 보내기 평가 정의(`eval/campaign_update/`)의 약속.

- 7일이 끝난 캠페인만 1번 보낸다. 시험·제안 번호 없음·결과 unknown은 안 보내고 이유를 남긴다
- direction 네 가지, summary 200자 이내(넘으면 짧은 고정형), facts 12개 이하·길이 제한, 네 칸
  따로·마이너스 그대로
- 기획 응답: applied·duplicate 보냄, unknown_proposal·그 밖의 거절은 다시 안 함, 연결 실패·5xx는
  하루 3번까지 다시. 기능 플래그가 꺼져 있으면 호출 0회
- 재무 기록용 값: 집행액 칸 없음, 음수 보존, 모르면 None
- 실제 계약 모델(ReceiveCampaignUpdateInput·CampaignResultInput)로 검증한다. 기획·재무는 부르지 않음
- 이 PR에서는 관찰만 (관문은 코드 PR에서)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "campaign_update"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"campaign_update_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_rules() -> None:
    cases = {c["name"]: c for c in make.cases()}
    assert cases["all_measured"]["expected"]["kind"] == "send"
    assert cases["still_measuring"]["expected"]["kind"] == "wait"
    assert cases["period_over"]["expected"]["kind"] == "send"
    assert cases["test_campaign"]["expected"] == {"kind": "skip", "reason": "시험 캠페인"}
    assert [cases[f"map_{n}"]["expected"]["direction"] for n in
            ("more", "less_loss", "same", "low_sample", "unmeasurable")] == [
        "늘었음", "줄었음", "같음", "확인 불가", "확인 불가"]  # fmt: skip
    assert cases["map_less_loss"]["expected"]["summary"].endswith(" · 매출 감소 경고")
    assert "-300,000원" in cases["map_less_loss"]["expected"]["summary"]
    assert cases["map_long_summary"]["expected"]["summary"] == "조회 달성 · 매출 확인 불가"
    for c in cases.values():
        if c["kind"] == "payload":
            assert len(c["expected"]["summary"]) <= 200 and len(c["expected"]["facts"]) <= 12
    assert cases["reply_retry_3_per_day"]["expected"] == {"calls": 4, "state": "failed",
                                                          "failures": 1, "reason": None}
    fin = cases["finance_less_negative"]["expected"]
    assert (fin["gmv_krw"], fin["orders"], fin["measured_gmv_krw"]) == (0, 0, -300_000)
    assert "spend_krw" not in fin and cases["finance_unknown"]["expected"]["gmv_krw"] is None
    assert make.cases() == list(cases.values())


def test_the_scorer() -> None:
    cases = make.cases()
    perfect: dict[str, dict] = {}
    for c in cases:
        if c["kind"] in ("payload", "finance"):
            perfect[c["name"]] = {"valid": True, **({"record": c["expected"]}
                                                    if c["kind"] == "finance" else c["expected"])}
        else:
            perfect[c["name"]] = dict(c["expected"])
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)


# ---------------------------------------------------------------- 실행 (모듈이 없으면 None = 0점)


def _output(r: dict, now: datetime):  # noqa: ANN202
    from ax_contracts.schemas.data import (
        CampaignAlert,
        CampaignResultOutput,
        CampaignSalesCell,
        CampaignSalesEffect,
        CampaignSalesVerdict,
        VideoCheckpoint,
        VideoPerformanceOutput,
    )

    start = datetime.fromisoformat(r["window_from"])
    videos = [
        VideoPerformanceOutput(
            video_id=f"v{n}", campaign_id=r["campaign_id"], test=v["test"], verdict=v["verdict"],
            checkpoints=[VideoCheckpoint(
                checkpoint="7d", status="known" if v["verdict"] != "measuring" else "unknown",
                target_ratio=v["ratio"])],
        )
        for n, v in enumerate(r["videos"])
    ]  # fmt: skip
    s = r["sales"]
    verdict = CampaignSalesVerdict(
        status=s["status"], verdict=s["verdict"], label=s["label"], low_sample=s["low_sample"],
        low_sample_reason=s["low_sample_reason"], incremental_gross_krw=s["gross"],
        incremental_orders=s["orders"],
    )  # fmt: skip
    cells = [CampaignSalesCell(key=c["key"], label=c["label"], status=c["status"], line=c["line"],
                               reason=c["reason"]) for c in s["cells"]]  # fmt: skip
    return CampaignResultOutput(
        status=r["status"], reason=r["reason"], campaign_id=r["campaign_id"],
        campaign_name=r["campaign_name"], insight_id=r["insight_id"],
        proposal_id=r["proposal_id"], test=r["test"], window_from=start,
        window_to=min(start + timedelta(days=7), now), videos=videos,
        sales=CampaignSalesEffect(status=s["status"], verdict=verdict, cells=cells),
        alerts=[CampaignAlert(kind="loss", message="줄었음") for k in r["alerts"] if k == "loss"],
    )  # fmt: skip


def _run(case: dict, tmp: Path) -> dict | None:
    try:
        from data_agent import campaign_update as cu
        from data_agent.skills.campaign_result import finance_record
    except ImportError:
        return None
    from ax_contracts.schemas import AgentResult, TaskStatus
    from ax_contracts.schemas.execute import ExecuteDecision
    from ax_contracts.schemas.finance import CampaignResultInput
    from ax_contracts.schemas.planning import ReceiveCampaignUpdateInput

    r = case["result"]
    if case["kind"] == "decide":
        now = datetime.fromisoformat(case["now"])
        d = cu.decide(_output(r, now), now)
        got = {"kind": d.kind, "reason": d.reason}
        if d.kind == "wait":
            got["send_after"] = d.send_after.isoformat() if d.send_after else None
        return got
    end = make.T0 + timedelta(days=8)
    if case["kind"] == "payload":
        update = cu.build_update(_output(r, end))
        body = update.model_dump(mode="json")
        try:
            ReceiveCampaignUpdateInput.model_validate(body)
            valid = True
        except ValueError:
            valid = False
        return {"valid": valid, "direction": body["direction"], "summary": body["summary"],
                "facts": [{k: f[k] for k in ("label", "value", "source")}
                          for f in body["facts"]]}  # fmt: skip
    if case["kind"] == "finance":
        record = finance_record(_output(r, end), None).model_dump(mode="json")
        try:  # 마케팅이 이름·채널·집행액을 더해 재무에 보낸다
            CampaignResultInput.model_validate({**record, "campaign_name": r["campaign_name"],
                                                "channel": "youtube", "spend_krw": 100_000})
            valid = "spend_krw" not in record
        except ValueError:
            valid = False
        keep = {k: record.get(k) for k in case["expected"]}
        return {"valid": valid, "record": keep}
    # reply · flag_off: 루프마다 보내기
    env = {"DATA_SEND_CAMPAIGN_UPDATE": "0" if case["kind"] == "flag_off" else "1"}
    store = cu.SendStore(tmp / f"{case['name']}.db")
    calls = 0
    for t in case["ticks"]:
        now = make.T0 + timedelta(hours=t["hours"])

        async def send(target, req, _reply=t["reply"]):  # noqa: ANN001, ANN202
            nonlocal calls
            calls += 1
            assert target == "planning" and req.skill == "receive_campaign_update"
            ReceiveCampaignUpdateInput.model_validate(req.input)
            done = _reply["status"] == "completed"
            return AgentResult(meta=req.meta,
                               status=TaskStatus.COMPLETED if done else TaskStatus.FAILED,
                               outcome=_reply["outcome"],
                               output={"outcome": _reply["outcome"]} if done else {})  # fmt: skip

        async def gate(action, params, meta, evidence):  # noqa: ANN001, ANN202
            return ExecuteDecision(execution_id="exe_eval", grade="A", status="executed")

        asyncio.run(cu.send_ready([_output(r, now)], store, env, now, send=send, gate=gate))
    status = cu.send_status(_output(r, now), store, env, now)
    store.close()
    if case["kind"] == "flag_off":
        return {"calls": calls, "state": status.state}
    return {"calls": calls, "state": status.state, "failures": status.failures,
            "reason": status.reason}  # fmt: skip


def observe(tmp_path: Path) -> dict[str, float]:
    cases = make.cases()
    return score.score(cases, {c["name"]: _run(c, tmp_path) for c in cases})


def test_campaign_update_observe(tmp_path: Path) -> None:
    """관찰만: 지표를 출력하고 모양만 본다 (관문은 코드 PR에서 켬)."""
    metrics = observe(tmp_path)
    print("campaign_update eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
    assert all(0.0 <= v <= 1.0 for v in metrics.values())
