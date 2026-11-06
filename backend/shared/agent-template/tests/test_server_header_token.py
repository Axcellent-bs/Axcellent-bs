"""`/a2a` 토큰: 본문 우선, 본문이 비었거나 자리표시자("data-via-nginx")일 때만 Authorization 헤더.

251 같은 공유 서버는 화면 번들에 실제 토큰을 넣지 않는다. 화면은 본문에 자리표시자를 보내고,
nginx가 `Authorization: Bearer <토큰>`을 넣는다. 로컬은 지금처럼 본문에 실제 토큰.
로그에는 검증 경로(body·header)와 명부 이름만 남기고 토큰 값은 남기지 않는다.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ax_agent_template.server import PLACEHOLDER_TOKEN

ORCH = "tok-orch-7f3a9c"  # 시험용 가짜 토큰 (명부 orchestrator)
DASH = "tok-dash-2b81e4"  # 시험용 가짜 토큰 (명부 dashboard-api, echo skill 요청자 아님)


@pytest.fixture
def client(app: FastAPI, real_mode: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AX_AGENT_TOKENS", f"orchestrator={ORCH},dashboard-api={DASH}")
    return TestClient(app)


def _post(client: TestClient, body: dict, header: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {header}"} if header is not None else {}
    return client.post("/a2a", json=body, headers=headers).json()


def test_placeholder_is_the_agreed_value() -> None:
    assert PLACEHOLDER_TOKEN == "data-via-nginx"


def test_real_body_token_passes(client, request_body) -> None:
    result = _post(client, request_body(token=ORCH))
    assert result["status"] == "completed" and result["meta"]["requester"] == "orchestrator"


def test_real_body_token_wins_over_a_different_header(client, request_body) -> None:
    result = _post(client, request_body(token=ORCH), header=DASH)
    assert result["status"] == "completed" and result["meta"]["requester"] == "orchestrator"


def test_placeholder_with_the_right_header_passes(client, request_body) -> None:
    result = _post(client, request_body(token=PLACEHOLDER_TOKEN), header=ORCH)
    assert result["status"] == "completed" and result["meta"]["requester"] == "orchestrator"


def test_placeholder_with_a_wrong_header_is_rejected(client, request_body) -> None:
    result = _post(client, request_body(token=PLACEHOLDER_TOKEN), header="tok-wrong-000")
    assert (result["status"], result["outcome"]) == ("rejected", "UNAUTHORIZED")


def test_placeholder_without_a_header_is_rejected(client, request_body) -> None:
    result = _post(client, request_body(token=PLACEHOLDER_TOKEN))
    assert (result["status"], result["outcome"]) == ("rejected", "UNAUTHORIZED")


def test_empty_body_token_with_the_right_header_passes(client, request_body) -> None:
    result = _post(client, request_body(token=""), header=ORCH)
    assert result["status"] == "completed" and result["meta"]["requester"] == "orchestrator"


def test_wrong_real_body_token_ignores_the_header(client, request_body) -> None:
    result = _post(client, request_body(token="tok-wrong-111"), header=ORCH)
    assert (result["status"], result["outcome"]) == ("rejected", "UNAUTHORIZED")


def test_header_must_be_a_bearer_token(client, request_body) -> None:
    body = request_body(token=PLACEHOLDER_TOKEN)
    result = client.post("/a2a", json=body, headers={"Authorization": ORCH}).json()
    assert result["outcome"] == "UNAUTHORIZED"


def test_header_token_never_comes_back_in_the_result(client, request_body) -> None:
    result = _post(client, request_body(token=PLACEHOLDER_TOKEN), header=ORCH)
    assert result["meta"]["token"] == "" and ORCH not in str(result)


def test_logs_have_the_path_and_name_but_no_token(client, request_body, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _post(client, request_body(token=PLACEHOLDER_TOKEN), header=ORCH)
    _post(client, request_body(token=ORCH), header=DASH)
    _post(client, request_body(token=PLACEHOLDER_TOKEN), header="tok-wrong-000")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "via=header" in text and "via=body" in text and "requester=orchestrator" in text
    for secret in (ORCH, DASH, "tok-wrong-000"):
        assert secret not in text
