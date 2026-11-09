from fastapi.testclient import TestClient

import data_agent
from data_agent.main import app


def test_import() -> None:
    assert data_agent.__name__ == "data_agent"


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ok" and body["meta"] == {}
    # 설명 LLM 설정 여부 (설정이 없으면 설명은 늘 템플릿, fallback_reason=llm_unavailable)
    assert isinstance(body["data"]["llm_configured"], bool)
