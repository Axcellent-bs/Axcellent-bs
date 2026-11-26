"""DejaFix 현재 장애 → 총괄 `infra.anomaly_detected` (Zabbix 알람 → 자동 복구 연결).

DejaFix 레포는 고치지 않는다. DejaFix 읽기 전용 프록시(`dejafix-proxy`, 로그인은 프록시가 한다)로
`GET /api/incidents?status=problem`을 주기적으로 읽고, `component` 태그가 붙은 장애만 총괄에 알린다.
- 태그가 없는 장애(PC·호스트 문제)는 DejaFix가 맡으므로 보내지 않는다.
- 태그 값이 부위(`payment` 등)다. Zabbix 쪽 감시(DJF-02)가 트리거에 `component=<부위>` 태그를 단다.
- 원인 판단은 넣지 않는다. 같은 부위의 열린 사건이 있으면 총괄이 합친다.

같은 장애는 한 번만 보낸다(`idempotency_key`가 DejaFix 장애 id). 주기 실행이라 LLM을 부르지 않는다.
주소는 매 회차 정한다(`base_url`): `INFRA_DEJAFIX_URL` → 보관함에 `dejafix`가 있으면 dejafix-proxy →
없으면 그 회차는 건너뛴다. 시작할 때 아무것도 없어도 감시는 켜 두어, 나중에 화면에서 연결하면
재시작 없이 읽기 시작한다.

`INFRA_DEJAFIX_COMPONENTS`(쉼표로 구분, 예: `payment`)를 주면 그 부위만 보낸다. Zabbix 기본
템플릿(Windows·Linux)이 PC·호스트 트리거에도 `component=memory`·`cpu` 같은 태그를 달아서, 거르지
않으면 팀원 PC 경고가 총괄 사건으로 열린다. 비우면 `component` 태그가 있는 장애를 모두 보낸다.

복구 신호 `infra.recovered` (2026-11-23, 로드맵 2단계): 이 감시가 보낸 장애가 현재 장애 목록에서
빠지면 총괄에 한 번 알린다. 가짜 복구를 막으려고(총괄 #1457 의견)
- 조회가 실패한 회차(오류·시간 초과)는 세지 않는다
- 조회가 성공한 회차에서 **연속 두 번** 빠져야 하고, 그다음 DejaFix 장애 상세
  (`GET /api/incidents/{id}`)에서 `problem`이 아님을 확인한다. 빈 목록도 상세로 확인하므로 센다
- 복구 시각 `recovered_at`은 DejaFix 상세의 `resolved_at`이다
  (감시가 알아챈 시각이 아님, 없으면 비움)
- 데이터는 장애 신호와 같은 `component`·`metric_code`·`source_ref`를 싣는다. 복구 방식
  `recovery`는 DejaFix가 알려 주지 않아 비운다

회사(11/26): 회차마다 회사마다 따로 읽는다. 프록시에 그 회사(`X-AX-Company`)를 싣고, 그 회사의
회사 연결 설정(Zabbix)을 거친 장애만 그 회사(`tenant_id`)로 총괄에 알린다. 회사 없이 부르거나
캣북스로 떨어지는 길은 없다(`tool_connections.dejafix_company`). 그 회사 Zabbix가 연결 전이면
(프록시 503 `DEJAFIX_NOT_CONNECTED`) 그 회차에 그 회사만 건너뛴다. 보낸 장애·복구 셈도 회사마다.
캣북스의 이벤트 키·회사 값은 예전 그대로다(이미 보낸 장애를 다시 보내지 않게).
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

from ax_agent_template import company_connection, company_scope, tool_connections
from ax_agent_template.companies import company_ids
from ax_agent_template.events import publish
from ax_contracts.schemas import EventEnvelope, EventType
from ax_contracts.schemas.event import InfraAnomalyData

CALLER_COMPANY = company_connection.CALLER_COMPANY_HEADER

logger = logging.getLogger("ax.infra.dejafix_watch")

URL_ENV = "INFRA_DEJAFIX_URL"
INTERVAL_ENV = "INFRA_DEJAFIX_POLL_SECONDS"
COMPONENTS_ENV = "INFRA_DEJAFIX_COMPONENTS"
DEFAULT_INTERVAL_SECONDS = 30
MIN_INTERVAL_SECONDS = 5
TIMEOUT_SECONDS = 10.0
PROBLEMS_PATH = "/api/incidents?status=problem&limit=100"
DETAIL_PATH = "/api/incidents/{id}"
RECOVER_AFTER_MISSES = 2  # 조회가 성공한 회차에서 연속으로 빠진 횟수
COMPONENT_TAG = "component"
METRIC_TAG = "metric"
DEFAULT_METRIC = "zabbix_problem"

# 프로세스 안에서 이미 보낸 idempotency_key (재시작 뒤 중복은 이벤트 버스가 event_id로 막는다)
_published_keys: set[str] = set()
# 장애 신호를 보냈고 아직 복구 신호를 안 보낸 장애:
# 회사 → DejaFix 장애 id → 그때 보낸 데이터 + 빠진 횟수
_open: dict[str, dict[str, dict[str, Any]]] = {}


class DejafixNotConnected(RuntimeError):
    """그 회사의 Zabbix(DejaFix)가 연결 전이다(프록시 503 `DEJAFIX_NOT_CONNECTED`)."""


def reset_published() -> None:
    """발행 기록을 비운다 (테스트용)."""
    _published_keys.clear()
    _open.clear()


def base_url(env: Mapping[str, str] | None = None, company: str | None = None) -> str | None:
    """이번에 읽을 주소: 환경변수 → 그 회사 보관함 dejafix(프록시) → None.

    `env`를 주면 환경변수만 본다.

    `company`가 없으면 지금 요청의 회사(요청 밖이면 `DejafixNoCompany`)."""
    if env is not None:
        return env.get(URL_ENV, "").strip().rstrip("/") or None
    return tool_connections.dejafix_proxy_url(URL_ENV, tool_connections.dejafix_company(company))


def watched_companies() -> list[str]:
    """이번 회차에 읽을 회사: 캣북스 + 회사 목록의 회사(정식 ID, 겹치지 않게)."""
    out = [company_scope.CATBOOKS]
    for cid in company_ids():
        cid = company_scope.scope_id(cid)
        if cid not in out:
            out.append(cid)
    return out


def _event_tenant(company: str) -> dict[str, str]:
    """봉투 회사 값: 캣북스는 예전 그대로(기본값), 다른 회사는 그 회사 ID."""
    return {} if company == company_scope.CATBOOKS else {"tenant_id": company}


def _key_part(company: str, incident_id: Any) -> str:
    """이벤트 키의 장애 부분. 캣북스는 예전 그대로, 다른 회사는 회사를 앞에(id가 겹칠 수 있다)."""
    return str(incident_id) if company == company_scope.CATBOOKS else f"{company}:{incident_id}"


def _check(response: httpx.Response) -> None:
    if response.status_code == 503:
        try:
            code = (response.json().get("error") or {}).get("code")
        except ValueError:
            code = None
        if code == "DEJAFIX_NOT_CONNECTED":
            raise DejafixNotConnected("이 회사의 Zabbix가 아직 연결되지 않았다")
    response.raise_for_status()


def interval_seconds(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get(INTERVAL_ENV, "").strip()
    try:
        return max(MIN_INTERVAL_SECONDS, int(raw))
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS


def allowed_components(env: Mapping[str, str] | None = None) -> frozenset[str] | None:
    """총괄에 보낼 부위 목록. 비우면 None(모든 부위)."""
    raw = (os.environ if env is None else env).get(COMPONENTS_ENV, "")
    names = frozenset(p.strip() for p in raw.split(",") if p.strip())
    return names or None


async def fetch_problems(
    url: str, client: httpx.AsyncClient | None = None, company: str | None = None
) -> list[dict[str, Any]]:
    """그 회사의 DejaFix 현재 장애 목록 (계약 #10 형식). 실패하면 예외를 그대로 올린다.

    `company`가 없으면 지금 요청의 회사. 둘 다 없으면 부르지 않고 `DejafixNoCompany`.
    그 회사 Zabbix가 연결 전이면 `DejafixNotConnected`."""
    # dejafix-proxy는 호출자 토큰과 회사를 본다 (회사를 먼저 정해, 없으면 부르지 않는다)
    headers = tool_connections.dejafix_proxy_headers(company)
    if client is None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as own:
            return await fetch_problems(url, own, headers[CALLER_COMPANY])
    response = await client.get(f"{url}{PROBLEMS_PATH}", headers=headers)
    _check(response)
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


def envelope_for(
    incident: Mapping[str, Any],
    component: str,
    now: datetime,
    company: str = company_scope.CATBOOKS,
) -> EventEnvelope:
    data = InfraAnomalyData(
        component=component,
        metric_code=_tag(incident, METRIC_TAG) or DEFAULT_METRIC,
        detected_at=_started(incident, now),
        source_ref=_source_ref(incident),
    )
    key = f"infra.anomaly_detected:dejafix:{_key_part(company, incident.get('id'))}"
    host = (incident.get("host") or {}).get("name")
    return EventEnvelope(
        event_id="evt_" + hashlib.sha256(key.encode()).hexdigest()[:16],
        type=EventType.INFRA_ANOMALY_DETECTED,
        **_event_tenant(company),
        occurred_at=now,
        context_id=None,
        source="infra",
        idempotency_key=key,
        # 복구 칸(recovery·recovered_at)은 infra.recovered에만 싣는다 (장애 신호 모양은 그대로)
        data=data.model_dump(mode="json", exclude={"recovery", "recovered_at"}),
        # DejaFix·Zabbix가 쓴 글이라 지시로 읽지 않는다
        untrusted={"title": incident.get("title"), "host": host},
    )


async def fetch_detail(
    url: str,
    incident_id: str,
    client: httpx.AsyncClient | None = None,
    company: str | None = None,
) -> dict[str, Any] | None:
    """그 회사의 DejaFix 장애 상세 (계약 #10 형식). 실패하거나 모양이 다르면 None."""
    headers = tool_connections.dejafix_proxy_headers(company)
    if client is None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as own:
            return await fetch_detail(url, incident_id, own, headers[CALLER_COMPANY])
    try:
        response = await client.get(f"{url}{DETAIL_PATH.format(id=incident_id)}", headers=headers)
        _check(response)
        body = response.json()
    except (httpx.HTTPError, ValueError, DejafixNotConnected):
        logger.warning("DejaFix 장애 상세를 읽지 못했다: %s", incident_id, exc_info=True)
        return None
    row = body.get("data") if isinstance(body, dict) else None
    return row if isinstance(row, dict) else None


def _resolved(detail: Mapping[str, Any]) -> datetime | None:
    raw = detail.get("resolved_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def recovered_envelope(
    incident_id: str,
    sent: Mapping[str, Any],
    resolved_at: datetime | None,
    now: datetime,
    company: str = company_scope.CATBOOKS,
) -> EventEnvelope:
    """`infra.recovered`. 장애 신호에 실었던 부위·지표·근거를 그대로 싣는다."""
    data = InfraAnomalyData.model_validate({**sent, "recovery": None, "recovered_at": resolved_at})
    key = f"infra.recovered:dejafix:{_key_part(company, incident_id)}"
    return EventEnvelope(
        event_id="evt_" + hashlib.sha256(key.encode()).hexdigest()[:16],
        type=EventType.INFRA_RECOVERED,
        **_event_tenant(company),
        occurred_at=now,
        context_id=None,
        source="infra",
        idempotency_key=key,
        data=data.model_dump(mode="json"),
    )


async def _check_recovered(
    url: str, seen: set[str], now: datetime, client: httpx.AsyncClient | None, company: str
) -> list[EventEnvelope]:
    """보냈던 장애가 목록에서 빠졌으면 세고, 연속 두 번이면 상세로 확인해 복구 신호를 보낸다."""
    emitted: list[EventEnvelope] = []
    opened = _open.setdefault(company, {})
    for incident_id in list(opened):
        entry = opened[incident_id]
        if incident_id in seen:
            entry["misses"] = 0
            continue
        entry["misses"] += 1
        if entry["misses"] < RECOVER_AFTER_MISSES:
            continue
        detail = await fetch_detail(url, incident_id, client, company)
        if detail is None:
            continue  # 상세를 못 읽으면 다음 회차에 다시 확인 (빠진 횟수는 그대로)
        if detail.get("status") == "problem":
            entry["misses"] = 0  # 목록에서만 빠졌고 아직 장애다
            continue
        env = recovered_envelope(incident_id, entry["data"], _resolved(detail), now, company)
        await publish(env)
        _published_keys.add(env.idempotency_key)
        del opened[incident_id]
        emitted.append(env)
    return emitted


async def run_once(
    url: str,
    now: datetime,
    client: httpx.AsyncClient | None = None,
    components: frozenset[str] | None = None,
    company: str | None = None,
) -> list[EventEnvelope]:
    """그 회사를 한 번 읽어 새로 보낸 이벤트(장애·복구)를 돌려준다. `components`를 주면 그 부위만.

    `company`가 없으면 지금 요청의 회사, 둘 다 없으면 `DejafixNoCompany`(캣북스로 떨어지지 않는다).
    목록 조회가 실패하면 예외를 올리고 복구는 세지 않는다."""
    cid = tool_connections.dejafix_company(company)
    emitted: list[EventEnvelope] = []
    now = now.astimezone(UTC)
    seen: set[str] = set()
    for incident in await fetch_problems(url, client, cid):
        seen.add(str(incident.get("id")))
        component = component_of(incident)
        if component is None:
            continue
        if components is not None and component not in components:
            continue  # 고른 부위가 아니면(예: 팀원 PC의 memory) 총괄에 알리지 않는다
        env = envelope_for(incident, component, now, cid)
        if env.idempotency_key in _published_keys:
            continue
        await publish(env)
        _published_keys.add(env.idempotency_key)
        _open.setdefault(cid, {})[str(incident.get("id"))] = {"data": env.data, "misses": 0}
        emitted.append(env)
    emitted.extend(await _check_recovered(url, seen, now, client, cid))
    return emitted


async def run_forever(
    url: str | Callable[[str], str | None] | None = None,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    components: frozenset[str] | None = None,
    companies: Callable[[], list[str]] = watched_companies,
) -> None:
    """`interval`초마다 회사마다 `run_once`. 주소는 회차·회사마다 정하고(없으면 그 회사는 건너뜀),
    한 회사가 실패하거나 연결 전이어도 다른 회사와 다음 회차는 읽는다."""
    while True:
        for cid in companies():
            try:
                target = url(cid) if callable(url) else (url or base_url(company=cid))
                if target is None:
                    continue
                sent = await run_once(target, clock(), components=components, company=cid)
                for env in sent:
                    logger.info("DejaFix 장애를 총괄에 알림: %s", env.idempotency_key)
            except DejafixNotConnected:
                logger.debug("DejaFix: %s 회사 Zabbix 연결 전", cid)
            except Exception:  # noqa: BLE001 - DejaFix가 잠깐 안 돼도 감시는 이어 간다
                logger.warning("DejaFix 현재 장애를 읽지 못했다: %s", cid, exc_info=True)
        await asyncio.sleep(interval)


def start(env: Mapping[str, str] | None = None) -> asyncio.Task:
    """감시를 켠다. 주소가 아직 없어도 켜 두고 매 회차 다시 찾는다."""
    return asyncio.create_task(
        run_forever(
            (lambda _cid: base_url(env)) if env is not None else None,
            interval_seconds(env),
            components=allowed_components(env),
        )
    )
