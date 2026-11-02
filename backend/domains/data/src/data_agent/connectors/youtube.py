"""YouTube Data API v3 커넥터 (영상 통계 스냅샷용). 읽기만 한다.

회사 지표 커넥터(`connectors.Connector`)와 모양이 달라 그 인터페이스를 구현하지 않는다.
메서드 3개:
- `popular(region, pages)`: 인기 목록(`videos.list chart=mostPopular`). 50개씩 최대 4페이지 = 200개.
  인기 순위(1부터)를 붙인다. 쪽당 1단위
- `videos(ids)`: 추적 중인 영상 통계(`videos.list id=`). 50개씩, 묶음당 1단위. 지워졌거나 비공개인
  영상은 응답에 없어서 결과에도 없다(0으로 만들지 않는다)
- `search(query, published_after, region)`: 키워드 검색(`search.list`, 100단위).
  검색 결과에는 통계가 없어서 `videos.list`로 한 번 더 받는다(1단위)

- 주소: `AX_YOUTUBE_API_URL`(없으면 `DEFAULT_URL`). 키: `YOUTUBE_API_KEY`(`.env`)만.
  키는 URL에 넣지 않고 헤더 `X-Goog-Api-Key`로 보낸다(오류 문장·로그에 URL이 찍혀도 키가 없게).
- 숫자 칸(조회수·좋아요·댓글)이 응답에 없으면(좋아요 숨김·댓글 막음) None이다. 0이 아니다.
- 못 읽으면 `ConnectorUnavailable`: 키 없음·401·403 `UNAUTHORIZED`, 403 할당량 초과
  `QUOTA_EXCEEDED`, 400 `BAD_REQUEST`, 429 `RATE_LIMITED`, 연결 실패·5xx `UNAVAILABLE`,
  JSON 아님·형식 다름 `BAD_RESPONSE`.
- 할당량: 부를 때마다 문서 단가(`COST`)를 `quota_used`에 더한다. 실패한 호출도 할당량을 쓰므로 센다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from data_agent.connectors import ConnectorUnavailable

DEFAULT_URL = "https://www.googleapis.com/youtube/v3"
URL_ENV = "AX_YOUTUBE_API_URL"
KEY_ENV = "YOUTUBE_API_KEY"
TIMEOUT_SECONDS = 15.0
PAGE_SIZE = 50  # videos.list 한 번에 최대 50개
MAX_POPULAR_PAGES = 4  # mostPopular는 200개까지 준다
COST = {"videos": 1, "search": 100}  # 문서 단가 (단위)
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"})

_client: httpx.AsyncClient | None = None  # 테스트가 `use_client`로 바꿔 끼운다


def use_client(client: httpx.AsyncClient | None) -> None:
    """HTTP 클라이언트를 바꿔 끼운다 (테스트용). None이면 호출마다 새로 만든다."""
    global _client
    _client = client


# ---------------------------------------------------------------- 표준 형식


@dataclass(frozen=True)
class VideoStat:
    """영상 하나의 그 순간 값. 숫자 칸이 None이면 응답에 없던 것(숨김)이다."""

    video_id: str
    channel_id: str
    category_id: str | None
    published_at: datetime  # UTC
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    tags: tuple[str, ...]
    title: str = ""
    rank: int | None = None  # 인기 순위 (인기 목록에서 받았을 때만, 1부터)


# ---------------------------------------------------------------- YouTube 응답 형식 (여기서만 쓴다)


class _Stats(BaseModel):
    model_config = ConfigDict(extra="ignore")
    viewCount: int | None = Field(default=None, ge=0)  # noqa: N815 - API 칸 이름
    likeCount: int | None = Field(default=None, ge=0)  # noqa: N815
    commentCount: int | None = Field(default=None, ge=0)  # noqa: N815


class _Snippet(BaseModel):
    model_config = ConfigDict(extra="ignore")
    publishedAt: datetime  # noqa: N815
    channelId: str  # noqa: N815
    title: str = ""
    categoryId: str | None = None  # noqa: N815
    tags: list[str] = Field(default_factory=list)


class _Video(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    snippet: _Snippet
    statistics: _Stats = Field(default_factory=_Stats)


class _VideoPage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[_Video]
    nextPageToken: str | None = None  # noqa: N815


class _SearchId(BaseModel):
    model_config = ConfigDict(extra="ignore")
    videoId: str | None = None  # noqa: N815


class _SearchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: _SearchId


class _SearchPage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[_SearchItem]


def _stat(video: _Video, rank: int | None = None) -> VideoStat:
    sn, st = video.snippet, video.statistics
    return VideoStat(
        video_id=video.id,
        channel_id=sn.channelId,
        category_id=sn.categoryId,
        published_at=sn.publishedAt.astimezone(UTC),
        view_count=st.viewCount,
        like_count=st.likeCount,
        comment_count=st.commentCount,
        tags=tuple(t.strip() for t in sn.tags if t.strip()),
        title=sn.title,
        rank=rank,
    )


# ---------------------------------------------------------------- 커넥터


class YouTubeConnector:
    """YouTube 읽기. 키·주소는 `env`(기본 `os.environ`)에서 읽는다.

    `quota_used`에 쓴 할당량 단위가 쌓인다.
    """

    def __init__(
        self, env: Mapping[str, str] | None = None, *, client: httpx.AsyncClient | None = None
    ) -> None:
        env = os.environ if env is None else env
        self.url = (env.get(URL_ENV) or DEFAULT_URL).rstrip("/")
        self._key = env.get(KEY_ENV) or ""
        self._client = client
        self.quota_used = 0

    def __repr__(self) -> str:  # 키를 담지 않는다
        return f"YouTubeConnector(url={self.url!r}, quota_used={self.quota_used})"

    async def popular(self, region: str = "KR", pages: int = MAX_POPULAR_PAGES) -> list[VideoStat]:
        """인기 목록. 순위를 붙인다. 다음 쪽이 없으면 거기서 멈춘다."""
        out: list[VideoStat] = []
        token: str | None = None
        for _ in range(max(1, min(pages, MAX_POPULAR_PAGES))):
            params = {"part": "snippet,statistics", "chart": "mostPopular",
                      "regionCode": region, "maxResults": PAGE_SIZE}  # fmt: skip
            if token:
                params["pageToken"] = token
            page = _parse(_VideoPage, await self._get("videos", params), "인기 목록")
            first = len(out) + 1  # 이 쪽 첫 영상의 순위
            out.extend(_stat(v, rank=first + i) for i, v in enumerate(page.items))
            token = page.nextPageToken
            if not token:
                break
        return out

    async def videos(self, ids: Sequence[str]) -> list[VideoStat]:
        """영상 id 목록의 지금 값. 응답에 없는 영상(지워짐·비공개)은 결과에도 없다."""
        unique = list(dict.fromkeys(i for i in ids if i))
        out: list[VideoStat] = []
        for start in range(0, len(unique), PAGE_SIZE):
            chunk = unique[start : start + PAGE_SIZE]
            params = {"part": "snippet,statistics", "id": ",".join(chunk), "maxResults": PAGE_SIZE}
            page = _parse(_VideoPage, await self._get("videos", params), "영상 통계")
            out.extend(_stat(v) for v in page.items)
        return out

    async def search(
        self, query: str, published_after: datetime, region: str = "KR", max_results: int = 25
    ) -> list[VideoStat]:
        """키워드 검색(최근 게시) → 결과 영상의 통계. 검색 100단위 + 통계 1단위."""
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "regionCode": region,
            "order": "viewCount",
            "maxResults": max(1, min(max_results, PAGE_SIZE)),
            "publishedAfter": published_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        page = _parse(_SearchPage, await self._get("search", params), "검색")
        ids = [item.id.videoId for item in page.items if item.id.videoId]
        return await self.videos(ids) if ids else []

    async def _get(self, resource: str, params: dict[str, Any]) -> Any:
        if not self._key:
            raise ConnectorUnavailable(
                "UNAUTHORIZED", f"{KEY_ENV}가 없어 YouTube Data API를 부를 수 없다"
            )
        self.quota_used += COST[resource]
        client = self._client or _client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.get(
                f"{self.url}/{resource}", params=params, headers={"X-Goog-Api-Key": self._key}
            )
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(
                "UNAVAILABLE", f"YouTube Data API에 연결할 수 없다 ({type(exc).__name__})"
            ) from exc
        finally:
            if client is not self._client and client is not _client:
                await client.aclose()
        if response.status_code != 200:
            raise _error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorUnavailable("BAD_RESPONSE", "YouTube 응답이 JSON이 아니다") from exc


def _parse[T: BaseModel](model: type[T], payload: Any, what: str) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ConnectorUnavailable(
            "BAD_RESPONSE", f"YouTube {what} 응답 형식이 예상과 다르다"
        ) from exc


def _error(response: httpx.Response) -> ConnectorUnavailable:
    status = response.status_code
    reasons: set[str] = set()
    message = ""
    try:
        error = response.json()["error"]
        reasons = {str(e.get("reason")) for e in error.get("errors", []) if isinstance(e, dict)}
        message = str(error.get("message", ""))[:200]
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    if status == 403 and reasons & QUOTA_REASONS:
        code = "QUOTA_EXCEEDED"
    else:
        code = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "UNAUTHORIZED",
                429: "RATE_LIMITED"}.get(status, "UNAVAILABLE")  # fmt: skip
    detail = f": {message}" if message else ""
    return ConnectorUnavailable(code, f"YouTube 응답 {status} ({code}){detail}")
