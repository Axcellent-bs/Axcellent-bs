"""Instagram Graph API 커넥터 (Facebook 로그인을 통한 Instagram API, graph.facebook.com 만).

스크래핑·비공식 API·제3자 수집 서비스는 쓰지 않는다. 공식 문서로 확인한 것 (2026-11-18):
- 오디오: `GET /ig_audio?audio_type={music|original_sound}&user_id=…`
  (search_query를 빼면 인기 상승 오디오). 응답 `{"audio": [{audio_id, title, display_artist(음악),
  ig_username(원본 사운드), duration_in_ms, on_platform_audio_preview_link,
  download_url(약 1.5일 뒤 만료 → 저장하지 않음)}]}`.
  문서상 권한 instagram_basic·instagram_content_publish. **데이터 토큰은 읽기 권한만 전제**하므로
  게시 권한이 없어 권한 오류가 나면 unknown + `AUDIO_PERMISSION_NOTE`
  https://developers.facebook.com/documentation/instagram-platform/content-publishing/audio-api
- 데이터 토큰 권한(읽기만): instagram_basic · pages_show_list · pages_read_engagement
  (+ business_discovery에 필요하면 instagram_manage_insights).
  instagram_content_publish는 요구하지 않음
- 이 커넥터는 GET만 보낸다 (게시·삭제 경로 없음, 시험으로 확인)
- 비즈니스 검색:
  `GET /{우리 IG 사용자 id}?fields=business_discovery.username(계정){media.limit(n){…}}`.
  프로페셔널(비즈니스·크리에이터) 계정만, 나이 제한 계정은 반환 안 됨.
  미디어 필드 id·media_product_type·timestamp·like_count·comments_count·
  view_count(비즈니스 검색에만)·caption·permalink·media_type·media_url·thumbnail_url.
  썸네일(실측 2026-11-18): VIDEO·릴스는 thumbnail_url만, IMAGE·CAROUSEL_ALBUM은 media_url(캐러셀은
  첫 장). 주소는 *.cdninstagram.com 등 CDN이고 oe 쿼리(16진수 유닉스 시각, 약 4일)에 만료.
  필드를 더해도 호출 수는 같다. 이미지는 내려받지 않고 주소만 넘긴다
  https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/business-discovery
  https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media
  `.limit(n)`: https://developers.facebook.com/docs/graph-api/guides/field-expansion
- 해시태그: `GET /ig_hashtag_search?user_id&q` → `{"data":[{"id"}]}`,
  `GET /{해시태그 id}/recent_media`(24시간 안 게시만, 페이지당 최대 50) · `top_media`.
  7일 동안 고유 해시태그 30개(recent·top 조회도 셈). Instagram Public Content Access 기능 검수 필요
  https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/hashtag-search
- 오류: `{"error": {"code", "error_subcode", "type", "message"}}`.
  4·17 호출 과다, 10·200~299 권한, 190 토큰 만료
  https://developers.facebook.com/docs/graph-api/guides/error-handling .
  32·613은 사용 제한 가이드로 안내만 있고 이 문서에서 직접 확인하지 못함(확인 필요) → 한도로 본다

**IG 미디어에는 오디오 id 칸이 없다** (`media_audio_type` = MUSIC·ORIGINAL_SOUND만).
그래서 게시물과 인기 오디오를 오디오 id로 잇는 "겹침"은 공식 API로 볼 수 없다.

- 키: `DATA_IG_ACCESS_TOKEN`(Facebook 로그인 사용자 토큰), `DATA_IG_USER_ID`(우리 IG 비즈니스
  계정 id), `DATA_IG_API_VERSION`(기본 v23.0). 마케팅 `IG_*` 변수는 읽지 않는다.
  토큰은 쿼리로만 보내고 로그·오류·응답에 남기지 않는다(오류 문장에 주소를 넣지 않음)
- 하루 호출 상한 `DATA_IG_DAILY_LIMIT`(기본 150, 한국 날짜)
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_searchad import JsonDailyCache, limit_of
from data_agent.stage_timing import stage

TOKEN_ENV = "DATA_IG_ACCESS_TOKEN"
USER_ENV = "DATA_IG_USER_ID"
VERSION_ENV = "DATA_IG_API_VERSION"
DAILY_LIMIT_ENV = "DATA_IG_DAILY_LIMIT"
CACHE_DB_ENV = "DATA_IG_CACHE_DB"
DEFAULT_VERSION = "v23.0"
DEFAULT_DAILY_LIMIT = 150
HOST = "https://graph.facebook.com"
TIMEOUT_SECONDS = 15.0
NO_KEY = "키 등록 후 가능"
OWNER_NAME = "owner_name"  # business_media가 게시물마다 붙이는 계정 표시 이름 (API 필드 아님)
# 앱 검수 전에는 레이더·해시태그도 권한 오류가 날 수 있다 (Meta 메시지 원문은 넣지 않는다)
PERMISSION_NOTE = "권한 필요(앱 검수·비즈니스 인증 필요할 수 있음)"
# 데이터 토큰은 읽기 권한만 (instagram_basic·pages_show_list·pages_read_engagement, 필요하면
# instagram_manage_insights). 인기 오디오는 게시 권한이 필요해 데이터 토큰으로는 못 읽는다
AUDIO_PERMISSION_NOTE = ("권한 필요: 인기 오디오는 게시 권한(instagram_content_publish)이 필요해"
                         " 데이터 토큰으로는 못 읽어요(마케팅과 방법 정하는 중)")  # fmt: skip
RATE_CODES = frozenset({4, 17, 32, 613})
MEDIA_FIELDS = ("id", "media_product_type", "timestamp", "like_count", "comments_count",
                "view_count", "caption", "permalink", "media_type", "media_url",
                "thumbnail_url")  # fmt: skip

_calls = JsonDailyCache(os.environ.get(CACHE_DB_ENV) or None)  # 하루 호출 수 (응답은 저장하지 않음)


def clear_calls() -> None:
    _calls.clear()


class IgError(ConnectorUnavailable):
    """Graph API 오류. code: KEY_MISSING · PERMISSION · RATE_LIMITED · TOKEN_EXPIRED · UNAVAILABLE ·
    BAD_RESPONSE · NOT_FOUND."""


def _error(status: int, payload: Any) -> IgError:
    err = payload.get("error") if isinstance(payload, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    message = str(err.get("message") or "")[:160] if isinstance(err, dict) else ""
    if code in RATE_CODES:
        return IgError("RATE_LIMITED", f"한도: Graph API 호출 제한(code {code})")
    if code == 190:
        return IgError("TOKEN_EXPIRED", "토큰 만료: 다시 발급 필요 (code 190)")
    if code == 10 or (isinstance(code, int) and 200 <= code <= 299):
        return IgError("PERMISSION", f"{PERMISSION_NOTE} (code {code})")
    if code == 110 or code == 100:
        return IgError("NOT_FOUND", f"찾을 수 없음 또는 지원 안 함 (code {code}): {message}")
    return IgError("UNAVAILABLE", f"Graph API 응답 {status} (code {code})")


class InstagramGraph:
    """graph.facebook.com 읽기 전용. 키·버전·한도는 `env`(기본 `os.environ`)."""

    def __init__(self, env: Mapping[str, str] | None = None, *,
                 client: httpx.AsyncClient | None = None,
                 calls: JsonDailyCache | None = None) -> None:  # fmt: skip
        env = os.environ if env is None else env
        self._token = env.get(TOKEN_ENV) or ""
        self.user_id = env.get(USER_ENV) or ""
        self.version = (env.get(VERSION_ENV) or DEFAULT_VERSION).strip()
        self.daily_limit = limit_of(env, DAILY_LIMIT_ENV, DEFAULT_DAILY_LIMIT)
        self._client = client
        self._calls = calls or _calls

    def __repr__(self) -> str:  # 토큰을 담지 않는다
        return f"InstagramGraph(version={self.version!r})"

    @property
    def configured(self) -> bool:
        return bool(self._token and self.user_id)

    @property
    def calls_today(self) -> int:
        self._calls.get("")
        return self._calls.calls

    async def _get(self, path: str, params: Mapping[str, str]) -> Any:
        if not self.configured:
            raise IgError("KEY_MISSING", f"{TOKEN_ENV}·{USER_ENV}가 없음 ({NO_KEY})")
        self._calls.count_call(self.daily_limit, "Instagram Graph API")
        query = {**params, "access_token": self._token}
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            with stage("external"):
                response = await client.get(f"{HOST}/{self.version}/{path.lstrip('/')}",
                                            params=query)  # fmt: skip
        except httpx.HTTPError as exc:  # 주소(토큰 포함)를 오류 문장에 넣지 않는다
            why = f"Graph API에 연결할 수 없다 ({type(exc).__name__})"
            raise IgError("UNAVAILABLE", why) from None
        finally:
            if client is not self._client:
                await client.aclose()
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code != 200 or (isinstance(payload, dict) and "error" in payload):
            raise _error(response.status_code, payload)
        if payload is None:
            raise IgError("BAD_RESPONSE", "Graph API 응답이 JSON이 아니다")
        return payload

    # ------------------------------------------------------------ 오디오

    async def audio(self, audio_type: str) -> list[dict[str, Any]]:
        """인기 상승 오디오 (search_query 없음). 응답 순서 그대로. download_url은 버린다."""
        payload = await self._get("ig_audio", {"audio_type": audio_type, "user_id": self.user_id})
        items = payload.get("audio") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise IgError("BAD_RESPONSE", "ig_audio 응답에 audio 목록이 없다")
        out = []
        for item in items:
            if not isinstance(item, dict) or not item.get("audio_id"):
                continue
            artist = item.get("display_artist") or item.get("ig_username")
            duration = item.get("duration_in_ms")
            out.append({
                "audio_id": str(item["audio_id"]),
                "title": str(item["title"]) if item.get("title") else None,
                "artist": str(artist) if artist else None,
                "duration_ms": int(duration) if isinstance(duration, int | float) else None,
                "link": item.get("on_platform_audio_preview_link") or None,
            })  # fmt: skip
        return out

    # ------------------------------------------------------------ 비즈니스 검색

    async def token_info(self) -> dict[str, Any]:
        """지금 토큰의 만료 시각 (공식 debug_token, 읽기 전용. 토큰을 바꾸거나 새로 고치지 않는다).

        graph.facebook.com/debug_token?input_token=<토큰>&access_token=<같은 토큰>. 사용자·페이지
        토큰은 자기 자신을 검사할 수 있다. 돌려주는 값: expires_at(초, 0 = 만료 없음)·
        data_access_expires_at·is_valid. 토큰 값은 돌려주지 않는다. 호출 1번(하루 상한에 셈)."""
        payload = await self._get("debug_token", {"input_token": self._token})
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise IgError("NO_DATA", "debug_token이 data를 주지 않음")

        def seconds(key: str) -> int | None:
            value = data.get(key)
            return value if isinstance(value, int) and value >= 0 else None

        return {"expires_at": seconds("expires_at"),
                "data_access_expires_at": seconds("data_access_expires_at"),
                "is_valid": bool(data.get("is_valid"))}  # fmt: skip

    async def business_profile(self, username: str) -> dict[str, Any]:
        """프로페셔널 계정 하나의 공개 정보 (이름·팔로워 수·게시물 수). 호출 1번.

        개인 계정·없는 계정은 business_discovery가 오류를 준다(IgError). 응답이 오면 프로페셔널
        계정이다."""
        info = "username,name,followers_count,media_count"
        fields = f"business_discovery.username({username}){{{info}}}"
        payload = await self._get(self.user_id, {"fields": fields})
        bd = payload.get("business_discovery") if isinstance(payload, dict) else None
        if not isinstance(bd, dict) or not bd.get("username"):
            raise IgError("NO_DATA", "계정 정보를 주지 않음")
        return {"username": str(bd["username"]), "name": bd.get("name"),
                "followers_count": bd.get("followers_count"),
                "media_count": bd.get("media_count")}  # fmt: skip

    async def business_media(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        """프로페셔널 계정 하나의 최근 미디어 (limit개). 개인 계정·나이 제한 계정은 오류."""
        n = max(1, min(50, limit))
        media = f"media.limit({n}){{{','.join(MEDIA_FIELDS)}}}"
        # name(계정 표시 이름)은 같은 호출에서 받는다 (호출 수 그대로).
        # 게시물마다 OWNER_NAME으로 붙인다
        fields = f"business_discovery.username({username}){{username,{media},name}}"
        payload = await self._get(self.user_id, {"fields": fields})
        bd = payload.get("business_discovery") if isinstance(payload, dict) else None
        media = (bd or {}).get("media") if isinstance(bd, dict) else None
        data = media.get("data") if isinstance(media, dict) else None
        if not isinstance(data, list) or not data:
            # 나이 제한 계정 등은 데이터가 비어 온다 → 이 계정만 "데이터 없음"
            raise IgError("NO_DATA", "데이터 없음 (나이 제한 등으로 계정 데이터를 주지 않음)")
        name = bd.get("name") if isinstance(bd, dict) else None
        out = [m for m in data if isinstance(m, dict) and m.get("id")]
        if isinstance(name, str) and name.strip():
            for m in out:
                m[OWNER_NAME] = name.strip()[:60]
        return out

    # ------------------------------------------------------------ 해시태그

    async def hashtag_id(self, name: str) -> str:
        payload = await self._get("ig_hashtag_search", {"user_id": self.user_id, "q": name})
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data or not data[0].get("id"):
            raise IgError("NOT_FOUND", "해시태그 id를 찾지 못함")
        return str(data[0]["id"])

    async def hashtag_media(self, hashtag_id: str, edge: str, fields: Sequence[str],
                            pages: int = 1) -> tuple[list[dict[str, Any]], bool]:  # fmt: skip
        """(미디어, 더 있음). `edge` = recent_media·top_media. 페이지당 50개, `pages`까지만."""
        out: list[dict[str, Any]] = []
        after: str | None = None
        more = False
        for _ in range(max(1, pages)):
            params = {"user_id": self.user_id, "fields": ",".join(fields), "limit": "50"}
            if after:
                params["after"] = after
            payload = await self._get(f"{hashtag_id}/{edge}", params)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise IgError("BAD_RESPONSE", f"{edge} 응답에 data 목록이 없다")
            out.extend(m for m in data if isinstance(m, dict))
            paging = payload.get("paging") if isinstance(payload, dict) else None
            cursors = paging.get("cursors") if isinstance(paging, dict) else None
            nxt = cursors.get("after") if isinstance(cursors, dict) else None
            more = bool(isinstance(paging, dict) and paging.get("next") and nxt and data)
            if not more:
                break
            after = str(nxt)
        return out, more
