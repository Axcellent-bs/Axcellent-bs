"""인프라 대화에 넣는 사실. 읽기만 한다 (상태를 바꾸지 않는다).

- 최근 배포 기록 (배포 기록 파일, 되돌린 배포 표시)
- 쇼핑몰 결제 서버 상태 (shop-api 장애 스위치 상태: 실패 중·느림)
- 최근 진단 결과 (이 서버가 기억하는 사건별 마지막 진단: 원인 후보·확신도)
- 최근 장애 사건과 그 안의 인프라 단계 (총괄 `GET /api/incidents`, 읽기 전용)
- DejaFix 현재 장애 (Zabbix 알람, `INFRA_DEJAFIX_URL`이 없으면 `configured: false`)

숫자·상태는 코드가 모으고 LLM은 풀어 쓸 뿐이다. 하나를 못 읽어도 나머지는 넣고,
못 읽은 이름은 `unavailable`에 남긴다(모르는 값을 채워 넣지 않는다).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ax_agent_template.config import agent_url, is_mock
from infra_agent import dejafix_watch
from infra_agent.runtime import get_runtime

logger = logging.getLogger(__name__)

RECENT = 8
ORCHESTRATOR_SECONDS = 5.0
INFRA_STEPS = ("diagnose", "rollback", "recovery")
NOTE_CHARS = 160


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _deployments() -> list[dict[str, Any]]:
    history = get_runtime().history
    rows = sorted(history.deployments, key=lambda d: d.deployed_at, reverse=True)[:RECENT]
    return [
        {
            "service": d.service,
            "version": d.version,
            "deployed_at": _iso(d.deployed_at),
            "components": list(d.components),
            "change_summary": d.change_summary,
            "rolled_back": d.deploy_id in history.rolled_back,
        }
        for d in rows
    ]


async def _shop() -> dict[str, Any]:
    state = await get_runtime().shop.fault_state()
    return {k: state.get(k) for k in ("active", "failing", "degraded", "delay_ms")}


def _diagnoses() -> list[dict[str, Any]]:
    rows = sorted(get_runtime().diagnoses.items(), key=lambda kv: kv[1].diagnosed_at, reverse=True)
    out = []
    for context_id, diag in rows[:RECENT]:
        o = diag.output
        out.append(
            {
                "context_id": context_id,
                "diagnosed_at": _iso(diag.diagnosed_at),
                "status": o.status,
                "suspects": [
                    {"service": s.service, "version": s.version, "confidence": s.confidence}
                    for s in o.suspects
                ],
            }
        )
    return out


async def _incidents() -> list[dict[str, Any]]:
    if is_mock("infra"):
        return []
    async with httpx.AsyncClient(timeout=ORCHESTRATOR_SECONDS) as client:
        response = await client.get(f"{agent_url('orchestrator')}/api/incidents")
    response.raise_for_status()
    rows = response.json().get("data") or []
    rows = sorted(
        (
            r
            for r in rows
            if isinstance(r, dict) and str(r.get("context_id", "")).startswith("ctx_inc_")
        ),
        key=lambda r: str(r.get("opened_at") or ""),
        reverse=True,
    )
    out = []
    for r in rows[:RECENT]:
        steps = [s for s in r.get("steps") or [] if s.get("step_id") in INFRA_STEPS]
        out.append(
            {
                "context_id": r.get("context_id"),
                "title": r.get("title"),
                "component": r.get("component"),
                "status": r.get("status"),
                "opened_at": r.get("opened_at"),
                "closed_at": r.get("closed_at"),
                "recovered_at": r.get("recovered_at"),
                "infra_steps": [
                    {
                        "step": s.get("step_id"),
                        "status": s.get("status"),
                        "note": str(s.get("note") or "")[:NOTE_CHARS],
                    }
                    for s in steps
                ],
            }
        )
    return out


async def _dejafix() -> dict[str, Any]:
    url = dejafix_watch.base_url()
    if url is None:
        return {"configured": False, "problems": []}
    rows = await dejafix_watch.fetch_problems(url)
    rows = sorted(rows, key=lambda r: str(r.get("started_at") or ""), reverse=True)
    return {
        "configured": True,
        "count": len(rows),
        "problems": [
            {
                "title": str(r.get("title") or "")[:NOTE_CHARS],
                "host": (r.get("host") or {}).get("name"),
                "severity": (r.get("severity") or {}).get("label"),
                "started_at": r.get("started_at"),
                # 이 값이 있으면 총괄에 장애로 알린 것이다(없으면 DejaFix만 맡음)
                "component": dejafix_watch.component_of(r),
            }
            for r in rows[:RECENT]
        ],
    }


async def collect() -> dict[str, Any]:
    """인프라 사실 묶음. 부분 실패는 `unavailable`에 이름만 남긴다(예외 내용은 넣지 않는다)."""
    out: dict[str, Any] = {"unavailable": []}
    for name, fn in (("recent_deployments", _deployments), ("recent_diagnoses", _diagnoses)):
        try:
            out[name] = fn()
        except Exception:  # noqa: BLE001 - 하나를 못 읽어도 대화는 이어 간다
            logger.warning("인프라 대화 사실 %s를 읽지 못했다", name, exc_info=True)
            out["unavailable"].append(name)
    for name, afn in (
        ("shop_payment_server", _shop),
        ("recent_incidents", _incidents),
        ("dejafix_problems", _dejafix),
    ):
        try:
            out[name] = await afn()
        except Exception:  # noqa: BLE001
            logger.warning("인프라 대화 사실 %s를 읽지 못했다", name, exc_info=True)
            out["unavailable"].append(name)
    return out
