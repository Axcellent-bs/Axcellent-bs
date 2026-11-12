"""'재무 AI 테스트' 버튼(자체 점검) 테스트."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from finance_agent import handlers, selftest
from finance_agent.main import app
from finance_agent.sources import MockSources

REGISTRY = {
    "actions": [
        {"action": "issue_coupon", "base_grade": "A", "verifier": None},
        {"action": "refund_request", "base_grade": "A", "verifier": None},
        {"action": "confirm_settlement", "base_grade": "C", "verifier": None},
    ]
}
ROUTING = "정산 확정은 사람 승인"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


def _network(up: set[str], registry: dict | None = None):
    """가짜 네트워크. `up`에 있는 서비스만 응답한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host not in up:
            raise httpx.ConnectError("down")
        if path == "/api/safety/registry":
            return httpx.Response(200, json={"data": registry or REGISTRY, "meta": {}})
        return httpx.Response(200, json={"data": {"status": "ok"}, "meta": {}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _run(client: httpx.AsyncClient) -> dict[str, Any]:
    return await selftest.run_self_test(http_client=client)


@pytest.mark.asyncio
async def test_all_green_when_everything_is_up() -> None:
    result = await _run(_network({"safety", "shop-api", "data"}))
    assert result["result"] == "pass", [c for c in result["checks"] if not c["ok"]]
    assert result["passed"] == result["total"] == 7
    names = {c["name"] for c in result["checks"]}
    assert "안전 장치 연결" in names
    # 쿠폰·환불 판단과 예산·한도는 재무 일이 아니라 점검하지 않는다
    assert not {n for n in names if "쿠폰" in n or "환불" in n or "한도" in n or "예산" in n}
    assert {
        "결산 계산 공식",
        "대사 상태 나누기",
        "무작위 장부 계산 점검",
    } <= names
    # 판매자 정산 공식은 고객지원이 계산해 고객지원 쪽에서 점검한다. 재무는 정산 확정 등급만 본다
    assert "판매자 정산 공식" not in names and ROUTING in names


@pytest.mark.asyncio
async def test_missing_data_sources_only_warn() -> None:
    result = await _run(_network({"safety"}))
    assert result["result"] == "warn"
    bad = {c["name"]: c["level"] for c in result["checks"] if not c["ok"]}
    assert bad == {"쇼핑몰 연결": "warn", "데이터 AI 연결": "warn"}


@pytest.mark.asyncio
async def test_safety_down_fails() -> None:
    result = await _run(_network({"shop-api", "data"}))
    assert result["result"] == "fail"
    failed = {c["name"] for c in result["checks"] if not c["ok"] and c["level"] == "fail"}
    assert {"안전 장치 연결", ROUTING} <= failed


@pytest.mark.asyncio
async def test_old_settlement_grade_or_missing_action_fails() -> None:
    """정산 확정이 C가 아니거나 등록되어 있지 않으면 빨간불."""
    settle = {"action": "confirm_settlement", "base_grade": "A"}
    old = {"actions": [*REGISTRY["actions"][:2], settle]}
    result = await _run(_network({"safety", "shop-api", "data"}, old))
    by = {c["name"]: c for c in result["checks"]}
    assert (by[ROUTING]["ok"], by[ROUTING]["level"]) == (False, "fail")
    assert result["result"] == "fail"

    no_settle = {"actions": REGISTRY["actions"][:2]}
    result = await _run(_network({"safety", "shop-api", "data"}, no_settle))
    assert not {c["name"]: c for c in result["checks"]}[ROUTING]["ok"]


@pytest.mark.asyncio
async def test_module_checks_cover_every_new_module() -> None:
    """결산·대사 규칙 점검이 모두 통과한다(가짜 자료, 실제 기록 없음)."""
    result = await _run(_network({"safety", "shop-api", "data"}))
    by = {c["name"]: c for c in result["checks"]}
    for name in (
        "결산 계산 공식",
        "대사 상태 나누기",
    ):
        assert by[name]["ok"], (name, by[name]["detail"])


@pytest.mark.asyncio
async def test_broken_calculation_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """계산 코드가 고장 나면 테스트가 빨간불을 켠다."""

    def broken(*args: Any, **kwargs: Any) -> tuple[str, None]:
        return ("matched", None)  # 무엇이든 일치로 나누는 고장

    monkeypatch.setattr(selftest, "classify", broken)
    result = await _run(_network({"safety", "shop-api", "data"}))
    failed = {c["name"] for c in result["checks"] if not c["ok"]}
    assert "대사 상태 나누기" in failed
    assert result["result"] == "fail"


@pytest.mark.asyncio
async def test_self_test_leaves_no_trace_in_real_records() -> None:
    await _run(_network({"safety", "shop-api", "data"}))
    ledger, records = handlers.state().ledger, handlers.state().records
    assert records.closings == {} and records.legacy_batches == {} and ledger.refunds == {}


def test_endpoints_run_and_remember_last(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    async def fake_run(*args: Any, **kwargs: Any) -> dict:
        return {
            "result": "pass",
            "summary": "ok",
            "checks": [],
            "passed": 0,
            "total": 0,
            "duration_ms": 1,
            "ran_at": datetime.now(UTC).isoformat(),
        }

    monkeypatch.setattr("finance_agent.main.run_self_test", fake_run)
    client = TestClient(app)
    assert client.get("/api/finance/self-test/last").json()["data"] is None
    ran = client.get("/api/finance/self-test", headers={"Origin": "http://localhost:3001"})
    assert ran.json()["data"]["result"] == "pass"
    assert ran.headers.get("access-control-allow-origin") == "http://localhost:3001"
    assert client.get("/api/finance/self-test/last").json()["data"]["result"] == "pass"


def test_last_result_expires_after_12_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """아침마다 새로 테스트하도록 12시간이 지난 결과는 지운다."""
    from datetime import UTC, datetime, timedelta

    client = TestClient(app)
    old = (datetime.now(UTC) - timedelta(hours=12, minutes=1)).isoformat()
    handlers.state().last_self_test = {"result": "pass", "ran_at": old}
    body = client.get("/api/finance/self-test/last").json()
    assert body["data"] is None and body["meta"]["ttl_hours"] == 12
    assert handlers.state().last_self_test is None

    fresh = (datetime.now(UTC) - timedelta(hours=11)).isoformat()
    handlers.state().last_self_test = {"result": "fail", "ran_at": fresh}
    assert client.get("/api/finance/self-test/last").json()["data"]["result"] == "fail"


@pytest.mark.parametrize("bad", [{}, {"ran_at": "어제"}, {"ran_at": None}])
def test_strange_timestamps_count_as_old(bad: dict) -> None:
    assert selftest.is_fresh(bad) is False


def _fake_result(result: str = "pass") -> dict[str, Any]:
    from datetime import UTC, datetime

    return {
        "result": result,
        "summary": "ok",
        "checks": [],
        "passed": 0,
        "total": 0,
        "duration_ms": 1,
        "ran_at": datetime.now(UTC).isoformat(),
    }


def test_manual_run_is_marked_manual_and_meta_shows_auto_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(*args: Any, **kwargs: Any) -> dict:
        return _fake_result()

    monkeypatch.setattr("finance_agent.main.run_self_test", fake_run)
    client = TestClient(app)
    assert client.get("/api/finance/self-test").json()["data"]["trigger"] == "manual"
    body = client.get("/api/finance/self-test/last").json()
    assert body["data"]["trigger"] == "manual"
    assert body["meta"] == {"ttl_hours": 12, "interval_seconds": 0, "next_run_at": None}


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 600), ("", 600), ("0", 0), ("30", 30), ("-5", 0), ("x", 600)]
)
def test_self_test_interval_env(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int):
    from finance_agent import main

    if raw is None:
        monkeypatch.delenv("FINANCE_SELF_TEST_SECONDS", raising=False)
    else:
        monkeypatch.setenv("FINANCE_SELF_TEST_SECONDS", raw)
    assert main.self_test_seconds() == expected


