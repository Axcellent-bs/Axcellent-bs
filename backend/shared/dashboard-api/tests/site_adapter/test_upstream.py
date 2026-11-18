"""고객사 API 호출: 인증 헤더, 커서로 끝까지 읽기, 최대 쪽 수, 오류 바꾸기."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template import company_connection as cc
from ax_dashboard_api.site_adapter import mapping as m
from ax_dashboard_api.site_adapter import upstream as up

SITE = "http://10.0.0.9:3000"


@pytest.fixture
def connected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_dir: Path) -> m.SiteMapping:
    path = tmp_path / "company_connection.json"
    path.write_text(
        json.dumps({"shop_api_url": SITE, "kind": "adapter", "adapter": {"base_url": SITE}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(up.TOKEN_ENV, "tok")
    found = m.load_mapping()
    assert found is not None
    return found


def _serve(monkeypatch: pytest.MonkeyPatch, handle: Any) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def wrap(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handle(request)

    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(wrap))
    return seen


def test_reads_two_pages(
    connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch, nubi: Any
) -> None:
    pages = {None: nubi("nubi_bookings_p1.json"), "MjAy": nubi("nubi_bookings_p2.json")}

    def handle(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json=pages[cursor[:4] if cursor else None])

    seen = _serve(monkeypatch, handle)
    rows, truncated = asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    assert [r["code"] for r in rows] == ["NB-20261110-0001", "NB-20261111-0002", "NB-20261115-0003"]
    assert truncated is False
    assert len(seen) == 2 and seen[1].url.params["cursor"].startswith("MjAy")
    assert str(seen[0].url).startswith(SITE + "/api/v1/bookings")


def test_sends_since_and_token(connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(
        monkeypatch, lambda req: httpx.Response(200, json={"data": [], "next_cursor": None})
    )
    asyncio.run(
        up.fetch_all(connected, connected.resources["orders"], since="2026-11-01T00:00:00Z")
    )
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert seen[0].url.params["updated_since"] == "2026-11-01T00:00:00Z"
    assert seen[0].url.params["limit"] == "200"


def test_no_since_param_for_resource_without_it(
    connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _serve(
        monkeypatch, lambda req: httpx.Response(200, json={"data": [], "next_cursor": None})
    )
    asyncio.run(
        up.fetch_all(connected, connected.resources["products"], since="2026-11-01T00:00:00Z")
    )
    assert "updated_since" not in seen[0].url.params


def test_stops_at_max_pages(connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(up.MAX_PAGES_ENV, "3")
    seen = _serve(
        monkeypatch,
        lambda req: httpx.Response(200, json={"data": [{"code": "x"}], "next_cursor": "c"}),
    )
    rows, truncated = asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    assert len(seen) == 3 and len(rows) == 3 and truncated is True


def test_auth_error_hides_token(connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda req: httpx.Response(401, json={"error": "bad key tok"}))
    with pytest.raises(up.UpstreamError) as err:
        asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    assert (err.value.status, err.value.code) == (503, "UPSTREAM_AUTH")
    assert "tok" not in str(err.value)


def test_server_error_is_unavailable(
    connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, lambda req: httpx.Response(500))
    with pytest.raises(up.UpstreamError) as err:
        asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    assert (err.value.status, err.value.code) == (503, "UPSTREAM_UNAVAILABLE")


def test_connect_error_is_unavailable(
    connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    _serve(monkeypatch, boom)
    with pytest.raises(up.UpstreamError) as err:
        asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    assert err.value.code == "UPSTREAM_UNAVAILABLE"


def test_missing_items_field_is_mapping_broken(
    connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, lambda req: httpx.Response(200, json={"rows": []}))
    with pytest.raises(up.UpstreamError) as err:
        asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    assert (err.value.status, err.value.code) == (502, "MAPPING_BROKEN")


def test_not_adapter_connection_is_not_configured(
    adapter_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "none.json"))
    monkeypatch.setattr(cc, "_cache", None)
    found = m.load_mapping()
    assert found is not None
    with pytest.raises(up.UpstreamError) as err:
        asyncio.run(up.fetch_all(found, found.resources["orders"]))
    assert (err.value.status, err.value.code) == (503, "NOT_CONFIGURED")


def test_ping(connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda req: httpx.Response(200, json={}))
    assert asyncio.run(up.ping()) is True
    _serve(monkeypatch, lambda req: httpx.Response(503))
    assert asyncio.run(up.ping()) is False
