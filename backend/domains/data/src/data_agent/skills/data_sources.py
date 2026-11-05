"""data_sources: 데이터 출처 목록 (코드). 출처마다 능력을 적고, 수집 계획(LLM)이 이 안에서만 고른다.

- 출처는 업종 태그로만 구분한다(회사 이름 금지). `"all"` = 전체 업종
- 상태(코드): 필요한 키 환경변수가 모두 있으면 `available`, 아니면 `key_required` + 키 이름.
  네이버 쇼핑인사이트는 검색어 트렌드와 같은 API HUB 키를 쓰지만 이용 신청이 따로일 수 있어,
  실제 호출 1번(`probe`)이 성공해야 `available`이다(결과는 저장, 실패면 하루 뒤 다시 확인)
- 승인(사람): 기존 출처(YouTube·Google 인기 검색어·네이버 검색어 트렌드)는 이미 쓰고 있어
  승인된 것으로 본다. 새 출처 수집은 회사 설정 `[sources] approved`에 출처 id를 적어야 시작한다
  (상태 변경이라 원래는 승인 레지스트리가 필요하다. 지금은 설정 파일 플래그로 대신한다)
- 한도·기능은 공식 문서 기준이고, 문서로 확인하지 못한 수치는 `quota_note`에 "미확인"으로 적는다
README "data_sources".
"""

from __future__ import annotations

import sqlite3
import threading
import tomllib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    DataSource,
    DataSourcesOutput,
    SourceDataKind,
    SourceStatus,
)
from data_agent.connectors import data4library, kosis, naver_shopping
from data_agent.connectors.naver_datalab import CLIENT_ID_ENV, CLIENT_SECRET_ENV
from data_agent.connectors.youtube import KEY_ENV as YOUTUBE_KEY_ENV
from data_agent.skills.domain_profile import TENANT_CONFIG_ENV

DB_ENV = "DATA_SOURCE_PROBE_DB"
DEFAULT_DB = Path(__file__).resolve().parents[5] / ".data" / "source_probe.db"  # backend/.data
RETRY_FAILED = timedelta(days=1)  # 확인 호출이 실패했으면 하루 뒤 다시
ALL = "all"


@dataclass(frozen=True)
class SourceSpec:
    """출처 하나의 능력 (공식 문서 기준, 코드에 적는다)."""

    source_id: str
    name: str
    kinds: tuple[SourceDataKind, ...]
    industries: tuple[str, ...]
    age: bool
    gender: bool
    categories: Literal["api", "fixed", "none"]
    period: str
    quota_daily: int | None
    quota_unit: str
    quota_note: str
    key_envs: tuple[str, ...]
    docs_url: str
    existing: bool  # 이미 수집 중인 출처 (승인된 것으로 본다)
    probe: bool = False  # 키가 있어도 실제 호출 1번으로 확인해야 하는 출처
    unlocks: str = ""  # 키가 있으면 더 쓸 수 있는 근거 (수집 계획에 보여 줌)


SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        "youtube", "YouTube Data API (인기 영상·숏츠 검색)", ("viewing",), (ALL,),
        age=False, gender=False, categories="api", period="수시 (인기 스냅샷·검색)",
        quota_daily=10000, quota_unit="units",
        quota_note=(
            "하루 10,000 단위(기본 할당). search.list 100, videos.list·videoCategories.list 1"
        ),
        key_envs=(YOUTUBE_KEY_ENV,),
        docs_url="https://developers.google.com/youtube/v3/getting-started#quota",
        existing=True,
    ),
    SourceSpec(
        "google_trending", "Google 인기 검색어 (공개 RSS)", ("search_interest",), (ALL,),
        age=False, gender=False, categories="none", period="수시 (지금 인기 검색어)",
        quota_daily=None, quota_unit="calls", quota_note="공개 RSS, 공식 한도 미공개(미확인)",
        key_envs=(), docs_url="https://trends.google.com/trending/rss?geo=KR", existing=True,
    ),
    SourceSpec(
        "naver_search_trend", "네이버 검색어 트렌드 (API HUB)", ("search_interest",), (ALL,),
        age=True, gender=True, categories="none", period="일·주·월 (상대값)",
        quota_daily=1000, quota_unit="calls",
        quota_note="하루 1,000회(NAVER_DATALAB_DAILY_LIMIT). 키워드 그룹 최대 5개·그룹당 20개",
        key_envs=(CLIENT_ID_ENV, CLIENT_SECRET_ENV),
        docs_url="https://api.ncloud-docs.com/docs/naver-api-hub-overview", existing=True,
    ),
    SourceSpec(
        naver_shopping.SOURCE_ID, "네이버 쇼핑인사이트 (API HUB, 쇼핑 분야·키워드 클릭 추이)",
        ("consumption",), (ALL,), age=True, gender=True, categories="fixed",
        period="일·주·월 (상대값, 2017-08-01부터)", quota_daily=1000, quota_unit="calls",
        quota_note=(
            "쇼핑 클릭 추이라 판매량이 아니다. 하루 한도 공식 수치 미확인(429 = 하루 허용량 초과)."
            " 분야 목록 API 없음: 네이버쇼핑 cat_id 후보를 실제 호출로 확인"
        ),
        key_envs=naver_shopping.KEY_ENVS, docs_url=naver_shopping.DOCS_URL, existing=False,
        probe=True,
        unlocks="쇼핑 분야·키워드별 클릭 추이와 연령대별 클릭 비중 (실제 구매 관심 근거)",
    ),
    SourceSpec(
        data4library.SOURCE_ID, "도서관 정보나루 (공공도서관 대출)", ("consumption",),
        ("출판·교육",), age=True, gender=True, categories="fixed",
        period="일·월 (대출 집계)", quota_daily=500, quota_unit="calls",
        quota_note="하루 한도 공식 수치 미확인(비공식 안내 500회). 주제 분류 = KDC 대분류",
        key_envs=data4library.KEY_ENVS, docs_url=data4library.DOCS_URL, existing=False,
        unlocks="주제(KDC)·연령·성별별 인기 대출 도서와 대출 수 (책을 실제로 빌려 읽는 근거)",
    ),
    SourceSpec(
        kosis.SOURCE_ID, "통계청 KOSIS (국가 승인 통계)", ("survey",), (ALL,),
        age=True, gender=True, categories="api", period="통계표마다 다름 (연 1회 조사 등)",
        quota_daily=1000, quota_unit="calls",
        quota_note=(
            "하루 한도 미확인, 2026 공지로 분당 호출 제한(수치 미확인). 통계목록 API로 표를 찾음"
        ),
        key_envs=kosis.KEY_ENVS, docs_url=kosis.DOCS_URL, existing=False,
        unlocks="연 1회 조사 통계 (예: 연령대별 독서율·가구 지출) — 시장 크기 참고값",
    ),
)  # fmt: skip

