"""회사 캐릭터 올리기 종류 (11/24): AI 가상 인물은 확인 체크가 있어야 올린다 (없으면 422)."""

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


S = _load("character_sheet_route_helpers_kind", "test_character_sheet_route.py")
URL, IMAGE = S.URL, S.IMAGE
fake, client = S.fake, S.client


def test_virtual_human_without_the_check_is_422(client: TestClient, fake: Any) -> None:
    res = client.post(URL, json={"image": IMAGE, "kind": "virtual_human"})
    assert res.status_code == 422
    assert fake.calls == []  # 마케팅을 부르지 않는다


def test_virtual_human_with_the_check_goes_as_input(client: TestClient, fake: Any) -> None:
    body = {"image": IMAGE, "kind": "virtual_human", "virtual_confirmed": True}
    assert client.post(URL, json=body).status_code == 200
    request = fake.calls[0][1]
    assert request.input == {"kind": "virtual_human", "virtual_confirmed": True}
    assert request.untrusted == {"image": IMAGE}  # 이미지는 그대로 untrusted로만


def test_illustration_needs_no_virtual_check(client: TestClient, fake: Any) -> None:
    assert client.post(URL, json={"image": IMAGE, "kind": "illustration"}).status_code == 200
    assert fake.calls[0][1].input == {"kind": "illustration", "virtual_confirmed": False}


def test_unknown_kind_is_422(client: TestClient, fake: Any) -> None:
    assert client.post(URL, json={"image": IMAGE, "kind": "celebrity"}).status_code == 422
    assert fake.calls == []
