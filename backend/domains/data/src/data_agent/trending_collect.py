"""인기 검색어 스냅샷 수집 (한 번 또는 `--every 초`마다). run_all.sh가 data와 함께 1시간마다 띄운다.

    cd backend
    uv run python -m data_agent.trending_collect              # 한 번
    uv run python -m data_agent.trending_collect --every 3600 # 1시간마다 (Ctrl+C로 멈춤)

Google Trends 공개 RSS(`connectors/google_trends.py`)를 한 번 읽어 `trending_store`에 저장한다.
지역은 회사 `trends.toml` `[trending] geo`(기본 KR, `DATA_TREND_CONFIG` → 없으면
`config/tenants/<AX_TENANT, 기본 catbooks>/trends.toml`). 키가 필요 없다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.google_trends import GoogleTrendsConnector
from data_agent.trending_store import DB_ENV, DEFAULT_DB, TrendingStore

BACKEND = Path(__file__).resolve().parents[4]
TREND_CONFIG_ENV = "DATA_TREND_CONFIG"
MIN_EVERY_SECONDS = 600  # --every 하한 (공개 피드에 부담 주지 않게)


@dataclass
class RunResult:
    collected_at: datetime
    geo: str
    items: int = 0
    saved: int = 0
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        errors = f" / 실패: {'; '.join(self.errors)}" if self.errors else ""
        return (
            f"[trending] {self.collected_at.isoformat(timespec='seconds')} {self.geo}"
            f" 인기 검색어 {self.items}개, 저장 {self.saved}줄{errors}"
        )


def config_path(env: Mapping[str, str]) -> Path:
    if env.get(TREND_CONFIG_ENV):
        return Path(env[TREND_CONFIG_ENV])
    tenant = (env.get("AX_TENANT") or "catbooks").strip()
    return BACKEND / "config" / "tenants" / tenant / "trends.toml"


def load_geo(path: Path) -> str:
    """회사 `trends.toml` `[trending] geo`. 없거나 틀리면 KR."""
    if not path.is_file():
        return "KR"
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f).get("trending") or {}
        return str(raw.get("geo") or "KR").strip().upper() or "KR"
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        return "KR"


async def collect_once(
    connector: GoogleTrendsConnector,
    store: TrendingStore,
    geo: str,
    *,
    now: datetime | None = None,
) -> RunResult:
    """한 번 읽어 저장한다. 실패는 기록만 하고 빈 목록을 지어내지 않는다."""
    collected_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    result = RunResult(collected_at, geo)
    try:
        items = await connector.trending(geo)
    except ConnectorUnavailable as error:
        result.errors.append(f"인기 검색어 [{error.code}]")
        items = []
    result.items = len(items)
    result.saved = store.save(collected_at, geo, items) if items else 0
    store.log_run(collected_at, geo, result.items, result.errors)
    return result


async def run(every: int | None, env: Mapping[str, str]) -> int:
    store = TrendingStore(env.get(DB_ENV) or DEFAULT_DB)
    geo = load_geo(config_path(env))
    try:
        while True:
            result = await collect_once(GoogleTrendsConnector(env), store, geo)
            print(result.line(), flush=True)
            if every is None:
                return 1 if result.errors else 0
            await asyncio.sleep(every)
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔에서도 한글이 깨지지 않게
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="인기 검색어(Google Trends 공개 RSS) 스냅샷 수집")
    parser.add_argument(
        "--every", type=int, default=None, help=f"초마다 반복 (최소 {MIN_EVERY_SECONDS})"
    )
    args = parser.parse_args(argv)
    if args.every is not None and args.every < MIN_EVERY_SECONDS:
        parser.error(f"--every 는 {MIN_EVERY_SECONDS}초 이상")
    try:
        return asyncio.run(run(args.every, dict(os.environ)))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
