"""회사 인스타 연결 상태·프로필 주소(instagram_status). 가짜 어댑터만 쓴다."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from marketing_agent import handlers
from marketing_agent import instagram_profile as ip
from marketing_agent.sns_publish import instagram_graph as ig
from marketing_agent.sns_publish.instagram import IgApiError, IgResponseLost


class FakeAdapter:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self._user_id = "17841400000000528"
        self.asked: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None):  # noqa: ANN202
        self.asked.append((method, path, params))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    ip.clear_cache()
    yield
    ip.clear_cache()


def _with(monkeypatch: pytest.MonkeyPatch, answer: Any) -> FakeAdapter:
    fake = FakeAdapter(answer)
    monkeypatch.setattr(ig.GraphInstagramAdapter, "from_env", classmethod(lambda cls: fake))
    return fake


def test_ok_gives_username_and_profile_url(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _with(monkeypatch, {"username": "catbooks_official", "id": "1"})
    out = asyncio.run(ip.status())
    assert out["connection"] == "ok"
    assert (out["username"], out["profile_url"]) == (
        "catbooks_official",
        "https://www.instagram.com/catbooks_official/",
    )
    assert fake.asked == [("GET", "17841400000000528", {"fields": "username"})]
    asyncio.run(ip.status())
    assert len(fake.asked) == 1  # 60초 기억


@pytest.mark.parametrize(
    ("answer", "connection"),
    [
        (IgApiError(190, None), "invalid_credentials"),
        (IgApiError(-1, None), "error"),
        (IgResponseLost("me"), "error"),
    ],
)
def test_errors(monkeypatch: pytest.MonkeyPatch, answer: Exception, connection: str) -> None:
    _with(monkeypatch, answer)
    out = asyncio.run(ip.status())
    assert (out["connection"], out["profile_url"]) == (connection, None)


def test_missing_token_is_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cls: Any) -> Any:
        raise ig.GraphConfigError("설정이 비어 있다")

    monkeypatch.setattr(ig.GraphInstagramAdapter, "from_env", classmethod(boom))
    assert asyncio.run(ip.status())["connection"] == "missing_keys"


def test_odd_username_is_not_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    _with(monkeypatch, {"username": "bad name/../x"})
    out = asyncio.run(ip.status())
    assert (out["connection"], out["username"], out["profile_url"]) == ("ok", None, None)


def test_skill_is_registered() -> None:
    assert "instagram_status" in handlers.HANDLERS