@pytest.mark.asyncio
async def test_loop_runs_repeatedly_and_survives_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 번 실패해도 루프는 계속 돌고, 결과는 trigger=auto로 남는다 (실제 시간을 기다리지 않음)."""
    import asyncio

    from finance_agent import main

    calls: list[int] = []
    done = asyncio.Event()

    async def flaky_run(*args: Any, **kwargs: Any) -> dict:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        if len(calls) >= 3:
            done.set()
        return _fake_result()

    monkeypatch.setattr(main, "run_self_test", flaky_run)
    task = asyncio.create_task(main.self_test_loop(0, first_delay=0))
    await asyncio.wait_for(done.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 3
    assert handlers.state().last_self_test["trigger"] == "auto"
    assert main.SELF_TEST_SCHEDULE["next_run_at"] is not None


def test_lifespan_starts_auto_test_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import time
    from datetime import datetime

    from finance_agent import main

    async def fake_run(*args: Any, **kwargs: Any) -> dict:
        return _fake_result("warn")

    monkeypatch.setattr(main, "run_self_test", fake_run)
    monkeypatch.setattr(main, "SELF_TEST_FIRST_DELAY", 0)
    monkeypatch.setenv("FINANCE_SELF_TEST_SECONDS", "600")
    with TestClient(app) as client:
        body: dict = {}
        for _ in range(200):
            body = client.get("/api/finance/self-test/last").json()
            if body["data"]:
                break
            time.sleep(0.01)
        assert body["data"]["result"] == "warn" and body["data"]["trigger"] == "auto"
        assert body["meta"]["interval_seconds"] == 600
        next_run = datetime.fromisoformat(body["meta"]["next_run_at"])
        assert next_run.tzinfo is not None
    assert main.SELF_TEST_SCHEDULE == {"interval_seconds": 0, "next_run_at": None}


def test_lifespan_does_not_start_auto_test_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import main

    async def fake_run(*args: Any, **kwargs: Any) -> dict:
        raise AssertionError("자동 테스트가 꺼져 있어야 한다")

    monkeypatch.setattr(main, "run_self_test", fake_run)
    monkeypatch.setattr(main, "SELF_TEST_FIRST_DELAY", 0)
    monkeypatch.setenv("FINANCE_SELF_TEST_SECONDS", "0")
    with TestClient(app) as client:
        meta = client.get("/api/finance/self-test/last").json()["meta"]
        assert (meta["interval_seconds"], meta["next_run_at"]) == (0, None)
        assert handlers.state().last_self_test is None


@pytest.mark.asyncio
async def test_random_books_check_passes_and_catches_rounding_bugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """무작위 장부 60건 점검: 정책대로면 통과, 계산이 틀어지면(예: 법인세 1원) 그 영역을 짚는다."""
    from finance_agent import accuracy

    result = await _run(_network({"safety", "shop-api", "data"}))
    by = {c["name"]: c for c in result["checks"]}
    assert by["무작위 장부 계산 점검"]["ok"], by["무작위 장부 계산 점검"]["detail"]
    assert "60건 모두" in by["무작위 장부 계산 점검"]["detail"]

    real = accuracy.income_tax_amount
    monkeypatch.setattr(accuracy, "income_tax_amount", lambda base: real(base) + (base > 0))
    result = await _run(_network({"safety", "shop-api", "data"}))
    check = next(c for c in result["checks"] if c["name"] == "무작위 장부 계산 점검")
    assert not check["ok"] and "법인세 누진 계산" in check["detail"]
    assert result["result"] == "fail"
