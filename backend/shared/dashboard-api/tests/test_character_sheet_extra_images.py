"""캐릭터 올리기 참조 여러 장 (11/25): 가상 인물·일러스트만 추가 최대 3장."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


S = _load("character_sheet_route_helpers_extra", "test_character_sheet_route.py")
URL, IMAGE = S.URL, S.IMAGE
fake, client = S.fake, S.client
EXTRA = IMAGE | {"label": "side"}


def _body(kind: str, extras: list[dict[str, Any]]) -> dict[str, Any]:
    return {"image": IMAGE, "kind": kind, "virtual_confirmed": True, "extra_images": extras}


def test_three_extras_go_to_marketing_as_untrusted(client: TestClient, fake: Any) -> None:
    extras = [EXTRA | {"label": "full"}, EXTRA, EXTRA | {"label": "face"}]
    assert client.post(URL, json=_body("virtual_human", extras)).status_code == 200
    request = fake.calls[0][1]
    assert request.untrusted["image"] == IMAGE
    assert [e["label"] for e in request.untrusted["extra_images"]] == ["full", "side", "face"]
    assert request.input == {"kind": "virtual_human", "virtual_confirmed": True}


def test_fourth_extra_is_422(client: TestClient, fake: Any) -> None:
    res = client.post(URL, json=_body("illustration", [EXTRA] * 4))
    assert res.status_code == 422 and fake.calls == []


def test_real_photo_with_extras_is_422(client: TestClient, fake: Any) -> None:
    res = client.post(URL, json={"image": IMAGE, "extra_images": [EXTRA]})
    assert res.status_code == 422 and fake.calls == []


def test_unknown_label_is_422(client: TestClient, fake: Any) -> None:
    res = client.post(URL, json=_body("virtual_human", [EXTRA | {"label": "back"}]))
    assert res.status_code == 422 and fake.calls == []


def test_without_extras_the_request_is_as_before(client: TestClient, fake: Any) -> None:
    assert client.post(URL, json={"image": IMAGE}).status_code == 200
    assert fake.calls[0][1].untrusted == {"image": IMAGE}
    assert fake.calls[0][1].input == {}
