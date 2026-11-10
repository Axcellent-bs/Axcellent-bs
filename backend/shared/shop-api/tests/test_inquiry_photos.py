"""문의 접수에 영수증 사진(images)을 붙이면 검사해서 고객지원에 untrusted로만 넘긴다."""

import logging

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_shop_api import inquiries, support_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app
from ax_shop_api.service import ShopError

PHOTO = {"ref": "img_1", "media_type": "image/jpeg", "data_base64": "QUJDREVGRw=="}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


class FakeSupport:
    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        output = {
            "id": 11,
            "status": "open",
            "order_id": request.input["order_id"],
            "updated_at": "2026-10-27T00:00:00Z",
        }
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output=output)


@pytest.fixture
def support(monkeypatch: pytest.MonkeyPatch) -> FakeSupport:
    fake = FakeSupport()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _post(client: TestClient, images: list | None, body: str = "영수증 확인해 주세요"):  # noqa: ANN202
    made = client.post(
        "/api/shop/orders",
        json={"buyer_id": "buy_0001", "items": [{"product_id": "prd_0001", "qty": 1}]},
    )
    payload = {"buyer_id": "buy_0001", "order_id": made.json()["data"]["order_id"], "body": body}
    if images is not None:
        payload["images"] = images
    return client.post("/api/shop/inquiries", json=payload)


def test_photos_are_passed_to_support_as_untrusted(
    client: TestClient, support: FakeSupport
) -> None:
    assert _post(client, [PHOTO]).status_code == 201
    sent = support.calls[0]
    assert sent.untrusted["images"] == [PHOTO]
    assert "images" not in sent.input  # 신뢰 입력에 섞지 않는다


def test_no_photos_keeps_the_old_request_shape(client: TestClient, support: FakeSupport) -> None:
    assert _post(client, None).status_code == 201
    assert "images" not in support.calls[0].untrusted


def test_more_than_four_photos_is_rejected(client: TestClient, support: FakeSupport) -> None:
    res = _post(client, [PHOTO] * 5)
    assert res.status_code == 400 and res.json()["error"]["code"] == "TOO_MANY_IMAGES"
    assert support.calls == []


def test_a_non_base64_photo_is_rejected_without_echoing_it(
    client: TestClient, support: FakeSupport
) -> None:
    res = _post(client, [{**PHOTO, "data_base64": "not base64 !!"}])
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVALID_IMAGE"
    assert "not base64" not in res.text and support.calls == []


def test_an_unknown_media_type_or_extra_field_is_rejected(client: TestClient) -> None:
    assert _post(client, [{**PHOTO, "media_type": "application/pdf"}]).status_code == 422
    assert _post(client, [{**PHOTO, "extra": 1}]).status_code == 422


def test_total_size_over_the_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inquiries, "MAX_IMAGES_TOTAL", 10)
    with pytest.raises(ShopError) as err:
        inquiries.clean_images([PHOTO, PHOTO])
    assert err.value.code == "IMAGES_TOO_LARGE"


def test_different_photos_make_a_different_dedup_key() -> None:
    other = {**PHOTO, "data_base64": "WFla"}
    base = inquiries.digest("buy_0001", "ord_1", "t", "m")
    assert inquiries.digest("buy_0001", "ord_1", "t", "m", images_key="") == base
    a = inquiries.digest("buy_0001", "ord_1", "t", "m", images_key=inquiries.images_key([PHOTO]))
    b = inquiries.digest("buy_0001", "ord_1", "t", "m", images_key=inquiries.images_key([other]))
    assert len({base, a, b}) == 3


def test_photo_content_is_never_logged(
    client: TestClient, support: FakeSupport, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        _post(client, [PHOTO])
        _post(client, [{**PHOTO, "data_base64": "bad !!"}])
    assert "QUJDREVGRw" not in caplog.text and "bad !!" not in caplog.text


# ---- 한 장 한도 (413, 사진 값은 오류에 싣지 않는다) -----------------------------------------
LIMIT = inquiries.MAX_IMAGE_BASE64


def _big(size: int) -> dict:
    return {"ref": "img_big", "media_type": "image/jpeg", "data_base64": "A" * size}


def test_한_장이_한도를_넘으면_413이고_응답에_사진이_없다(
    client: TestClient, support: FakeSupport
) -> None:
    res = _post(client, [_big(LIMIT + 1)])
    assert res.status_code == 413
    assert res.json() == {
        "error": {"code": "IMAGE_TOO_LARGE", "message": "사진 한 장의 크기가 너무 크다"}
    }
    assert len(res.text) < 500 and "AAAA" not in res.text
    assert support.calls == []


def test_한도_그대로는_접수된다(client: TestClient, support: FakeSupport) -> None:
    assert _post(client, [_big(LIMIT)]).status_code == 201


def test_422_응답에도_요청이_보낸_값이_실리지_않는다(
    client: TestClient, support: FakeSupport
) -> None:
    secret = "QUJDREVGRw=="  # ref가 없는 사진: 오류가 사진이 든 항목을 되돌려 주던 경우
    res = _post(
        client, [{"media_type": "image/jpeg", "data_base64": secret}], body="비밀 본문 내용"
    )
    assert res.status_code == 422
    assert secret not in res.text and "비밀 본문 내용" not in res.text
    detail = res.json()["detail"]
    assert detail and all("input" not in item for item in detail)  # 어느 필드가 틀렸는지는 남는다
    assert detail[0]["loc"][-1] == "ref"
    assert support.calls == []
