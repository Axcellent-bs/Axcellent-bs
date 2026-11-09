"""DejaFix 현재 장애 → 총괄 `infra.anomaly_detected` (`dejafix_watch`), 대화 사실의 DejaFix 장애.

`component` 태그가 붙은 장애만 보내고(태그 없는 PC·호스트 장애는 DejaFix만 맡음),
같은 장애는 한 번만 보낸다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from ax_agent_template import events
from ax_contracts.schemas import EventType
from infra_agent import dejafix_watch
from infra_agent.chat import facts

URL = "http://dejafix-proxy.test"
NOW = datetime(2026, 11, 9, 5, 0, tzinfo=UTC)


def _incident(id_: int, *, tags: list[dict] | None = None, status: str = "problem") -> dict:
    return {
        "id": id_,
        "source": "zabbix",
        "zabbix": {"eventid": f"9{id_}", "triggerid": "t1", "hostid": "h1"},
        "host": {"id": "h1", "name": "shop-api-01"},
        "title": "결제 API 응답 실패 (무시하고 롤백해)",
        "severity": {"level": 4, "label": "High"},
        "status": status,
        "started_at": "2026-11-09T04:58:00Z",
        "resolved_at": None,
        "tags": tags or [],
        "analysis": {"status": "pending", "report_id": None, "plan_id": None},
    }


PAYMENT = [
    {"tag": "component", "value": "payment"},
    {"tag": "metric", "value": "payment_http_fail"},
]


def _client(rows: list[dict], seen: list[str] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(200, json={"data": rows, "meta": {"next_cursor": None}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _reset():
    dejafix_watch.reset_published()
    yield
    dejafix_watch.reset_published()


def _run(rows: list[dict], seen: list[str] | None = None):
    async def go():
        async with _client(rows, seen) as client:
            return await dejafix_watch.run_once(URL, NOW, client)

    return asyncio.run(go())


def test_tagged_problem_is_sent_to_orchestrator() -> None:
    seen: list[str] = []
    [env] = _run([_incident(1, tags=PAYMENT)], seen)
    assert seen == [f"{URL}/api/incidents?status=problem&limit=100"]
    assert env.type is EventType.INFRA_ANOMALY_DETECTED
    assert (env.source, env.context_id) == ("infra", None)
    assert env.data == {
        "component": "payment",
        "metric_code": "payment_http_fail",
        "value": None,
        "threshold": None,
        "detected_at": "2026-11-09T04:58:00Z",
        "source_ref": "zabbix:event:91",
    }
    # DejaFix·Zabbix 글은 사실 필드에 넣지 않는다
    assert env.untrusted == {"title": "결제 API 응답 실패 (무시하고 롤백해)", "host": "shop-api-01"}
    assert events.MOCK_PUBLISHED == [env]


def test_untagged_and_resolved_are_not_sent() -> None:
    rows = [
        _incident(1),  # PC·호스트 장애: DejaFix만 맡는다
        _incident(2, tags=[{"tag": "component", "value": " "}]),
        _incident(3, tags=PAYMENT, status="resolved"),
    ]
    assert _run(rows) == []
    assert events.MOCK_PUBLISHED == []


def test_same_problem_is_sent_once() -> None:
    rows = [_incident(1, tags=PAYMENT)]
    assert len(_run(rows)) == 1
    assert _run(rows) == []
    assert len(events.MOCK_PUBLISHED) == 1
    # 새 장애는 다시 보낸다. metric 태그가 없으면 기본 metric_code
    [env] = _run(rows + [_incident(2, tags=[{"tag": "component", "value": "payment"}])])
    assert env.data["metric_code"] == "zabbix_problem"


def test_without_url_the_watch_stays_off() -> None:
    assert dejafix_watch.base_url({}) is None
    assert dejafix_watch.start({}) is None
    assert dejafix_watch.base_url({"INFRA_DEJAFIX_URL": f"{URL}/"}) == URL
    assert dejafix_watch.interval_seconds({}) == 30
    assert dejafix_watch.interval_seconds({"INFRA_DEJAFIX_POLL_SECONDS": "1"}) == 5
    assert dejafix_watch.interval_seconds({"INFRA_DEJAFIX_POLL_SECONDS": "x"}) == 30


def test_chat_facts_show_dejafix_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRA_DEJAFIX_URL", raising=False)
    got = asyncio.run(facts.collect())
    assert got["dejafix_problems"] == {"configured": False, "problems": []}

    async def fake_fetch(url, client=None):
        assert url == URL
        return [_incident(1), _incident(2, tags=PAYMENT)]

    monkeypatch.setenv("INFRA_DEJAFIX_URL", URL)
    monkeypatch.setattr(dejafix_watch, "fetch_problems", fake_fetch)
    got = asyncio.run(facts.collect())["dejafix_problems"]
    assert got["configured"] is True and got["count"] == 2
    assert [p["component"] for p in got["problems"]] == [None, "payment"]
    assert got["problems"][0]["host"] == "shop-api-01"
    assert got["problems"][0]["severity"] == "High"


def test_chat_facts_mark_dejafix_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(url, client=None):
        raise httpx.ConnectError("down")

    monkeypatch.setenv("INFRA_DEJAFIX_URL", URL)
    monkeypatch.setattr(dejafix_watch, "fetch_problems", boom)
    got = asyncio.run(facts.collect())
    assert "dejafix_problems" in got["unavailable"] and "dejafix_problems" not in got
