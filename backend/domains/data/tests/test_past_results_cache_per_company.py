"""지난 성과(campaign_result) 10분 캐시를 회사마다 따로 둔다 (11/27).

캠페인 번호는 회사마다 겹칠 수 있어, 캐시 열쇠가 캠페인 번호뿐이면 한 회사의 성과가 다른 회사
인사이트에 붙었다. 열쇠를 (회사, 캠페인)으로 둔다.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from data_agent import company_scope, past_results
from data_agent.skills import campaign_result as cr
from data_agent.skills import video_performance as vp


class _Store:
    def __init__(self, _path: Any) -> None:
        pass

    def videos(self, include_test: bool = False) -> list[Any]:
        return [SimpleNamespace(campaign_id="cmp-1", insight_id="ins-1", proposal_id=None)]

    def close(self) -> None:
        pass


def test_same_campaign_id_is_cached_per_company(monkeypatch: pytest.MonkeyPatch) -> None:
    past_results.clear_cache()
    calls: list[str] = []

    async def fake_result(req: Any, *_a: Any, **_k: Any) -> Any:
        who = company_scope.active()
        calls.append(who)
        return SimpleNamespace(output=f"{who}:{req.campaign_id}")

    monkeypatch.setattr(vp, "VideoStore", _Store)
    monkeypatch.setattr(cr, "campaign_result", fake_result)
    env = {vp.DB_ENV: ":memory:"}

    async def run() -> list[Any]:
        return await past_results.campaign_results(env, connector=None)

    try:
        with company_scope.using("catbooks"):
            assert asyncio.run(run()) == ["catbooks:cmp-1"]
            assert asyncio.run(run()) == ["catbooks:cmp-1"]  # 캐시
        with company_scope.using("nubi-tour"):
            assert asyncio.run(run()) == ["nubi-tour:cmp-1"]  # 캣북스 캐시를 쓰지 않는다
        assert calls == ["catbooks", "nubi-tour"]
    finally:
        past_results.clear_cache()
