"""DejaFix 현재 장애 → 총괄 `infra.anomaly_detected` (Zabbix 알람 → 자동 복구 연결).

DejaFix 레포는 고치지 않는다. DejaFix 읽기 전용 프록시(`dejafix-proxy`, 로그인은 프록시가 한다)로
`GET /api/incidents?status=problem`을 주기적으로 읽고, `component` 태그가 붙은 장애만 총괄에 알린다.
- 태그가 없는 장애(PC·호스트 문제)는 DejaFix가 맡으므로 보내지 않는다.
- 태그 값이 부위(`payment` 등)다. Zabbix 쪽 감시(DJF-02)가 트리거에 `component=<부위>` 태그를 단다.
- 원인 판단은 넣지 않는다. 같은 부위의 열린 사건이 있으면 총괄이 합친다.

같은 장애는 한 번만 보낸다(`idempotency_key`가 DejaFix 장애 id). 주기 실행이라 LLM을 부르지 않는다.
`INFRA_DEJAFIX_URL`이 없으면 켜지 않는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from ax_agent_template.events import publish
from ax_contracts.schemas import EventEnvelope, EventType
from ax_contracts.schemas.event import InfraAnomalyData

logger = logging.getLogger("ax.infra.dejafix_watch")

URL_ENV = "INFRA_DEJAFIX_URL"
INTERVAL_ENV = "INFRA_DEJAFIX_POLL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 30
MIN_INTERVAL_SECONDS = 5
TIMEOUT_SECONDS = 10.0
PROBLEMS_PATH = "/api/incidents?status=problem&limit=100"
COMPONENT_TAG = "component"
METRIC_TAG = "metric"
DEFAULT_METRIC = "zabbix_problem"

# 프로세스 안에서 이미 보낸 idempotency_key (재시작 뒤 중복은 이벤트 버스가 event_id로 막는다)
_published_keys: set[str] = set()


def reset_published() -> None:
    """발행 기록을 비운다 (테스트용)."""
    _published_keys.clear()


def base_url(env: Mapping[str, str] | None = None) -> str | None:
    raw = (os.environ if env is None else env).get(URL_ENV, "").strip()
    return raw.rstrip("/") or None


def interval_seconds(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get(INTERVAL_ENV, "").strip()
    try:
        return max(MIN_INTERVAL_SECONDS, int(raw))
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS


async def fetch_problems(url: str, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """DejaFix 현재 장애 목록 (계약 #10 형식). 실패하면 예외를 그대로 올린다."""
    if client is None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as own:
            return await fetch_problems(url, own)
    response = await client.get(f"{url}{PROBLEMS_PATH}")
    response.raise_for_status()
    rows = response.json().get("data") or []
    return [r for r in rows if isinstance(r, dict) and r.get("status") == "problem"]


def _tag(incident: Mapping[str, Any], name: str) -> str | None:
    for tag in incident.get("tags") or []:
        if isinstance(tag, dict) and tag.get("tag") == name and str(tag.get("value") or "").strip():
            return str(tag["value"]).strip()
    return None


def component_of(incident: Mapping[str, Any]) -> str | None:
    """총괄에 알릴 부위. `component` 태그가 없으면 None(DejaFix만 맡는 장애)."""
    return _tag(incident, COMPONENT_TAG)


def _started(incident: Mapping[str, Any], now: datetime) -> datetime:
    try:
        return datetime.fromisoformat(str(incident.get("started_at")).replace("Z", "+00:00"))
    except ValueError:
        return now


def _source_ref(incident: Mapping[str, Any]) -> str:
    event_id = (incident.get("zabbix") or {}).get("eventid")
    return f"zabbix:event:{event_id}" if event_id else f"dejafix:incident:{incident.get('id')}"


def envelope_for(incident: Mapping[str, Any], component: str, now: datetime) -> EventEnvelope:
    data = InfraAnomalyData(
        component=component,
        metric_code=_tag(incident, METRIC_TAG) or DEFAULT_METRIC,
        detected_at=_started(incident, now),
        source_ref=_source_ref(incident),
    )
    key = f"infra.anomaly_detected:dejafix:{incident.get('id')}"
    host = (incident.get("host") or {}).get("name")
    return EventEnvelope(
        event_id="evt_" + hashlib.sha256(key.encode()).hexdigest()[:16],
        type=EventType.INFRA_ANOMALY_DETECTED,
        occurred_at=now,
        context_id=None,
        source="infra",
        idempotency_key=key,
        data=data.model_dump(mode="json"),
        # DejaFix·Zabbix가 쓴 글이라 지시로 읽지 않는다
        untrusted={"title": incident.get("title"), "host": host},
    )


async def run_once(
    url: str, now: datetime, client: httpx.AsyncClient | None = None
) -> list[EventEnvelope]:
    """한 번 읽어 새로 보낸 이벤트를 돌려준다."""
    emitted: list[EventEnvelope] = []
    for incident in await fetch_problems(url, client):
        component = component_of(incident)
        if component is None:
            continue
        env = envelope_for(incident, component, now.astimezone(UTC))
        if env.idempotency_key in _published_keys:
            continue
        await publish(env)
        _published_keys.add(env.idempotency_key)
        emitted.append(env)
    return emitted


async def run_forever(
    url: str,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """`interval`초마다 `run_once`. 한 번 실패해도 다음 주기에 다시 읽는다."""
    while True:
        try:
            sent = await run_once(url, clock())
            for env in sent:
                logger.info("DejaFix 장애를 총괄에 알림: %s", env.idempotency_key)
        except Exception:  # noqa: BLE001 - DejaFix가 잠깐 안 돼도 감시는 이어 간다
            logger.warning("DejaFix 현재 장애를 읽지 못했다", exc_info=True)
        await asyncio.sleep(interval)


def start(env: Mapping[str, str] | None = None) -> asyncio.Task | None:
    """`INFRA_DEJAFIX_URL`이 있으면 감시를 켠다. 없으면 None."""
    url = base_url(env)
    if url is None:
        return None
    return asyncio.create_task(run_forever(url, interval_seconds(env)))
