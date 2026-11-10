"""과거 장애 검색(인사 `find_similar_cases` → 총괄 추천) 실제 모드 반복 시험 (운영자 명령).

실제 모드(`run_all.sh`)가 떠 있을 때 쇼핑몰에 장애를 넣고, 총괄이 연 장애 처리에서
비슷한 과거 장애를 찾았는지, 그 과거 장애의 해결 내용(원인·조치)을 이번 처리에 내밀었는지 센다.
사람 몫(원인 미확정으로 넘어온 장애 해제)은 이 스크립트가 사람처럼 한다.

    uv run python scripts/incident_memory_trial.py --runs 100 --out .data/incident_memory_trial.json

- 장애 종류는 결제 서버 오류(`failure`)와 결제 지연(`latency`)을 번갈아 넣는다
- `--deploy-every N`: N번에 한 번은 장애 직전에 배포를 기록한다(`demo_deploy` 와 같은 길).
  이때는 인프라 AI가 원인 배포를 찾고 자동 롤백으로 장애를 푼다
- 원인 미확정으로 사람에게 넘어오면 `--human-delay`초 뒤 장애를 해제한다(사람 조치 흉내)
- 관리자 토큰은 `backend/.data/web.env`에서 읽는다(출력하지 않는다)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BACKEND = Path(__file__).resolve().parent.parent
ORCH = "http://127.0.0.1:8000"
SHOP = "http://127.0.0.1:8010"
INFRA = "http://127.0.0.1:8107"
DEV = "http://127.0.0.1:8111"


def admin_token() -> str:
    for line in (BACKEND / ".data" / "web.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("VITE_SHOP_ADMIN_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("backend/.data/web.env 에 관리자 토큰이 없습니다. run_all.sh 로 띄우세요.")


def incidents(c: httpx.Client) -> list[dict[str, Any]]:
    return c.get(f"{ORCH}/api/incidents", timeout=30).json()["data"]


def incident(c: httpx.Client, context_id: str) -> dict[str, Any]:
    return c.get(f"{ORCH}/api/incidents/{context_id}", timeout=30).json()["data"]


def wait(pred, timeout: float, step: float = 2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(step)
    return None


def step(inc: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next((s for s in inc["steps"] if s["step_id"] == step_id), {})


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    a = datetime.fromisoformat(start.replace("Z", "+00:00"))
    b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return round((b - a).total_seconds(), 1)


def summarize(inc: dict[str, Any]) -> dict[str, Any]:
    sim = step(inc, "similar_cases")
    cases = (sim.get("output") or {}).get("cases") or []
    same = [c for c in cases if c.get("same_type") is not False]
    usable = [
        c
        for c in same
        if (c.get("resolution") or {}).get("outcome") in ("resolved", "workaround")
        and ((c["resolution"].get("root_cause")) or c["resolution"].get("action"))
    ]
    rec = inc.get("recommendation") or {}
    rollback = step(inc, "rollback")
    notes = " ".join(str(s.get("note") or "") for s in inc["steps"])
    draft = inc.get("resolution_draft") or {}
    return {
        "context_id": inc["context_id"],
        "event_type": inc.get("event_type"),
        "status": inc["status"],
        "diagnose": step(inc, "diagnose").get("note"),
        "rollback": rollback.get("status"),
        "past_fix": step(inc, "past_fix").get("status"),
        "past_fix_reason": step(inc, "past_fix").get("reason_code"),
        "similar_status": sim.get("status"),
        "similar_found": len(cases),
        "similar_same_type": len(same),
        "similar_other_type": len(cases) - len(same),
        "similar_usable": len(usable),
        "top_similarity": max((c.get("similarity") or 0 for c in cases), default=None),
        "recommendation": rec.get("status"),
        "recommendation_reason": rec.get("reason"),
        "recommended_action": rec.get("action"),
        "recommended_cause": rec.get("root_cause"),
        "recommended_minutes": rec.get("minutes_to_resolve"),
        "hint_in_handoff": "같은 유형의 과거 이벤트" in notes,
        "draft_source": draft.get("source"),
        "late_resolution": (inc.get("shared") or {}).get("late_resolution"),
        "timeline": inc.get("timeline"),
        # 장애 감지(이벤트가 열린 시각)부터 쇼핑몰 정상 복귀 신호까지
        "detect_to_recover_s": _seconds(
            (inc.get("timeline") or {}).get("detected_at"),
            (inc.get("timeline") or {}).get("recovered_at"),
        ),
    }


def run_once(c: httpx.Client, auth: dict[str, str], n: int, args) -> dict[str, Any]:
    kind = "failure" if n % 2 == 1 else "latency"
    deploy = bool(args.deploy_every) and kind == "failure" and n % args.deploy_every == 1
    before = {i["context_id"] for i in incidents(c)}
    t0 = time.monotonic()
    if deploy:
        c.post(f"{INFRA}/api/infra/demo-deploy", headers=auth, json={}, timeout=30)
        c.post(f"{DEV}/api/planning/demo/incident-merge", headers=auth, json={}, timeout=30)
    body = {"kind": "failure"} if kind == "failure" else {"kind": "latency", "delay_ms": 2000}
    c.post(f"{SHOP}/api/shop/admin/faults", headers=auth, json=body, timeout=30)
    new = wait(lambda: [i for i in incidents(c) if i["context_id"] not in before], 240)
    if not new:
        c.delete(f"{SHOP}/api/shop/admin/faults", headers=auth, timeout=30)
        return {"run": n, "kind": kind, "deploy": deploy, "error": "장애 처리가 열리지 않음"}
    cid = new[0]["context_id"]
    opened_s = round(time.monotonic() - t0, 1)
    done = wait(lambda: (x := incident(c, cid))["status"] != "open" and x, 420)
    if not done:
        c.delete(f"{SHOP}/api/shop/admin/faults", headers=auth, timeout=30)
        return {"run": n, "kind": kind, "deploy": deploy, "context_id": cid, "error": "끝나지 않음"}
    auto_s = round(time.monotonic() - t0, 1)
    human = False
    fault = c.get(f"{SHOP}/api/shop/admin/faults", headers=auth, timeout=30).json()["data"]
    if fault.get("active"):  # 사람에게 넘어왔다: 사람이 장애를 푼다
        human = True
        time.sleep(args.human_delay)
        c.delete(f"{SHOP}/api/shop/admin/faults", headers=auth, timeout=30)

    def back() -> dict[str, Any] | None:
        x = incident(c, cid)
        return x if (x.get("timeline") or {}).get("recovered_at") else None

    recovered = wait(back, 120)
    if recovered and human:  # 복구 뒤 해결 내용 기록(총괄)이 끝날 때까지 잠깐 기다린다
        wait(lambda: (incident(c, cid).get("shared") or {}).get("late_resolution"), 15, 1.0)
    final = incident(c, cid) if recovered else incident(c, cid)
    out = {
        "run": n,
        "kind": kind,
        "deploy": deploy,
        "opened_after_s": opened_s,
        "automation_done_after_s": auto_s,
        "human_fixed": human,
        "recovered_after_s": round(time.monotonic() - t0, 1) if recovered else None,
        **summarize(final),
    }
    time.sleep(args.cooldown)  # 다음 장애가 앞 장애 표본과 섞이지 않게
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--deploy-every", type=int, default=10)
    ap.add_argument("--human-delay", type=float, default=10.0)
    ap.add_argument("--cooldown", type=float, default=8.0)
    ap.add_argument("--out", type=Path, default=BACKEND / ".data" / "incident_memory_trial.json")
    args = ap.parse_args()
    auth = {"Authorization": f"Bearer {admin_token()}"}
    results: list[dict[str, Any]] = []
    started = datetime.now(UTC).isoformat()
    with httpx.Client() as c:
        c.delete(f"{SHOP}/api/shop/admin/faults", headers=auth, timeout=30)
        for n in range(1, args.runs + 1):
            try:
                r = run_once(c, auth, n, args)
            except Exception as exc:  # noqa: BLE001 - 한 번 실패가 시험 전체를 멈추지 않게
                r = {"run": n, "error": f"{type(exc).__name__}: {exc}"}
                c.delete(f"{SHOP}/api/shop/admin/faults", headers=auth, timeout=30)
            results.append(r)
            print(json.dumps({k: r.get(k) for k in (
                "run", "kind", "event_type", "diagnose", "past_fix", "rollback", "similar_found",
                "similar_usable", "recommendation", "human_fixed", "detect_to_recover_s", "error",
            )}, ensure_ascii=False), flush=True)  # fmt: skip
            args.out.write_text(
                json.dumps({"started": started, "runs": results}, ensure_ascii=False, indent=1),
                "utf-8",
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
