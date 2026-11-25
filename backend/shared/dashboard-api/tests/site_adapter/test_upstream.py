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


def test_second_read_uses_cache(connected: m.SiteMapping, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(
        monkeypatch, lambda req: httpx.Response(200, json={"data": [], "next_cursor": None})
    )
    asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    assert len(seen) == 1


def test_disabled_connection_is_not_configured(
    connected: m.SiteMapping, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "off.json"
    path.write_text(
        json.dumps(
            {
                "shop_api_url": SITE,
                "enabled": False,
                "kind": "adapter",
                "adapter": {"base_url": SITE},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    with pytest.raises(up.UpstreamError) as err:
        asyncio.run(up.fetch_all(connected, connected.resources["orders"]))
    assert err.value.code == "NOT_CONFIGURED"


@pytest.mark.parametrize(
    "bad", ["@evil.host/x", "//evil.host/x", "api/v1", "/x?y=1", "https://evil/x"]
)
def test_mapping_path_must_be_local(adapter_dir: Path, bad: str) -> None:
    raw = json.loads((adapter_dir / "mapping.json").read_text(encoding="utf-8"))
    raw["resources"]["orders"]["path"] = bad
    (adapter_dir / "mapping.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(m.MappingError):
        m.load_mapping()


def test_openapi_found_under_api_v1(monkeypatch) -> None:
    """루트에 없고 `/api/v1/openapi.json`에 있는 사이트(스터디온)도 목록 경로를 찾는다 (11/26)."""
    import httpx as _httpx

    from ax_dashboard_api.site_adapter import upstream as _up

    seen: list[str] = []

    def handle(request: _httpx.Request) -> _httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/v1/openapi.json":
            return _httpx.Response(
                200, json={"paths": {"/api/v1/orders": {"get": {}}, "/api/v1/courses": {"get": {}}}}
            )
        return _httpx.Response(404, json={"error": "nope"})

    monkeypatch.setattr(_up, "TRANSPORT", _httpx.MockTransport(handle))
    paths, _writes = asyncio.run(_up.get_all_paths("http://10.0.0.9:3200", {}))
    assert paths == ["/api/v1/courses", "/api/v1/orders"]
    assert seen[0] == "/openapi.json" and "/api/v1/openapi.json" in seen


def test_openapi_stops_when_site_unreachable(monkeypatch) -> None:
    import httpx as _httpx

    from ax_dashboard_api.site_adapter import upstream as _up

    calls: list[str] = []

    def handle(request: _httpx.Request) -> _httpx.Response:
        calls.append(request.url.path)
        raise _httpx.ConnectError("down")

    monkeypatch.setattr(_up, "TRANSPORT", _httpx.MockTransport(handle))
    paths, _writes = asyncio.run(_up.get_all_paths("http://10.0.0.9:3200", {}))
    assert paths == [] and len(calls) == 1
