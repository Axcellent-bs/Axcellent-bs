"""설정 키워드 없는 회사의 YouTube 자동 키워드 (11/26): 그 회사 연결 정보(상품 목록)에서."""

from __future__ import annotations

import asyncio

from ax_agent_template import company_connection
from data_agent import youtube_collect
from data_agent.youtube_collect import YouTubeConfig, auto_queries, with_auto_queries

NUBI = "c-cf2f0d"
STUDY = "c-5d7e01"


def test_auto_queries_prefer_category_names_then_products() -> None:
    got = auto_queries(
        {"JP": "일본 여행", "EU": "EU"},  # 코드와 같은 이름(EU)은 쓰지 않는다
        ["[특가] 오사카·교토 4일", "오사카·교토 4일", "다낭 5일 (노쇼핑)", "방콕 4일"],
    )
    assert got == ("일본 여행", "오사카·교토 4일", "다낭 5일")


def test_auto_queries_empty_without_products() -> None:
    assert auto_queries({"DEV": "DEV"}, []) == ()


def test_company_keywords_come_from_its_own_catalog_and_setting_wins() -> None:
    """회사마다 그 회사 범위에서 상품 목록을 읽는다. 설정 키워드가 있으면 그대로."""
    catalogs = {NUBI: ({}, ["오사카 4일"]), STUDY: ({"DEV": "개발"}, ["파이썬 입문"])}

    async def read() -> tuple[dict[str, str], list[str]]:
        return catalogs[company_connection.company_of()]

    companies = [(NUBI, YouTubeConfig()), (STUDY, YouTubeConfig(queries=("코딩 공부",)))]
    got = asyncio.run(with_auto_queries(companies, {}, read))
    assert got == [(NUBI, YouTubeConfig(queries=("오사카 4일",))),
                   (STUDY, YouTubeConfig(queries=("코딩 공부",)))]  # fmt: skip


def test_one_company_catalog_failure_does_not_stop_others() -> None:
    async def read() -> tuple[dict[str, str], list[str]]:
        if company_connection.company_of() == NUBI:
            raise RuntimeError("down")
        return {}, ["파이썬 입문"]

    got = asyncio.run(
        with_auto_queries([(NUBI, YouTubeConfig()), (STUDY, YouTubeConfig())], {}, read)
    )
    assert [c.queries for _, c in got] == [(), ("파이썬 입문",)]


def test_max_auto_queries_is_small() -> None:
    assert youtube_collect.MAX_AUTO_QUERIES == 3
