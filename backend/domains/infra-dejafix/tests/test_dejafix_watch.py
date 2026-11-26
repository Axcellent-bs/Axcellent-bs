"""DejaFix 현재 장애 → 총괄 `infra.anomaly_detected` (`dejafix_watch`), 대화 사실의 DejaFix 장애.

`component` 태그가 붙은 장애만 보내고(태그 없는 PC·호스트 장애는 DejaFix만 맡음),
같은 장애는 한 번만 보낸다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from ax_agent_template import company_scope, events, tool_connections
from ax_contracts.schemas import EventEnvelope, EventType
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
    """요청 회사는 캣북스(DejaFix는 회사 없이 부르지 않는다, 11/26). 회사 없는 길은 따로 본다."""
    dejafix_watch.reset_published()
    with company_scope.using(company_scope.CATBOOKS):
        yield
    dejafix_watch.reset_published()


def _run(
    rows: list[dict],
    seen: list[str] | None = None,
    components: frozenset[str] | None = None,
):
    async def go():
        async with _client(rows, seen) as client:
            return await dejafix_watch.run_once(URL, NOW, client, components=components)

    return asyncio.run(go())


def test_fetch_sends_agent_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """dejafix-proxy가 호출자 토큰을 확인하므로 인프라 AI 토큰을 붙인다."""
    monkeypatch.setenv("INFRA_TOKEN", "tok-infra")
    auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth.append(request.headers.get("authorization"))
        auth.append(request.headers.get("x-ax-company"))
        return httpx.Response(200, json={"data": []})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await dejafix_watch.fetch_problems(URL, client)

    assert asyncio.run(go()) == []
    assert auth == ["Bearer tok-infra", "catbooks"]  # 요청 회사도 싣는다


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


MEMORY = [{"tag": "class", "value": "os"}, {"tag": "component", "value": "memory"}]


def test_only_chosen_components_are_sent() -> None:
    """Zabbix 기본 템플릿이 PC 트리거에 다는 `component=memory`는 고른 부위가 아니면 안 보낸다."""
    rows = [_incident(1, tags=MEMORY), _incident(2, tags=PAYMENT)]
    [env] = _run(rows, components=frozenset({"payment"}))
    assert env.data["component"] == "payment"
    assert events.MOCK_PUBLISHED == [env]
    # 걸러 낸 장애는 보낸 것으로 치지 않는다: 나중에 부위를 넓히면 그때 보낸다
    [later] = _run(rows, components=frozenset({"payment", "memory"}))
    assert later.data["component"] == "memory"


def test_without_a_component_list_every_tagged_problem_is_sent() -> None:
    rows = [_incident(1, tags=MEMORY), _incident(2, tags=PAYMENT)]
    sent = _run(rows)
    assert sorted(e.data["component"] for e in sent) == ["memory", "payment"]


def test_component_list_comes_from_env() -> None:
    assert dejafix_watch.allowed_components({}) is None
    assert dejafix_watch.allowed_components({"INFRA_DEJAFIX_COMPONENTS": " , "}) is None
    assert dejafix_watch.allowed_components(
        {"INFRA_DEJAFIX_COMPONENTS": "payment, checkout"}
    ) == frozenset({"payment", "checkout"})


def test_without_url_there_is_no_address() -> None:
    assert dejafix_watch.base_url({}) is None
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


@pytest.fixture
def vault(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from ax_agent_template import tool_connections as tc

    monkeypatch.setenv(tc.FILE_ENV, str(tmp_path / "tool-connections.json"))
    monkeypatch.delenv("INFRA_DEJAFIX_URL", raising=False)
    monkeypatch.delenv("AX_DEJAFIX_PROXY_URL", raising=False)
    tc._cache = None
    return tc


def _connect(tc) -> None:
    tc.upsert(
        "dejafix",
        "장애",
        {"url": "http://10.0.0.5", "username": "v"},
        {"password": "pw"},
        conn_id=None,
        by="t",
    )


def test_env_url_wins_over_vault(vault, monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(vault)
    monkeypatch.setenv("INFRA_DEJAFIX_URL", f"{URL}/")
    assert dejafix_watch.base_url() == URL


def test_watch_starts_when_vault_gets_dejafix(vault) -> None:
    """부팅 때 아무것도 없어도 감시는 떠 있고, 나중에 연결하면 재시작 없이 읽는다."""
    seen: list[str | None] = []

    async def scenario() -> None:
        task = dejafix_watch.start()
        await asyncio.sleep(0)
        assert not task.done()  # 주소가 없어도 켜져 있다
        seen.append(dejafix_watch.base_url())
        _connect(vault)
        seen.append(dejafix_watch.base_url())
        task.cancel()

    asyncio.run(scenario())
    assert seen == [None, "http://dejafix-proxy:8030"]


def test_run_forever_skips_round_without_url(caplog: pytest.LogCaptureFixture) -> None:
    """주소가 없으면 요청도 경고도 없이 건너뛰고 간격만큼 쉰다."""
    seen: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    async def run() -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(dejafix_watch.asyncio, "sleep", fake_sleep)
            mp.setattr(dejafix_watch, "run_once", _forbidden(seen))
            await dejafix_watch.run_forever(lambda _cid: None, interval=7)

    def _forbidden(calls: list[str]):
        async def boom(*args, **kwargs):
            calls.append("called")
            return []

        return boom

    with caplog.at_level("WARNING"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    assert seen == []
    assert sleeps == [7, 7]
    assert not caplog.records


# ---------------------------------------------------------------- 복구 신호 infra.recovered (11/23)

RESOLVED_AT = "2026-11-09T05:03:20Z"


def _rounds(
    lists: list[list[dict] | None], detail: dict | None = None, detail_status: int = 200
) -> list:
    """회차마다 목록을 바꿔 가며 run_once. 목록이 None이면 그 회차 조회가 500으로 실패한다."""
    detail_calls: list[str] = []
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/incidents/"):
            detail_calls.append(request.url.path)
            if detail_status != 200:
                return httpx.Response(detail_status, json={"error": "x"})
            return httpx.Response(200, json={"data": detail})
        rows = lists[state["i"]]
        if rows is None:
            return httpx.Response(500, json={"error": "down"})
        return httpx.Response(200, json={"data": rows, "meta": {"next_cursor": None}})

    async def go():
        out = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for i in range(len(lists)):
                state["i"] = i
                try:
                    out.append(await dejafix_watch.run_once(URL, NOW, client))
                except httpx.HTTPStatusError:
                    out.append("failed")
        return out

    return asyncio.run(go()), detail_calls


def _resolved_detail(id_: int = 7) -> dict:
    row = _incident(id_, tags=PAYMENT, status="resolved")
    row["resolved_at"] = RESOLVED_AT
    return row


def test_recovered_after_two_successful_misses_with_dejafix_resolved_time() -> None:
    open_ = _incident(7, tags=PAYMENT)
    rounds, details = _rounds([[open_], [], []], detail=_resolved_detail())
    assert [len(r) for r in rounds] == [1, 0, 1]  # 첫 번째로 빠진 회차는 아직 안 보냄
    assert details == ["/api/incidents/7"]
    rec = rounds[2][0]
    assert rec.type == EventType.INFRA_RECOVERED
    assert rec.idempotency_key == "infra.recovered:dejafix:7"
    assert rec.data["component"] == "payment" and rec.data["metric_code"] == "payment_http_fail"
    assert rec.data["source_ref"] == rounds[0][0].data["source_ref"] == "zabbix:event:97"
    # 감시 시각(NOW)이 아니라 DejaFix 해소 시각
    assert rec.data["recovered_at"] == "2026-11-09T05:03:20Z"
    assert rec.data["recovery"] is None
    assert [e.type for e in events.MOCK_PUBLISHED] == [
        EventType.INFRA_ANOMALY_DETECTED,
        EventType.INFRA_RECOVERED,
    ]


def test_recovered_only_once() -> None:
    rounds, details = _rounds([[_incident(7, tags=PAYMENT)], [], [], [], []], _resolved_detail())
    assert [len(r) for r in rounds] == [1, 0, 1, 0, 0]
    assert len(details) == 1


def test_failed_poll_does_not_count_as_missing() -> None:
    """조회 실패(500)는 빠진 것으로 세지 않는다: 실패·빠짐·실패면 아직 한 번만 빠진 것."""
    rounds, details = _rounds([[_incident(7, tags=PAYMENT)], None, [], None], _resolved_detail())
    assert rounds[1] == "failed" and rounds[3] == "failed"
    assert rounds[2] == [] and details == []


def test_reappearing_resets_the_count() -> None:
    inc = _incident(7, tags=PAYMENT)
    rounds, details = _rounds([[inc], [], [inc], []], _resolved_detail())
    assert all(r == [] for r in rounds[1:]) and details == []


def test_detail_still_problem_is_not_recovery() -> None:
    """목록에서만 빠지고(예: limit) 상세는 아직 problem이면 복구로 보지 않는다."""
    still = _incident(7, tags=PAYMENT)
    rounds, details = _rounds([[still], [], []], detail=still)
    assert rounds[2] == [] and details == ["/api/incidents/7"]


def test_detail_error_waits_for_next_round() -> None:
    rounds, details = _rounds([[_incident(7, tags=PAYMENT)], [], [], []], detail_status=502)
    assert all(r == [] for r in rounds[1:])
    assert details == ["/api/incidents/7", "/api/incidents/7"]  # 다음 회차에 다시 확인


def test_untagged_incident_never_gets_recovery() -> None:
    rounds, details = _rounds([[_incident(8)], [], []], _resolved_detail(8))
    assert all(r == [] for r in rounds) and details == []


# ---------------------------------------------------------------- 회사별 (11/26)

NUBI = "c-cf2f0d"


def _company_client(per_company: dict[str, list[dict] | None], calls: list[str | None]):
    """회사(`X-AX-Company`)마다 다른 목록. None이면 그 회사 Zabbix 연결 전(프록시 503)."""

    def handler(request: httpx.Request) -> httpx.Response:
        company = request.headers.get("X-AX-Company")
        calls.append(company)
        rows = per_company.get(company or "")
        if rows is None:
            return httpx.Response(
                503, json={"error": {"code": "DEJAFIX_NOT_CONNECTED", "message": "연결 전"}}
            )
        return httpx.Response(200, json={"data": rows, "meta": {"next_cursor": None}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_no_company_does_not_call_dejafix(monkeypatch: pytest.MonkeyPatch) -> None:
    """요청 밖이고 회사를 주지 않으면 부르지 않는다(캣북스로 떨어지지 않는다)."""
    monkeypatch.setenv("INFRA_TOKEN", "tok-infra")
    calls: list[str | None] = []

    async def go():
        async with _company_client({"catbooks": [_incident(1, tags=PAYMENT)]}, calls) as client:
            token = company_scope._ACTIVE.set(None)  # 요청 밖(스스로 도는 루틴)
            try:
                with pytest.raises(tool_connections.DejafixNoCompany):
                    await dejafix_watch.fetch_problems(URL, client)
                with pytest.raises(tool_connections.DejafixNoCompany):
                    await dejafix_watch.run_once(URL, NOW, client)
            finally:
                company_scope._ACTIVE.reset(token)

    asyncio.run(go())
    assert calls == []


def test_each_company_is_read_with_its_own_company(monkeypatch: pytest.MonkeyPatch) -> None:
    """회사마다 그 회사로 부르고, 그 회사 장애는 그 회사(tenant_id)로 총괄에 알린다."""
    monkeypatch.setenv("INFRA_TOKEN", "tok-infra")
    calls: list[str | None] = []
    rows = {"catbooks": [_incident(1, tags=PAYMENT)], NUBI: [_incident(1, tags=PAYMENT)]}

    async def go():
        async with _company_client(rows, calls) as client:
            cat = await dejafix_watch.run_once(URL, NOW, client, company="catbooks")
            nubi = await dejafix_watch.run_once(URL, NOW, client, company=NUBI)
            return cat, nubi

    cat, nubi = asyncio.run(go())
    assert calls == ["catbooks", NUBI]
    assert [e.idempotency_key for e in cat] == ["infra.anomaly_detected:dejafix:1"]  # 예전 그대로
    assert cat[0].tenant_id == EventEnvelope.model_fields["tenant_id"].default
    assert [e.idempotency_key for e in nubi] == [f"infra.anomaly_detected:dejafix:{NUBI}:1"]
    assert nubi[0].tenant_id == NUBI  # 같은 장애 id라도 회사가 다르면 따로 알린다


def test_run_forever_skips_company_without_zabbix(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Zabbix 연결 전인 회사는 그 회사만 조용히 건너뛰고, 다른 회사는 읽는다."""
    monkeypatch.setenv("INFRA_TOKEN", "tok-infra")
    calls: list[str | None] = []
    sent: list[str] = []
    real_run_once = dejafix_watch.run_once

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    async def go():
        async with _company_client({"catbooks": [_incident(1, tags=PAYMENT)]}, calls) as client:

            async def run_once(url, now, client_=None, components=None, company=None):
                out = await real_run_once(url, now, client, components=components, company=company)
                sent.extend(e.idempotency_key for e in out)
                return out

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(dejafix_watch.asyncio, "sleep", fake_sleep)
                mp.setattr(dejafix_watch, "run_once", run_once)
                await dejafix_watch.run_forever(
                    lambda _cid: URL, interval=7, companies=lambda: [NUBI, "catbooks"]
                )

    with caplog.at_level("WARNING"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(go())
    assert calls == [NUBI, "catbooks"]
    assert sent == ["infra.anomaly_detected:dejafix:1"]
    assert not caplog.records  # 연결 전은 경고가 아니다


def test_chat_facts_company_without_zabbix_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대화 사실도 그 회사 것만: 연결 전이면 연결 전(캣북스 장애로 채우지 않는다)."""
    monkeypatch.setenv("INFRA_TOKEN", "tok-infra")
    monkeypatch.setenv("INFRA_DEJAFIX_URL", URL)
    seen: list[str | None] = []

    async def fake_fetch(url, client=None, company=None):
        seen.append(company_scope.active())
        raise dejafix_watch.DejafixNotConnected("연결 전")

    monkeypatch.setattr(dejafix_watch, "fetch_problems", fake_fetch)
    with company_scope.using(NUBI):
        got = asyncio.run(facts.collect())["dejafix_problems"]
    assert got == {"configured": False, "problems": []}
    assert seen == [NUBI]