BY_ID = {s.source_id: s for s in SPECS}


def approved_ids(env: Mapping[str, str]) -> set[str]:
    """회사 설정 `[sources] approved` (새 출처 수집 승인 플래그). 기존 출처는 늘 승인."""
    ids = {s.source_id for s in SPECS if s.existing}
    path = env.get(TENANT_CONFIG_ENV)
    if path and Path(path).is_file():
        try:
            with Path(path).open("rb") as f:
                raw = tomllib.load(f).get("sources") or {}
            ids |= {str(x) for x in raw.get("approved") or [] if str(x) in BY_ID}
        except (OSError, tomllib.TOMLDecodeError, AttributeError):
            pass
    return ids


class ProbeStore:
    """출처 확인 호출 결과 (출처 id → 성공 여부·이유·시각)."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS probes (source_id TEXT PRIMARY KEY,"
                " ok INTEGER NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL)"
            )

    def close(self) -> None:
        self._conn.close()

    def get(self, source_id: str) -> tuple[bool, str, datetime] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT ok, reason, at FROM probes WHERE source_id = ?", (source_id,)
            ).fetchone()
        return (bool(row[0]), row[1], datetime.fromisoformat(row[2])) if row else None

    def put(self, source_id: str, ok: bool, reason: str, at: datetime) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO probes VALUES (?,?,?,?)",
                (source_id, int(ok), reason, at.isoformat()),
            )


Prober = Callable[[str], Awaitable[tuple[bool, str]]]


async def default_probe(source_id: str) -> tuple[bool, str]:
    if source_id == naver_shopping.SOURCE_ID:
        return await naver_shopping.NaverShoppingConnector().probe()
    return False, "확인 호출이 정의되지 않은 출처"


@dataclass(frozen=True)
class SourcesResult:
    output: DataSourcesOutput
    evidence: Evidence


async def list_sources(
    env: Mapping[str, str],
    *,
    prober: Prober | None = None,
    now: datetime | None = None,
    store: ProbeStore | None = None,
    recheck: bool = False,
) -> SourcesResult:
    """출처 목록과 상태. 확인이 필요한 출처는 저장된 확인 결과가 없을 때만 1번 부른다."""
    now = now or datetime.now(UTC)
    prober = prober or default_probe
    approved = approved_ids(env)
    own = store is None
    store = store or ProbeStore(env.get(DB_ENV) or DEFAULT_DB)
    queries: list[str] = []
    out = DataSourcesOutput(checked_at=now)
    try:
        for spec in SPECS:
            missing = [k for k in spec.key_envs if not env.get(k)]
            status: SourceStatus = "available"
            why: str | None = None
            if missing:
                status, why = "key_required", f"키 필요: {', '.join(missing)} (키 등록 후 가능)"
            elif spec.probe:
                seen = store.get(spec.source_id)
                if recheck or seen is None or (not seen[0] and now - seen[2] >= RETRY_FAILED):
                    ok, reason = await prober(spec.source_id)
                    store.put(spec.source_id, ok, reason, now)
                    queries.append(f"probe {spec.source_id} ok={ok}")
                    seen = (ok, reason, now)
                if not seen[0]:
                    status = "key_required"
                    why = (
                        f"지금 키로 확인 호출이 실패함 ({seen[1]}, {seen[2]:%Y-%m-%d %H:%M} UTC)."
                        f" API HUB 앱에서 이 API 이용 신청이 필요할 수 있음"
                    )
                else:
                    why = f"지금 키로 확인 호출 성공 ({seen[2]:%Y-%m-%d %H:%M} UTC)"
            out.sources.append(
                DataSource(
                    source_id=spec.source_id,
                    name=spec.name,
                    kinds=list(spec.kinds),
                    industries=list(spec.industries),
                    age=spec.age,
                    gender=spec.gender,
                    categories=spec.categories,
                    period=spec.period,
                    quota_daily=spec.quota_daily,
                    quota_unit=spec.quota_unit,
                    quota_note=spec.quota_note,
                    key_envs=list(spec.key_envs),
                    status=status,
                    status_reason=why,
                    docs_url=spec.docs_url,
                    approved=spec.source_id in approved,
                )  # fmt: skip
            )
    finally:
        if own:
            store.close()
    ready = [s.source_id for s in out.sources if s.status == "available"]
    need = [s.source_id for s in out.sources if s.status == "key_required"]
    out.summary = (
        f"출처 {len(out.sources)}개: 사용 가능 {', '.join(ready) or '없음'},"
        f" 키 필요 {', '.join(need) or '없음'}"
    )
    return SourcesResult(out, Evidence(queries=queries, data_summary=out.summary, collected_at=now))
