import httpx
import pytest

from ax_orchestrator.real import http_card_fetcher
from ax_orchestrator.registry import Registry


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetches_card_from_well_known_path() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"name": "data"})

    async with _client(handler) as client:
        card = await http_card_fetcher(client)("data")
    assert card == {"name": "data"}
    assert seen[0].endswith("/.well-known/agent-card.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 500])
async def test_non_200_is_none(status) -> None:
    async with _client(lambda r: httpx.Response(status)) as client:
        assert await http_card_fetcher(client)("data") is None


@pytest.mark.asyncio
async def test_connection_error_and_bad_json_are_none() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with _client(refuse) as client:
        assert await http_card_fetcher(client)("data") is None
    async with _client(lambda r: httpx.Response(200, text="not json")) as client:
        assert await http_card_fetcher(client)("data") is None


@pytest.mark.asyncio
async def test_unreachable_dept_becomes_unavailable_in_registry() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    registry = Registry()
    async with _client(refuse) as client:
        await registry.load(["data"], http_card_fetcher(client))
    assert "data" in registry.unavailable


@pytest.mark.asyncio
async def test_registry_real_endpoint_reports_unreachable_depts(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from ax_orchestrator import real
    from ax_orchestrator.main import app

    async def fetch_none(dept):
        return None

    monkeypatch.setattr(real, "http_card_fetcher", lambda client=None: fetch_none)
    with TestClient(app) as client:
        data = client.get("/api/registry/real").json()
    assert data["meta"]["mock"] is False
    assert set(data["data"]["unavailable"]) >= {"data", "hr"}


@pytest.mark.asyncio
async def test_merged_department_card_keeps_one_entry_per_skill() -> None:
    """부서가 서비스 둘로 나뉘고 같은 skill id가 둘 다에 있어도 부서 카드에는 하나만 둔다."""
    cards = iter(
        [
            {
                "name": "handover",
                "skills": [{"id": "get_roster"}, {"id": "answer_planning_question"}],
            },
            {
                "name": "decisions",
                "skills": [{"id": "record_case"}, {"id": "answer_planning_question"}],
            },
        ]
    )
    async with _client(lambda request: httpx.Response(200, json=next(cards))) as client:
        card = await http_card_fetcher(client)("hr")
    ids = [s["id"] for s in card["skills"]]
    assert ids.count("answer_planning_question") == 1
    assert set(ids) == {"get_roster", "record_case", "answer_planning_question"}
    assert card["name"] == "hr"
