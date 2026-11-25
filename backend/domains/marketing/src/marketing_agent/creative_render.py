"""이미지 시트·스토리보드·영상을 Higgsfield로 실제로 만든다 (2026-11-20).

제작 기록(CR-…)마다 세 단계를 기록의 `render` 칸에 남긴다.
- `sheet_image`: 이미지 시트 프롬프트 + 올린 캐릭터(참조)로 이미지 시트 그림
- `board_image`: 스토리보드 시트 프롬프트 + 이미지 시트 그림(참조)으로 스토리보드 그림
- `video`: 스토리보드 그림을 시작 이미지로 영상. **사람 승인(C, `generate_creative_video`) 뒤에만**

공통 순서(돈·크레딧이 나가기 전): 모델 경로·키 확인 → 월 사용량 예산 + 장부 예약(`MediaLedger`,
#1490과 같은 장부) → `execute()` 안전 관문 → `executed`일 때만 Higgsfield 호출 → 파일 저장·정산.
그림은 기존 이미지 생성과 같은 안전 작업 `generate_media`(B, media_kind=image)를, 영상은 새 작업
`generate_creative_video`(C)를 쓴다. 영상은 보류(held)되면 기록에 `review_id`를 남기고,
기록을 다시 읽을 때(`check_video`) 같은 멱등 키로 관문을 다시 물어 승인됐으면 그때 만든다.

Higgsfield(공식 문서 docs.higgsfield.ai, 2026-11-20 확인):
- 인증 `Authorization: Key <ID>:<SECRET>`(키 `MARKETING_HIGGSFIELD_API_KEY`,
  값은 로그·결과에 내지 않는다)
- 생성 `POST https://api.higgsfield.ai/<모델 경로>` → `request_id`, 상태 `GET /requests/<id>/status`
  (queued·in_progress·completed·failed·nsfw·canceled), 완료 `images[].url` / `video.url`.
  결과는 7일 보관이라 받아서 저장한다
- 참조 이미지는 `POST /files/generate-upload-url`로 받은 `upload_url`에 PUT(인증 헤더 없이)하고
  `public_url`을 넘긴다
- 미검증: 나노바나나 등 이미지 모델 경로와 요청 필드 이름은 공개 문서에 없다. 경로는 설정
  (`MARKETING_HIGGSFIELD_IMAGE_MODEL`·`MARKETING_HIGGSFIELD_VIDEO_MODEL`)로만 받고, 필드 이름은 아래
  `IMAGE_FIELDS`·`VIDEO_FIELDS` 한 곳에 모았다. 실제 키로 한 번 불러 본 뒤 맞춘다.

회사 계정 연결(원격 MCP, 구독 크레딧, 2026-11-23): 요청 회사가 Higgsfield 계정을 연결했으면
(`higgsfield_mcp_auth.connected`) 키 대신 그 계정으로 만든다(`creative_mcp.py`).
없으면 위의 키 길 그대로.
- 길(`route`: mcp | api)은 시작할 때 정해 기록에 남긴다. 영상은 승인받은 길·회사 그대로 만든다
  (승인 사이에 연결을 끊었으면 키 길로 바꾸지 않고 `higgsfield_not_connected`로 실패)
- 모델 ID는 `MARKETING_HIGGSFIELD_MCP_IMAGE_MODEL`·`MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL`
- 크레딧이 회사 구독에서 나가므로 고객사 키처럼 우리 키 월 예산을 걸지 않는다(`quota_applies`가
  customer일 때와 같다). 장부 예약(멱등·진행 중 막기)·안전 관문(그림 B, 영상 C)·AI 생성 표시는 같다
- 만들기 전에 예상 크레딧과 남은 크레딧을 본다(`estimate_*_cost` + `balance`, 작업을 만들지 않는다).
  모자라면 `credits_insufficient`. 영상은 승인 요청 때와 실제로 만들기 직전에 두 번 본다
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import math
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ax_agent_template import company_scope as _request_company
from ax_agent_template.execute import execute
from ax_contracts.schemas import Evidence, ExecuteDecision, RequestMeta
from ax_contracts.schemas.marketing_character import REFERENCE_KINDS
from marketing_agent import company_scope as _company_scope
from marketing_agent import creative_mcp as _mcp
from marketing_agent import creative_records as _creative
from marketing_agent import higgsfield_mcp_auth as _mcp_auth
from marketing_agent import motion_ref as _motion
from marketing_agent import render_settings as _settings
from marketing_agent.higgsfield_mcp import McpError, log_line
from marketing_agent.higgsfield_mcp_auth import McpAuthError
from marketing_agent.media import usage as usage_mod
from marketing_agent.media.config import (
    HIGGSFIELD_IMAGE_MODEL_ENV,
    HIGGSFIELD_VIDEO_MODEL_ENV,
    MediaSettings,
)
from marketing_agent.media.ledger import LedgerError, MediaLedger

logger = logging.getLogger("marketing.creative_render")

API_HOST = "https://api.higgsfield.ai"
PROVIDER = "higgsfield"
MCP_PROVIDER = "higgsfield_mcp"  # 회사 계정 연결(구독 크레딧)
MCP, API = "mcp", "api"
# 연결 오류 → 기록·화면 사유 코드
_AUTH_CODES = {
    "not_connected": "higgsfield_not_connected",
    "reconnect_needed": "higgsfield_reconnect_needed",
    "client_id_missing": "higgsfield_app_missing",
}
IMAGE_ACTION = "generate_media"  # 기존 이미지 생성과 같은 안전 작업(B)
VIDEO_ACTION = "generate_creative_video"  # 사람 승인(C)
VIDEO_DURATION_ENV = "MARKETING_HIGGSFIELD_VIDEO_DURATION_S"
DEFAULT_VIDEO_S = 5
MAX_VIDEO_S = 15
POLL_S = 4.0
JOB_TIMEOUT_S = 600.0
HTTP_TIMEOUT_S = 60.0
MAX_DOWNLOAD = 200 * 1024 * 1024
STALE_S = 1800  # 이만큼 넘게 'running'이면 서버가 다시 켜져 끊긴 것으로 본다

# 요청 필드 이름(미검증, 한 곳에 모은다)
IMAGE_FIELDS = {
    "prompt": "prompt",
    "negative": "negative_prompt",
    "refs": "image_urls",
    "aspect": "aspect_ratio",
}
VIDEO_FIELDS = {
    "prompt": "prompt",
    "refs": "image_urls",
    "aspect": "aspect_ratio",
    "duration": "duration",
}
SHEET_ASPECT, BOARD_ASPECT, VIDEO_ASPECT = "16:9", "3:4", "9:16"
IMAGE_CREDITS_DISABLED = "image_credits_disabled"  # 회사 만들기 설정: 그림에 크레딧을 쓰지 않음

PARTS = ("sheet_image", "board_image", "video")
FILES = {
    "sheet_image": "sheet.jpg",
    "board_image": "board.jpg",
    "video": "video.mp4",
    "overlay_video": "overlay.mp4",  # 자막·엔드카드를 얹은 영상(`creative_overlay`)
    "motion_video": "motion.mp4",  # 11/25 사람이 올린 모션 참조 영상
}
OVERLAY_PART = "overlay_video"
OVERLAY_STATE = "overlay"  # render.overlay: {status, error, captions, source_part, ...}
LATEST_VIDEO_PARTS = ("final_video", "video")  # 먼저 있는 것이 최신 영상(BGM 입힌 완성 영상 우선)
ATTACH_ACTION = "attach_creative_file"  # 사람이 올린 파일을 기록에 붙임(A)
# 사람이 올릴 수 있는 칸과 저장 이름(그림은 JPEG·PNG, 영상은 MP4)
UPLOAD_FILES = {
    "sheet_image": ("sheet.jpg", "sheet.png"),
    "board_image": ("board.jpg", "board.png"),
    "video": ("video.mp4",),
    "final_video": ("final.mp4",),
    "motion_video": ("motion.mp4",),  # 11/25 모션 참조 영상(동작만 따른다, `motion_ref`)
}
_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
PENDING = {"queued", "in_progress"}
FAILED = {"failed", "nsfw", "canceled"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)


# 테스트가 바꾼다: Higgsfield HTTP 클라이언트, 기다리기
CLIENT: Callable[[], httpx.AsyncClient] = _client
SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep
_tasks: set[asyncio.Task[Any]] = set()


class RenderError(Exception):
    """사유 코드만 담는다(키·응답 본문을 넣지 않는다)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _key(cid: str | None = None) -> str:
    """그 회사의 Higgsfield 키(앞뒤 공백·줄바꿈을 지운 값). 없으면 빈 글.

    값은 로그에 남기지 않는다."""
    from marketing_agent.higgsfield_status import higgsfield_key

    return higgsfield_key(cid).strip()


def request_company(meta: RequestMeta | None = None) -> str | None:
    """이 의뢰의 회사(정식 ID). 한 번 정해 경로·키·연결 확인·폴더에 같은 값을 쓴다 (11/24).

    순서: 요청 회사(`company_scope.active()`, 서버가 `meta.tenant_id`로 건다) → `meta.tenant_id` →
    설치 회사(`company_ai.company_id()`). 뒤에서 도는 작업은 이 값을 인자로 받아 쓴다."""
    active = _request_company.active()
    if active:
        return active
    tid = getattr(meta, "tenant_id", None) if meta is not None else None
    if tid:
        return _request_company.scope_id(tid)
    return _mcp_auth.company()


def _log_route(via: str, cid: str | None, part: str, record_id: str) -> None:
    """경로를 고른 결과 한 줄 (값 없음)."""
    logger.info("creative_render: route=%s cid=%s part=%s ref=%s", via, cid or "-", part, record_id)


def image_model() -> str:
    return os.getenv(HIGGSFIELD_IMAGE_MODEL_ENV, "").strip()


def video_model() -> str:
    return os.getenv(HIGGSFIELD_VIDEO_MODEL_ENV, "").strip()


def video_seconds() -> int:
    try:
        n = int(os.getenv(VIDEO_DURATION_ENV, "") or DEFAULT_VIDEO_S)
    except ValueError:
        n = DEFAULT_VIDEO_S
    return max(1, min(n, MAX_VIDEO_S))


def mcp_image_model(cid: str | None = None) -> str:
    """회사 만들기 설정(11/25)의 그림 모델. 비어 있으면 서버 설정(MCP_IMAGE_MODEL)."""
    return _settings.effective(cid).image_model


def mcp_video_model(cid: str | None = None) -> str:
    """회사 만들기 설정의 영상 모델. 비어 있으면 서버 설정(MCP_VIDEO_MODEL)."""
    return _settings.effective(cid).video_model


def image_aspect(default: str, cid: str | None = None) -> str:
    """그림 비율. 회사 설정이 "plan"·빈 값이면 `default`, 아니면 영상과 같은 비율."""
    aspect = _settings.effective(cid).aspect
    return default if aspect == "plan" else aspect


def video_aspect(cid: str | None = None) -> str:
    return image_aspect(VIDEO_ASPECT, cid)


def route(cid: str | None = None) -> str:
    """그 회사가 Higgsfield 계정을 연결했으면 언제나 `mcp`(구독 크레딧), 연결이 없을 때만 `api`(키).

    `cid`를 주면 그 회사로 본다(뒤에서 도는 작업). 없으면 지금 요청 회사(예전 그대로)."""
    return MCP if _mcp_auth.connected(cid) else API


def _model(kind: str, via: str, cid: str | None = None) -> str:
    if via == MCP:
        return mcp_image_model(cid) if kind == "image" else mcp_video_model(cid)
    return image_model() if kind == "image" else video_model()


def _provider(via: str) -> str:
    return MCP_PROVIDER if via == MCP else PROVIDER


BILLING_SUBSCRIPTION = "company_subscription"  # 회사 계정 구독 크레딧 (영상)
BILLING_UNLIM = "unlim"  # 회사 계정 무제한 (11/24, 그림: creative_mcp.image_unlim())


def image_billing(via: str) -> str | None:
    """그림 안전 기록의 billing.

    회사 계정 길이고 무제한이면 unlim, 아니면 구독 크레딧. 키 길은 None."""
    if via != MCP:
        return None
    return BILLING_UNLIM if _mcp.image_unlim() else BILLING_SUBSCRIPTION


def _billing_ko(via: str, billing: str | None = None) -> str:
    if via != MCP:
        return "Higgsfield"
    return "Higgsfield 무제한" if billing == BILLING_UNLIM else "Higgsfield 회사 계정 구독 크레딧"


def _code(error: Exception) -> str:
    """실패 사유 코드(값·응답 본문 없이)."""
    if isinstance(error, RenderError):
        return str(error)
    if isinstance(error, McpAuthError):
        return _AUTH_CODES.get(str(error), f"higgsfield_{error}")
    if isinstance(error, McpError):
        if error.code in ("credits_insufficient", _mcp.UNLIM_UNAVAILABLE):  # 사유 코드 그대로
            return error.code
        return f"mcp_{error}"
    return type(error).__name__


def configured(kind: str, via: str | None = None, cid: str | None = None) -> str | None:
    """만들 수 없는 이유 코드. 되면 None. `cid`는 그 회사(없으면 지금 요청 회사)."""
    # 11/25 회사가 그림에 크레딧을 쓰지 않게 했으면 그림 단계는 멈춘다(예약·호출 없음)
    if kind == "image" and not _settings.effective(cid).image_credits_ok:
        return IMAGE_CREDITS_DISABLED
    if (via or route(cid)) == MCP:
        return None if _model(kind, MCP, cid) else "model_not_configured"
    if not _key(cid):  # 공백만 있는 키도 없는 것으로 본다
        return "higgsfield_key_missing"
    if not (image_model() if kind == "image" else video_model()):
        return "model_not_configured"
    return None


COMPANIES_DIR = "companies"


def company_part(company_id: str | None) -> Path:
    """미디어 폴더 안 회사 자리(11/23). 캣북스는 지금 자리 그대로, 다른 회사는 `companies/<회사>`.

    dashboard-api(`marketing_client.creative_asset`)가 같은 규칙으로 파일을 찾는다."""
    cid = _company_scope.scope_id(company_id)
    if cid == _company_scope.scope_id(None):
        return Path()
    return Path(COMPANIES_DIR) / cid


def _dir(record_id: str, cid: str | None = None) -> Path:
    """기록 파일 폴더: 그 회사(`cid`, 없으면 요청 회사 `company_scope.active()`) 자리 아래
    `creative/<기록 번호>`."""
    base = MediaSettings.from_env().media_dir / company_part(cid or _request_company.active())
    return base / "creative" / record_id


def asset_path(record_id: str, part: str, cid: str | None = None) -> Path:
    return _dir(record_id, cid) / FILES[part]


def latest_video_part(rec: dict[str, Any] | None) -> str | None:
    """지금 "최신 영상" 칸: 완성 영상(`final_video`, BGM)이 끝났으면 그것, 아니면 `video`.

    둘 다 없으면 None."""
    render = (rec or {}).get("render") or {}
    for part in LATEST_VIDEO_PARTS:
        if (render.get(part) or {}).get("status") == "done":
            return part
    return None


def publish_video_part(rec: dict[str, Any] | None) -> str | None:
    """유튜브·인스타·썸네일·화면이 같이 쓰는 "내보낼 영상" 칸 (고르는 규칙은 여기 한 곳).

    최신 영상(`latest_video_part`)이 기준이다. 자막을 얹은 결과(`render.overlay`)가 `applied`이고
    그 원본 칸(`source_part`)이 최신 영상 그대로(올린 파일이 바뀌었으면 `source_sha256`도 같게)이며
    `overlay_video`가 끝나 있으면 `overlay_video`, 아니면 최신 영상."""
    latest = latest_video_part(rec)
    if latest is None:
        return None
    render = (rec or {}).get("render") or {}
    overlay = render.get(OVERLAY_STATE) or {}
    result = render.get(OVERLAY_PART) or {}
    if (
        overlay.get("status") == "applied"
        and overlay.get("source_part") == latest
        and result.get("status") == "done"
        and result.get("asset")
        and result.get("sha256")
        and overlay.get("source_sha256") in (None, (render.get(latest) or {}).get("sha256"))
    ):
        return OVERLAY_PART
    return latest


def _set(record_id: str, part: str, **values: Any) -> dict[str, Any]:
    """기록의 render.<part>를 바꾼다(없던 칸은 만든다)."""
    store = _creative.store()
    render = store.get_render(record_id)
    cur = dict(render.get(part) or {})
    cur.update(values)
    cur["at"] = _now().isoformat()
    render[part] = cur
    store.set_render(record_id, render)
    return cur


def _spawn(coro: Awaitable[Any]) -> None:
    task = asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def wait_idle() -> None:
    """테스트용: 돌고 있는 렌더가 끝나기를 기다린다."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
        # 끝난 일은 완료 콜백이 다음 차례에 표에서 지운다. 한 번 양보하지 않으면 끝난 일만 남은 채로
        # 같은 자리를 돈다(끝난 일을 기다리는 await는 양보하지 않는다)
        await asyncio.sleep(0)


def _safe(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - 장부 정리 실패는 결과를 막지 않는다
        logger.exception("creative_render: 장부 정리 실패")


# ---------------------------------------------------------------- Higgsfield HTTP
# 11/24: 키 길도 원인이 보이게 (#1691과 같은 규칙). 단계: api_upload·api_submit·api_status·download
def _checked(headers: dict[str, str], stage: str) -> dict[str, str]:
    """보내기 전에 헤더 값의 CR·LF·제어문자를 본다. 걸리면 client_error(헤더 이름만, 값 X)."""
    from marketing_agent.higgsfield_mcp import bad_header

    if (name := bad_header(headers)) is not None:
        raise McpError("client_error", stage=stage, detail=f"header {name}")
    return headers


def _auth(stage: str = "api_submit", cid: str | None = None) -> dict[str, str]:
    key = _key(cid)  # 앞뒤 공백·줄바꿈을 지운 값
    if not key:
        raise RenderError("higgsfield_key_missing")
    return _checked({"Authorization": f"Key {key}"}, stage)


async def _http(stage: str, send: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
    """httpx 예외 → 사유 코드(network·timeout·client_error) + 단계 (값 없이)."""
    from marketing_agent.higgsfield_mcp import from_http_error

    try:
        return await send()
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise from_http_error(error, stage=stage, tool=None) from error


async def _upload(client: httpx.AsyncClient, data: bytes, content_type: str = "image/jpeg",
                  cid: str | None = None) -> str:  # fmt: skip
    stage = "api_upload"
    headers = _auth(stage, cid)
    r = await _http(stage, lambda: client.post(
        f"{API_HOST}/files/generate-upload-url", json={"content_type": content_type},
        headers=headers))  # fmt: skip
    if r.status_code not in (200, 201):
        raise RenderError(f"upload_url_http_{r.status_code}")
    body = r.json()
    public, put_url = body.get("public_url"), body.get("upload_url")
    if not (isinstance(public, str) and isinstance(put_url, str)):
        raise RenderError("upload_url_missing")
    put_headers = _checked(
        {str(k).strip(): str(v).strip() for k, v in (body.get("upload_headers") or {}).items()},
        stage,
    )
    url = put_url.strip()
    put = await _http(stage, lambda: client.put(url, content=data, headers=put_headers))
    if put.status_code not in (200, 201, 204):  # 인증 헤더는 보내지 않는다
        raise RenderError(f"upload_http_{put.status_code}")
    return public


async def _submit(client: httpx.AsyncClient, model: str, payload: dict[str, Any],
                  cid: str | None = None) -> str:  # fmt: skip
    stage = "api_submit"
    headers = _auth(stage, cid)
    r = await _http(stage, lambda: client.post(f"{API_HOST}/{model}", json=payload,
                                               headers=headers))  # fmt: skip
    if r.status_code not in (200, 201, 202):
        raise RenderError(f"submit_http_{r.status_code}")
    job = r.json().get("request_id")
    if not isinstance(job, str) or not _ID.fullmatch(job):
        raise RenderError("no_request_id")
    return job


def _result_url(body: dict[str, Any], kind: str) -> str:
    if kind == "video":
        url = (body.get("video") or {}).get("url")
    else:
        images = body.get("images") or []
        url = images[0].get("url") if images and isinstance(images[0], dict) else None
    if not isinstance(url, str) or not url.startswith("https://"):
        raise RenderError("no_result_url")
    return url


async def _wait(client: httpx.AsyncClient, job: str, kind: str, cid: str | None = None) -> str:
    """완료되면 결과 URL. 실패·시간 초과면 RenderError."""
    waited = 0.0
    stage = "api_status"
    status_url = f"{API_HOST}/requests/{job}/status"
    while waited <= JOB_TIMEOUT_S:
        headers = _auth(stage, cid)
        r = await _http(stage, functools.partial(client.get, status_url, headers=headers))
        if r.status_code != 200:
            raise RenderError(f"status_http_{r.status_code}")
        body = r.json()
        status = str(body.get("status") or "")
        if status == "completed":
            return _result_url(body, kind)
        if status in FAILED:
            raise RenderError(f"job_{status}")
        if status not in PENDING:
            raise RenderError("job_unknown_status")
        await SLEEP(POLL_S)
        waited += POLL_S
    raise RenderError("job_timeout")


async def _download(client: httpx.AsyncClient, url: str, path: Path) -> str:
    """결과 내려받기 (키 길·회사 계정 길 둘 다). 인증 헤더 없이, 예외는 stage=download로."""
    target = url.strip()
    r = await _http("download", lambda: client.get(target))
    if r.status_code != 200:
        raise RenderError(f"download_http_{r.status_code}")
    if len(r.content) > MAX_DOWNLOAD:
        raise RenderError("download_too_large")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return hashlib.sha256(r.content).hexdigest()


# ---------------------------------------------------------------- 관문 + 예산
def _evidence(record_id: str, what: str) -> Evidence:
    return Evidence(
        queries=["creative_render"],
        data_summary=f"제작 기록 {record_id} {what}",
        collected_at=_now(),
        ref_ids=[record_id],
    )


def _quota_applies(settings: MediaSettings, via: str) -> bool:
    """우리 키로 만들 때만 월 예산을 건다. 회사 계정(구독 크레딧)·고객사 키는 걸지 않는다."""
    return settings.quota_applies and via != MCP


async def _gate_image(
    ledger: MediaLedger,
    meta: RequestMeta,
    record_id: str,
    part: str,
    via: str = API,
    credits: float | None = None,
    cid: str | None = None,
) -> tuple[ExecuteDecision | None, int | None, str | None]:
    """(결정, 장부 seq, 막힌 이유). 막히면 결정·seq는 None."""
    if not _settings.effective(cid).image_credits_ok:  # 11/25 회사 설정: 예약 전에 멈춘다
        return None, None, IMAGE_CREDITS_DISABLED
    settings = MediaSettings.from_env()
    expected = usage_mod.planned("image", None)
    applies = _quota_applies(settings, via)
    cap = settings.quota("image") if applies else None
    if applies and cap is None:
        return None, None, "quota_not_configured"
    key = f"{meta.context_id}:creative_render:{record_id}:{part}"
    now = _now()
    try:
        res = ledger.reserve(
            key=key,
            month=now.strftime("%Y-%m"),
            media_kind="image",
            provider=_provider(via),
            model_id=_model("image", via, cid),
            usage=expected,
            monthly_cap=cap,
            now=now,
        )
    except LedgerError:
        return None, None, "storage_error"
    if res.outcome == "over_budget":
        return None, None, "quota_exceeded"
    if res.row is None or res.outcome != "reserved":
        return None, None, "in_progress"
    if part == "sheet_image":
        label = "이미지 시트"
    elif part.startswith("keyframe_"):  # 11/25 키프레임 (keyframe_<번호>_<시도>)
        label = f"키프레임 {int(part.split('_')[1]) + 1}번"
    else:
        label = "스토리보드"
    billing = image_billing(via)  # 11/24: 무제한이면 unlim("Higgsfield 무제한")
    params = {
        "media_kind": "image",
        "resolution": "1024x1024",
        "provider": _provider(via),
        "scene_template_id": f"creative_{part}",
        "post_draft_id": record_id,
        "request_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "ai_generated": True,
        "usage_unit": expected.unit,
        "usage_amount": expected.amount,
        "summary": f"{label} 그림 생성 1장 ({_billing_ko(via, billing)}, AI 생성)",
    }
    if billing:
        params["billing"] = billing
        if credits is not None:
            params["credits_estimate"] = credits
    decision = await execute(
        IMAGE_ACTION,
        params,
        meta.model_copy(update={"idempotency_key": key}),
        _evidence(record_id, f"{label} 그림"),
    )
    if decision.status != "executed":
        seq = res.row.seq
        _safe(lambda: ledger.release(seq))
        return None, None, f"gate_{decision.status}"
    return decision, res.row.seq, None


# ---------------------------------------------------------------- 이미지 시트·스토리보드 그림
def start_sheet(
    ledger: MediaLedger,
    meta: RequestMeta,
    record_id: str,
    sheet: dict[str, Any],
    ref: bytes | None,
    cid: str | None = None,
) -> None:
    """이미지 시트 프롬프트가 나온 직후 부른다. 설정이 없으면 기록에 이유만 남긴다.

    회사(`cid`, 없으면 `request_company(meta)`)를 여기서 한 번 정해 뒤에서 도는 작업에 넘긴다."""
    cid = cid or request_company(meta)
    via = route(cid)
    _log_route(via, cid, "sheet_image", record_id)
    why = configured("image", via, cid)
    if why:
        _set(record_id, "sheet_image", status="unavailable", error=why, route=via, company_id=cid)
        return
    _set(record_id, "sheet_image", status="running", error=None, route=via, company_id=cid)
    prompt = str(sheet.get("prompt_en") or "")
    negative = str(sheet.get("negative_prompt_en") or "")
    _spawn(
        _render_image(
            ledger,
            meta,
            record_id,
            "sheet_image",
            prompt,
            negative,
            ref,
            image_aspect(SHEET_ASPECT, cid),
            via,
            cid,
        )  # fmt: skip
    )


def _story_sha(rec: dict[str, Any]) -> str:
    return hashlib.sha256(str(rec.get("storyboard_at") or "").encode()).hexdigest()[:12]


def maybe_start_board(ledger: MediaLedger, meta: RequestMeta, record_id: str,
                      cid: str | None = None, force: bool = False) -> None:  # fmt: skip
    """스토리보드가 붙었고 이미지 시트 그림이 있으면 스토리보드 그림을 만든다.

    회사: `cid` → 시트 그림을 만들 때 남긴 회사 → `request_company(meta)`.
    11/25: 영상이 가상 인물 원본을 참조로 쓰면(`board_needed`가 False) 그림을 만들지 않는다(크레딧
    예약 없음). 사람이 [Higgsfield 그림으로 다시 그리기]를 고르면 `force=True`."""
    rec = _creative.store().get(record_id)
    if rec is None or not rec.get("storyboard"):
        return
    render = rec.get("render") or {}
    sheet = render.get("sheet_image") or {}
    board = render.get("board_image") or {}
    if not force:
        # 11/25 키프레임 방식이면 스토리보드 그림 대신 키프레임을 그린다(기획안 → 제작 기록 길
        # #1804도 여기로 그림을 시작한다). 캐릭터 없는 기록은 시트 그림 없이 구도만으로 그린다
        from marketing_agent import keyframes as _kf

        kcid = cid or sheet.get("company_id") or request_company(meta)
        if _kf.active(rec, route(kcid), kcid):
            _spawn(_kf.ensure(ledger, meta, record_id, kcid))
            return
    if board.get("status") in ("running", "done") and board.get("story_sha") == _story_sha(rec):
        return
    if sheet.get("status") != "done":
        if sheet.get("status") in ("unavailable", "failed", "blocked"):
            _set(record_id, "board_image", status="unavailable", error="sheet_image_missing")
        return  # 시트 그림이 끝나면 다시 부른다
    cid = cid or sheet.get("company_id") or request_company(meta)
    via = route(cid)
    if not force and not board_needed(rec, via):
        return
    _log_route(via, cid, "board_image", record_id)
    why = configured("image", via, cid)
    if why:
        _set(record_id, "board_image", status="unavailable", error=why, route=via, company_id=cid)
        return
    try:
        ref = _ref_bytes(record_id, "sheet_image", cid, rec)  # 올린 원본이면 sheet.png일 수 있다
    except OSError:
        _set(record_id, "board_image", status="failed", error="sheet_file_missing")
        return
    board_out = rec["storyboard"]
    _creative.store().set_board_source(record_id, "higgsfield")
    _set(
        record_id,
        "board_image",
        status="running",
        error=None,
        story_sha=_story_sha(rec),
        route=via,
        company_id=cid,
    )
    _spawn(
        _render_image(
            ledger,
            meta,
            record_id,
            "board_image",
            str(board_out.get("sheet_prompt_en") or ""),
            str(board_out.get("negative_prompt_en") or ""),
            ref,
            image_aspect(BOARD_ASPECT, cid),
            via,
            cid,
        )
    )


async def _render_image(
    ledger: MediaLedger,
    meta: RequestMeta,
    record_id: str,
    part: str,
    prompt: str,
    negative: str,
    ref: bytes | None,
    aspect: str,
    via: str = API,
    cid: str | None = None,
) -> None:
    # 회사는 부른 쪽이 정한 값(뒤에서 돌 때 company_scope가 비어도 같은 회사). 없으면 예전 그대로
    cid = cid or request_company(meta)
    if not _settings.effective(cid).image_credits_ok:  # 11/25: 미리 보기·예약 전에 멈춘다
        _set(record_id, part, status="blocked", error=IMAGE_CREDITS_DISABLED)
        if part == "sheet_image":
            maybe_start_board(ledger, meta, record_id, cid)
        return
    params = _mcp.image_params(mcp_image_model(cid), prompt, negative, aspect)
    credits: float | None = None
    if via == MCP:
        try:
            credits = await _mcp_preflight(cid, "image", params)
        except _ERRORS as error:
            # 11/24: 미리 보기(예상 크레딧·잔액) 실패도 원인을 남긴다 (값 없이)
            logger.warning("%s", log_line(f"creative_render: Higgsfield {part} 미리 보기", error,
                                          record_id))  # fmt: skip
            _set(record_id, part, status="blocked", error=_code(error))
            if part == "sheet_image":
                maybe_start_board(ledger, meta, record_id, cid)
            return
    decision, seq, blocked = await _gate_image(ledger, meta, record_id, part, via, credits, cid)
    if blocked or decision is None or seq is None:
        _set(record_id, part, status="blocked", error=blocked)
        if part == "sheet_image":
            maybe_start_board(ledger, meta, record_id, cid)
        return
    try:
        async with CLIENT() as client:
            if via == MCP:
                sha = await _mcp_render(client, cid, record_id, part, "image", params, ref)
            else:
                sha = await _api_image(client, record_id, part, prompt, negative, ref, aspect,
                                       cid)  # fmt: skip
    except _ERRORS as error:
        code = _code(error)
        # 11/24: 사유·단계·도구·예외 종류·정리한 문장·시각 (값 없이)
        logger.warning("%s", log_line(f"creative_render: Higgsfield {part}", error, record_id))
        _safe(lambda: ledger.mark_failed(seq, _now()))
        # 무제한이 안 되면 실패가 아니라 멈춤(크레딧으로 다시 시도하지 않는다, 11/24)
        stopped = code == _mcp.UNLIM_UNAVAILABLE
        _set(record_id, part, status="blocked" if stopped else "failed", error=code)
        if part == "sheet_image":
            maybe_start_board(ledger, meta, record_id, cid)
        return
    usage = usage_mod.planned("image", None)
    _safe(
        lambda: ledger.settle(
            seq,
            model_id=_model("image", via, cid),
            usage=usage,
            usage_source="estimate",
            result_json=f'{{"provider":"{_provider(via)}"}}',
            now=_now(),
        )
    )
    _set(
        record_id,
        part,
        status="done",
        error=None,
        asset=FILES[part],
        sha256=sha,
        execution_id=decision.execution_id,
    )
    if part == "sheet_image":
        maybe_start_board(ledger, meta, record_id, cid)


async def _api_image(
    client: httpx.AsyncClient,
    record_id: str,
    part: str,
    prompt: str,
    negative: str,
    ref: bytes | None,
    aspect: str,
    cid: str | None = None,
) -> str:
    """키 길(예전 그대로): 참조 올리기 → 만들기 → 기다리기 → 내려받기. 돌려주는 값은 sha256.

    키는 그 회사(`cid`)의 것만 쓴다(없으면 설치 기본 키, `higgsfield_key`)."""
    payload: dict[str, Any] = {
        IMAGE_FIELDS["prompt"]: prompt[:8000],
        IMAGE_FIELDS["aspect"]: aspect,
    }
    if negative:
        payload[IMAGE_FIELDS["negative"]] = negative[:1000]
    if ref:
        payload[IMAGE_FIELDS["refs"]] = [await _upload(client, ref, cid=cid)]
    job = await _submit(client, image_model(), payload, cid)
    _set(record_id, part, request_id=job)
    url = await _wait(client, job, "image", cid)
    return await _download(client, url, asset_path(record_id, part, cid))


# ---------------------------------------------------------------- 회사 계정(MCP)
_ERRORS = (RenderError, McpError, McpAuthError, httpx.HTTPError, OSError, ValueError)


async def _mcp_preflight(cid: str | None, kind: str, params: dict[str, Any]) -> float | None:
    """예상 크레딧(모르면 None). 모자라거나 연결이 없으면 예외."""
    if not cid:
        raise McpAuthError("not_connected")
    async with CLIENT() as client:
        return await _mcp.preflight(_mcp.Higgsfield(client, cid), kind, params)


async def _mcp_render(
    client: httpx.AsyncClient,
    cid: str | None,
    record_id: str,
    part: str,
    kind: str,
    params: dict[str, Any],
    ref: bytes | None,
    role: str | None = None,
    extra_refs: list[bytes] | None = None,
    target: Path | None = None,
    uploaded: list[str] | None = None,
    video_ref: bytes | None = None,
) -> str:
    """회사 계정으로: 참조 올리기 → 만들기 → 기다리기 → 내려받기. 돌려주는 값은 sha256.

    `role`을 주면 그 역할로(11/24 영상 image_references), 아니면 그림은 image_references·영상은
    start_image. `target`을 주면 그 파일로 받고 render.<part>는 건드리지 않는다(11/25 키프레임).
    `uploaded`를 주면 올린 참조 media id를 순서대로 담는다."""
    if not cid:
        raise McpAuthError("not_connected")
    hf = _mcp.Higgsfield(client, cid)
    if ref:
        png = ref.startswith(b"\x89PNG")
        name = f"{record_id}-{part}-ref.{'png' if png else 'jpg'}"
        media = await _mcp.upload(hf, ref, name, "image/png" if png else "image/jpeg")
        role = role or (_mcp.IMAGE_REF_ROLE if kind == "image" else _mcp.VIDEO_START_ROLE)
        ids = [media]
        for i, extra in enumerate(extra_refs or [], start=1):  # 11/25 대표 다음 추가 참조 순서대로
            png_x = extra.startswith(b"\x89PNG")
            ext = "png" if png_x else "jpg"
            ids.append(await _mcp.upload(hf, extra, f"{record_id}-{part}-ref{i}.{ext}",
                                         "image/png" if png_x else "image/jpeg"))  # fmt: skip
        params = (_mcp.with_medias(params, ids, role) if len(ids) > 1
                  else _mcp.with_media(params, media, role))  # fmt: skip
        if uploaded is not None:  # 11/25 키프레임: 올린 참조 media id(순서대로)를 부른 쪽에 남긴다
            uploaded.extend(ids)
    if video_ref:  # 11/25 모션 참조 영상(동작·카메라 움직임만 따른다)
        clip = await _mcp.upload(hf, video_ref, f"{record_id}-{part}-motion.mp4", "video/mp4")
        params = _mcp.with_video_reference(params, clip)
    try:
        job = await _mcp.submit(hf, kind, params)
        if target is None:
            _set(record_id, part, request_id=job)
        url = await _mcp.wait(hf, job, SLEEP)
    except McpError as error:
        # 11/24: 무제한으로 만들던 그림이 무제한이 안 된다면 크레딧으로 다시 시도하지 않고 멈춘다
        if _mcp.uses_unlim(params) and (no := _mcp.unlim_unavailable(error)) is not None:
            raise no from error
        raise
    return await _download(client, url, target or asset_path(record_id, part, cid))


# ---------------------------------------------------------------- 영상 (사람 승인)
async def request_video(
    ledger: MediaLedger, meta: RequestMeta, record_id: str, who: str | None,
    cid: str | None = None, *, regenerate: bool = False,
) -> dict[str, Any]:  # fmt: skip
    """영상 만들기를 사람 승인에 올린다. 돌려주는 값은 기록의 render.video.

    회사: `cid` → 그림을 만들 때 남긴 회사 → `request_company(meta)`. 정한 값을 기록에 남겨
    승인 뒤(check_video·_render_video)에도 같은 회사로 만든다.

    11/26 `regenerate`(화면 [영상 다시 만들기] 버튼에서만): 다 만든 영상(done)이라도 그 기록의
    기술 검사(render.qc)에 확인 필요(warn)가 있으면 새 멱등 키로 영상 승인을 다시 요청한다.
    사람 승인 관문·영상 예산 예약은 그대로이고 그 밖은 지금과 같다(자동으로 다시 만들지 않는다)."""
    from marketing_agent import creative_qc

    rec = _creative.store().get(record_id)
    if rec is None:
        raise RenderError("not_found")
    render = rec.get("render") or {}
    video = render.get("video") or {}
    again = regenerate and video.get("status") == "done" and creative_qc.has_warn(render)
    if video.get("status") in ("awaiting_approval", "running", "done") and not again:
        return video
    if render.get(SUPERSEDED):  # 11/26 의뢰 캐릭터를 바꿔 멈춘 기록으로는 영상을 요청하지 않는다
        raise RenderError(CHARACTER_CHANGED)
    board = render.get("board_image") or {}
    cid = cid or board.get("company_id") or request_company(meta)
    via = route(cid)
    from marketing_agent import keyframes as _kf

    # 11/25 키프레임 방식(Kling 4.0 + 기획 키프레임 칸): 키프레임이 모두 그려져야 승인에 올린다
    keyframed = _kf.active(rec, via, cid)
    if keyframed and not _kf.all_done(rec):
        raise RenderError("keyframes_not_ready")
    if board.get("status") != "done" and board_needed(rec, via, cid):
        raise RenderError("board_image_not_ready")
    _log_route(via, cid, "video", record_id)
    why = configured("video", via, cid)
    if why:
        raise RenderError(why)
    settings = MediaSettings.from_env()
    # 11/24 기획 제작안 길이 → 서버 설정·모델 상한 안으로 (기획 길이가 없으면 지금 규칙)
    model = _model("video", via, cid)
    shown = _settings.effective(cid)  # 11/25 회사 만들기 설정: 길이 plan|정수, 비율, 영상 모델
    # 11/25 재생 속도: 더 길게 만들고 자막을 얹을 때 같은 ffmpeg로 빠르게 튼다(creative_overlay)
    seconds, clamped, planned, speed = sped_seconds(
        (rec.get("storyboard") or {}).get(PLAN_DURATION_KEY),
        model,
        shown.video_seconds,
        shown.speed,
    )
    expected = usage_mod.UsageAmount("seconds", seconds)
    applies = _quota_applies(settings, via)
    cap = settings.quota("video") if applies else None
    if applies and cap is None:
        raise RenderError("quota_not_configured")
    credits: float | None = None
    if via == MCP:
        preview = (_kf.video_params(rec, seconds, cid, speed) if keyframed
                   else _video_mcp_params(rec, seconds, cid, speed))  # fmt: skip
        try:
            credits = await _mcp_preflight(cid, "video", preview)
        except _ERRORS as error:
            raise RenderError(_code(error)) from None
    # 11/26 거절(사람·기한 지남) 뒤 다시 요청하면 새 키로 묻는다. 같은 키면 관문이 예전 거절을
    # 그대로 돌려준다. 콘티·키프레임은 기록에 그대로 있어 다시 만들지 않는다
    # 11/26 기술 검사 확인 필요로 다시 만들 때(again)도 새 키
    attempt = int(video.get("attempt") or 0) + (
        1 if video.get("status") == "rejected" or again else 0
    )
    key = f"{meta.context_id}:{VIDEO_ACTION}:{record_id}" + (f":retry{attempt}" if attempt else "")
    now = _now()
    try:
        res = ledger.reserve(
            key=key,
            month=now.strftime("%Y-%m"),
            media_kind="video",
            provider=_provider(via),
            model_id=model,
            usage=expected,
            monthly_cap=cap,
            now=now,
        )
    except LedgerError as error:
        raise RenderError("storage_error") from error
    if res.outcome == "over_budget":
        raise RenderError("quota_exceeded")
    if res.row is None or res.outcome != "reserved":
        raise RenderError("in_progress")
    seq = res.row.seq
    title = (rec.get("storyboard") or {}).get("title_ko") or record_id
    aspect = video_aspect(cid) if via == MCP else VIDEO_ASPECT
    # 11/25 승인 화면용 요약 (보류일 때부터 기록 render.video.summary에 들어 있다)
    summary = {
        "model": model,
        "seconds": seconds,
        "seconds_clamped_from": clamped,
        "aspect": aspect,
        "reference": "character_original"
        if video_reference(rec, model)[0] == "sheet_image"
        else "storyboard_image",
        "credits_estimate": credits,
        "speed": speed,
        "generated_seconds": seconds,
    }
    extra_ko = f", {model or '모델 미정'}, 비율 {aspect}"
    if keyframed:
        summary["reference"] = _kf.REFERENCE
        summary.update(_kf.summary(rec))
        kf_credits = summary.get("keyframe_credits")
        extra_ko += f", 키프레임 {summary['keyframes']}장"
        if kf_credits is not None:
            extra_ko += f"(그림 {kf_credits:g} 크레딧 썼음)"
    if speed > 1.0:
        extra_ko += f", {speed:g}배속으로 틀어 {planned}초"
    if credits is not None:
        extra_ko += f", 예상 {'영상 ' if keyframed else ''}{credits:g} 크레딧"
    params: dict[str, Any] = {
        "record_id": record_id,
        "provider": _provider(via),
        "duration_s": seconds,
        "usage_unit": "seconds",
        "usage_amount": seconds,
        "ai_generated": True,
        "requested_by": (who or "")[:80],
        "summary": f"{'키프레임' if keyframed else '스토리보드'} 영상 {seconds}초 만들기: {title} "
        f"({_billing_ko(via)}{extra_ko}, AI 생성)",
    }
    if via == MCP:
        params["billing"] = BILLING_SUBSCRIPTION  # 영상은 그대로 구독 크레딧(사람 승인 뒤)
        if credits is not None:
            params["credits_estimate"] = credits
    decision = await execute(
        VIDEO_ACTION,
        params,
        meta.model_copy(update={"idempotency_key": key}),
        _evidence(record_id, "스토리보드 영상"),
    )
    base = {
        "key": key,
        "seq": seq,
        "seconds": seconds,
        "context_id": meta.context_id,
        "requested_by": who,
        "params": params,
        "route": via,
        "company_id": cid,
        "summary": summary,
        "speed": speed,
        "plan_seconds": planned,
        "attempt": attempt,
        "reason": None,  # 거절 이유(review_timeout 등). 새 요청이면 지운다
    }
    if keyframed:
        base["keyframes"] = True  # 승인 뒤에도 같은 방식으로 만든다
    if clamped is not None:
        base["seconds_clamped_from"] = clamped
    if decision.status == "held":
        return _set(
            record_id,
            "video",
            status="awaiting_approval",
            review_id=decision.review_id,
            error=None,
            held_at=now.isoformat(),
            **base,
        )
    if decision.status == "executed":
        _set(record_id, "video", status="running", error=None, **base)
        _spawn(_render_video(ledger, record_id, seq, decision))
        return _creative.store().get_render(record_id).get("video") or {}
    _safe(lambda: ledger.release(seq))
    return _set(record_id, "video", status="blocked", error=f"gate_{decision.status}", **base)


async def check_video(ledger: MediaLedger, meta: RequestMeta, record_id: str) -> None:
    """승인 대기 영상이면 같은 키·같은 내용으로 관문을 다시 묻는다(멱등). 승인됐으면 만든다."""
    video = (_creative.store().get_render(record_id).get("video")) or {}
    if video.get("status") != "awaiting_approval" or not video.get("key"):
        return
    stored = meta.model_copy(
        update={
            "idempotency_key": video["key"],
            "context_id": video.get("context_id") or meta.context_id,
        }
    )
    decision = await execute(
        VIDEO_ACTION,
        dict(video.get("params") or {"record_id": record_id}),
        stored,
        _evidence(record_id, "스토리보드 영상"),
    )
    seq = int(video.get("seq") or 0)
    if decision.status in ("held", "verifying"):
        return
    if decision.status == "executed":
        _set(record_id, "video", status="running", error=None)
        _spawn(_render_video(ledger, record_id, seq, decision))
        return
    _safe(lambda: ledger.release(seq))
    # 11/26 기한 지나 자동 취소(review_timeout)면 화면이 "기한 지나 취소됨" + [다시 승인 요청]
    timed_out = decision.hold_reason == "review_timeout"
    reason = "review_timeout" if timed_out else "rejected"
    _set(record_id, "video", status="rejected", error=f"gate_{decision.status}", reason=reason)


VIDEO_PROMPT_MAX = 2000  # 키 길(`_api_video`)·MCP 길(`creative_mcp.video_params`)이 자르는 길이
SCRIPT_HEAD = "Follow this script panel by panel:"


def _one(text: Any) -> str:
    return " ".join(str(text or "").split())


def _secs(x: Any) -> str:
    """초 표기. 화면(`higgsfieldWeb.ts`)과 같게: 정수면 소수점 없이, 아니면 소수 한 자리."""
    v = math.floor(float(x or 0) * 10 + 0.5) / 10  # JS Math.round와 같은 반올림
    return str(int(v)) if v == int(v) else str(v)


# 11/25 예진 님 지적: 영상 모델에 글자를 그리라고 하지 않는다. 자막·CTA는 #1742가 코드로 얹는다
NO_TEXT = "No on-screen text, captions, subtitles, logos or signs; text is added later."
# 첫 문장은 참조 방식(`render.video.summary.reference`)에 맞춘다
REF_STORYBOARD = "storyboard_image"  # 스토리보드 그림을 start_image로
REF_CHARACTER = "character_original"  # 가상 인물 원본을 image_references로 (스토리보드 그림 없음)
OPENING = {
    REF_STORYBOARD: (
        "Animate this storyboard as one continuous short film, panel by panel in order, "
        "keeping the same characters, outfits and art style."
    ),
    REF_CHARACTER: (
        "Make one continuous short film of the same person as the reference images, "
        "keeping face, hair and outfit consistent."
    ),
}


def _scene_line(shot: dict[str, Any], speed: float = 1.0) -> str:
    """장면 한 줄. 시각 = 기획 시각 × 배속(만든 영상 기준). 자막은 넣지 않고, 대사는 따옴표 없이
    장면이 전하는 내용으로만 남긴다(입 모양·글자를 그리게 하지 않는다)."""
    start = float(shot.get("start_s") or 0) * speed
    end = float(shot.get("end_s") or 0) * speed
    line = f"Scene {shot.get('no')} ({_secs(start)}-{_secs(end)}s): " + _one(shot.get("scene_ko"))
    said = _one(shot.get("line_ko"))
    if said:
        line += f" / the action conveys: {said}"
    return line + "."


def script_flow(shots: list[dict[str, Any]] | None, room: int, speed: float = 1.0) -> str:
    """대본을 따라 만든 스토리보드의 장면 흐름·대사 요지. `room` 글자 안에 맞춘다.

    장면마다 같은 몫(남은 길이 ÷ 장면 수)까지만 쓰고, 끝의 글자 금지 문장(`NO_TEXT`)은 늘 남긴다."""
    rows = [s for s in (shots or []) if s.get("start_s") is not None]
    if not rows:
        return ""
    tail = f" {NO_TEXT}"
    share = max(0, (room - len(SCRIPT_HEAD) - len(tail)) // len(rows) - 1)
    body = " ".join(_scene_line(s, speed)[:share] for s in rows)
    return f"{SCRIPT_HEAD} {body}{tail}"


def storyboard_video_prompt(
    style_en: str | None,
    shots: list[dict[str, Any]] | None = None,
    *,
    speed: float = 1.0,
    reference: str = REF_STORYBOARD,
) -> str:
    """영상 프롬프트.

    첫 문장은 참조 방식(`reference`)에 맞춘다: 스토리보드 그림(start_image)이면 "Animate this
    storyboard…", 가상 인물 원본(image_references)이면 "Make one continuous short film of the same
    person…". 대본을 따라 만든 스토리보드면(칸에 시간이 있으면) 장면 흐름(시각 × `speed`)·대사
    요지와 글자 금지 문장을 붙이고 `VIDEO_PROMPT_MAX` 안에 맞춘다. 자막·CTA는 넣지 않는다.
    화면의 [영상 프롬프트 복사 + Higgsfield 열기]가 같은 문장을 만든다
    (`frontend/src/lib/higgsfieldWeb.ts`의 `storyboardVideoPrompt`). 바꾸면 둘 다 바꾼다.
    """
    opening = OPENING.get(reference, OPENING[REF_STORYBOARD])
    base = f"{opening} {style_en or ''}".strip()[:VIDEO_PROMPT_MAX]
    flow = script_flow(shots, VIDEO_PROMPT_MAX - len(base) - 1, speed)
    return f"{base} {flow}"[:VIDEO_PROMPT_MAX] if flow else base


def video_speed(rec: dict[str, Any]) -> float:
    """기록의 실제 배속(`render.video.speed` → `summary.speed`). 없으면 1.0."""
    video = (rec.get("render") or {}).get("video") or {}
    raw = video.get("speed")
    if raw is None:
        raw = (video.get("summary") or {}).get("speed")
    return speed_value(raw)


def _video_prompt(
    rec: dict[str, Any], speed: float | None = None, reference: str = REF_STORYBOARD
) -> str:
    board = rec.get("storyboard") or {}
    shots = board.get("shots") if board.get("script_used") else None
    return storyboard_video_prompt(
        board.get("style_en"),
        shots,
        speed=video_speed(rec) if speed is None else speed,
        reference=reference,
    )


def _video_mcp_params(
    rec: dict[str, Any], seconds: int, cid: str | None = None, speed: float | None = None
) -> dict[str, Any]:
    model = mcp_video_model(cid)
    # 참조 방식은 영상을 만들 때 고르는 것(`video_reference`)과 같다
    part, _ = video_reference(rec, model)
    reference = REF_CHARACTER if part == "sheet_image" else REF_STORYBOARD
    prompt = _video_prompt(rec, speed, reference)
    return _mcp.video_params(model, prompt, video_aspect(cid), seconds)


async def _render_video(
    ledger: MediaLedger, record_id: str, seq: int, decision: ExecuteDecision
) -> None:
    rec = _creative.store().get(record_id) or {}
    video = (rec.get("render") or {}).get("video") or {}
    seconds = int(video.get("seconds") or video_seconds())
    via = MCP if video.get("route") == MCP else API  # 승인받은 길 그대로
    cid = video.get("company_id")  # 승인에 올릴 때 정한 회사 그대로
    # 11/25 모션 참조 영상이 있고 회사 계정 영상 모델이 받으면 동작을 따라 만든다(키프레임보다 먼저)
    motion = _motion_ref_bytes(record_id, cid) if via == MCP else None
    try:
        if via == MCP and video.get("keyframes") and not motion:  # 키프레임 (승인받은 방식 그대로)
            from marketing_agent import keyframes as _kf

            if not cid or not _mcp_auth.connected(cid):
                raise McpAuthError("not_connected")
            if not _kf.all_done(rec):
                raise RenderError("keyframes_not_ready")
            _kf.mark_approved(record_id)
            params = _kf.video_params(rec, seconds, cid, video_speed(rec))
            await _mcp_preflight(cid, "video", params)
            async with CLIENT() as client:
                sha = await _kf.render_video(client, cid, record_id, rec, params)
        elif via == MCP:
            if not cid or not _mcp_auth.connected(cid):
                raise McpAuthError("not_connected")  # 키 길로 바꾸지 않는다
            # 11/24 가상 인물 원본(image_references를 받는 모델) 또는 스토리보드 그림(start_image)
            part, role = video_reference(rec, mcp_video_model(cid))
            ref = _ref_bytes(record_id, part, cid, rec)
            params = _video_mcp_params(rec, seconds, cid)
            if motion:
                params = _motion.with_prompt(params)
            extras: list[bytes] = []
            if role == _mcp.IMAGE_REF_ROLE:  # 11/25 참조 여러 장(모델 상한 안으로 자름)
                room = _mcp.max_image_references(mcp_video_model(cid)) - 1
                extras = extra_reference_bytes(record_id, rec, cid)[: max(room, 0)]
            await _mcp_preflight(cid, "video", params)  # 승인 사이에 크레딧이 줄었을 수 있다
            async with CLIENT() as client:
                sha = await _mcp_render(client, cid, record_id, "video", "video", params, ref,
                                        role=role, extra_refs=extras, video_ref=motion)  # fmt: skip
        else:
            ref = asset_path(record_id, "board_image", cid).read_bytes()
            async with CLIENT() as client:
                sha = await _api_video(client, record_id, _video_prompt(rec), ref, seconds, cid)
    except _ERRORS as error:
        code = _code(error)
        logger.warning("%s", log_line("creative_render: Higgsfield video", error, record_id))
        _safe(lambda: ledger.mark_failed(seq, _now()))
        _set(record_id, "video", status="failed", error=code)
        return
    _safe(
        lambda: ledger.settle(
            seq,
            model_id=_model("video", via, cid),
            usage=usage_mod.UsageAmount("seconds", seconds),
            usage_source="estimate",
            result_json=f'{{"provider":"{_provider(via)}"}}',
            now=_now(),
        )
    )
    _set(
        record_id,
        "video",
        status="done",
        error=None,
        asset=FILES["video"],
        sha256=sha,
        execution_id=decision.execution_id,
    )
    await _overlay_after_attach(record_id, cid)


def _motion_ref_bytes(record_id: str, cid: str | None) -> bytes | None:
    """모션 참조 영상(앞 몇 초, 소리 없음). 없거나 모델이 못 받거나 자르기에 실패하면 None."""
    if not cid or not _mcp.takes_video_references(mcp_video_model(cid)):
        return None
    path = _motion.present(_creative.store().get_render(record_id), _dir(record_id, cid))
    return _motion.trimmed_bytes(path) if path else None


async def _overlay_after_attach(record_id: str, cid: str | None) -> None:
    """영상·완성 영상이 붙으면 최신 영상에 자막·엔드카드를 한 번 얹는다. 실패해도 흐름은 계속."""
    try:
        from marketing_agent import creative_overlay

        await creative_overlay.apply(record_id, cid)
    except Exception:  # noqa: BLE001 - 자막 얹기는 부가 기능이라 영상·게시 흐름을 막지 않는다
        logger.exception("creative_render: 자막 얹기 실패 (%s)", record_id)
    # 11/26 영상 기술 검사(검은 화면·멈춘 화면·길이). 자막 얹기 뒤 내보낼 영상으로 한 번,
    # 같은 sha면 다시 안 돌린다. 실패해도 흐름은 계속
    try:
        from marketing_agent import creative_qc

        await creative_qc.run(record_id, cid)
    except Exception:  # noqa: BLE001 - 기술 검사는 부가 기능이라 영상·게시 흐름을 막지 않는다
        logger.exception("creative_render: 기술 검사 실패 (%s)", record_id)


async def _api_video(
    client: httpx.AsyncClient, record_id: str, prompt: str, ref: bytes, seconds: int,
    cid: str | None = None,
) -> str:  # fmt: skip
    """키 길(예전 그대로). 돌려주는 값은 sha256. 키는 그 회사(`cid`)의 것."""
    payload = {
        VIDEO_FIELDS["prompt"]: prompt[:2000],
        VIDEO_FIELDS["refs"]: [await _upload(client, ref, cid=cid)],
        VIDEO_FIELDS["aspect"]: VIDEO_ASPECT,
        VIDEO_FIELDS["duration"]: seconds,
    }
    job = await _submit(client, video_model(), payload, cid)
    _set(record_id, "video", request_id=job)
    url = await _wait(client, job, "video", cid)
    return await _download(client, url, asset_path(record_id, "video", cid))


# ---------------------------------------------------------------- 의뢰 캐릭터 바꾸기 (11/26)
SUPERSEDED = "superseded"  # render 칸: 의뢰 캐릭터를 바꿔 멈춘 기록 {"at", "by"}
CHARACTER_CHANGED = "character_changed"


def superseded(record_id: str) -> bool:
    """의뢰 캐릭터를 바꿔 멈춘 기록인가(그림을 더 그리지 않고 영상을 요청하지 않는다)."""
    return bool(_creative.store().get_render(record_id).get(SUPERSEDED))


def supersede(record_id: str, by: str | None) -> bool:
    """멈춤 표시. 이미 영상을 요청한 기록은 건드리지 않는다(False)."""
    store = _creative.store()
    if store.get(record_id) is None:
        return False
    render = store.get_render(record_id)
    if (render.get("video") or {}).get("status"):
        return False
    render[SUPERSEDED] = {"at": _now().isoformat(), "by": by}
    store.set_render(record_id, render)
    return True


def unsupersede(record_id: str) -> None:
    """같은 캐릭터로 되돌아오면 멈춤 표시를 지운다(그린 그림은 그대로 다시 쓴다)."""
    store = _creative.store()
    render = store.get_render(record_id)
    if render.pop(SUPERSEDED, None) is not None:
        store.set_render(record_id, render)


def expire_stale(record_id: str) -> None:
    """서버가 다시 켜져 끊긴 'running'을 실패로 바꾼다(기록을 읽을 때)."""
    render = _creative.store().get_render(record_id)
    changed = False
    for part in (*PARTS, OVERLAY_STATE):
        cur = render.get(part) or {}
        if cur.get("status") != "running" or not cur.get("at"):
            continue
        try:
            age = (_now() - datetime.fromisoformat(cur["at"])).total_seconds()
        except ValueError:
            continue
        if age > STALE_S and not _tasks:
            cur.update(status="failed", error="interrupted")
            render[part] = cur
            changed = True
    # 11/25 키프레임 장도 같은 규칙(끊긴 장은 실패 → [다시 그리기]로 고친다)
    kfs = render.get("keyframes") or {}
    for frame in kfs.get("frames") or []:
        if not isinstance(frame, dict) or frame.get("status") != "running" or not frame.get("at"):
            continue
        try:
            age = (_now() - datetime.fromisoformat(frame["at"])).total_seconds()
        except ValueError:
            continue
        if age > STALE_S and not _tasks:
            frame.update(status="failed", error="interrupted")
            kfs["status"] = "failed"
            changed = True
    if changed:
        _creative.store().set_render(record_id, render)


# ---------------------------------------------------------------- 사람이 올린 파일
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


ORIGINAL_SOURCE = "upload_original"  # 11/24 가상 인물·일러스트: 올린 원본을 시트 그림 자리에
_MAGIC = {"image/jpeg": (b"\xff\xd8", "sheet.jpg"), "image/png": (b"\x89PNG", "sheet.png")}


def save_original(record_id: str, data: bytes, mime_type: str, cid: str | None = None) -> None:
    """올린 원본(화면이 긴 변을 줄인 JPEG·PNG, 업로드 검사를 지난 것)을 그 회사 기록 폴더에
    시트 그림(`sheet_image`)으로 저장한다. Higgsfield를 부르지 않는다(크레딧 0).

    실제 사람 사진(real_photo)은 부르지 않는다(원본을 저장하지 않는 지금 규칙)."""
    magic, name = _MAGIC.get(mime_type, (b"", ""))
    if not name or not data.startswith(magic):
        raise RenderError("bad_asset")
    path = _dir(record_id, cid) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _set(record_id, "sheet_image", status="done", error=None, asset=name, sha256=_sha256(path),
         source=ORIGINAL_SOURCE, company_id=cid or _request_company.active())  # fmt: skip


def save_extra(record_id: str, index: int, data: bytes, mime_type: str, cid: str | None = None
               ) -> None:  # fmt: skip
    """추가 참조 이미지(1~3번)를 그 회사 기록 폴더에 `ref_<번호>.jpg`(PNG는 .png)로 저장한다."""
    magic, _ = _MAGIC.get(mime_type, (b"", ""))
    if not 1 <= index <= 3 or not magic or not data.startswith(magic):
        raise RenderError("bad_asset")
    path = _dir(record_id, cid) / f"ref_{index}.{'png' if mime_type == 'image/png' else 'jpg'}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def extra_reference_bytes(record_id: str, rec: dict[str, Any], cid: str | None) -> list[bytes]:
    """기록 `sheet.references`의 추가 이미지(1번~)를 순서대로. 파일이 없으면 건너뛴다."""
    out: list[bytes] = []
    refs = sorted(((rec.get("sheet") or {}).get("references") or []),
                  key=lambda r: int(r.get("index") or 0))  # fmt: skip
    for ref in refs:
        index = int(ref.get("index") or 0)
        if index < 1:
            continue
        for ext in ("jpg", "png"):
            path = _dir(record_id, cid) / f"ref_{index}.{ext}"
            if path.is_file():
                out.append(path.read_bytes())
                break
    return out


PLAN_DURATION_KEY = "plan_duration_s"  # 의뢰 스토리보드에 남긴 기획 제작안 길이 (request_creative)


def _ref_bytes(record_id: str, part: str, cid: str | None, rec: dict[str, Any]) -> bytes:
    """그 칸 파일 (시트 그림은 저장 이름이 jpg·png 둘 중 하나)."""
    name = ((rec.get("render") or {}).get(part) or {}).get("asset")
    if part == "sheet_image" and name in UPLOAD_FILES["sheet_image"]:
        return (_dir(record_id, cid) / str(name)).read_bytes()
    return asset_path(record_id, part, cid).read_bytes()


def is_reference_kind(rec: dict[str, Any] | None) -> bool:
    """기록이 올린 원본을 그대로 참조로 쓰는 종류(가상 인물·일러스트)인가."""
    return _creative.upload_kind((rec or {}).get("sheet")) in REFERENCE_KINDS


def video_reference(rec: dict[str, Any], model: str) -> tuple[str, str]:
    """(참조로 올릴 칸, MCP medias role).

    11/24: 가상 인물·일러스트 기록이고 영상 모델이 `image_references`를 받으면 올린 원본(시트 그림
    자리)을 `image_references`로. 아니면(모르는 모델 포함) 지금처럼 스토리보드 그림을
    `start_image`로."""
    sheet = (rec.get("render") or {}).get("sheet_image") or {}
    # 의뢰 기록은 대표 캐릭터의 원본을 복사해 오므로(`source="brand_copy"`) `original`로 이어 본다
    original = (sheet.get("source") == ORIGINAL_SOURCE or sheet.get("original") is True) and (
        sheet.get("status") == "done"
    )
    if is_reference_kind(rec) and original and _mcp.takes_image_references(model):
        return "sheet_image", _mcp.IMAGE_REF_ROLE
    return "board_image", _mcp.VIDEO_START_ROLE


def board_needed(rec: dict[str, Any], via: str, cid: str | None = None) -> bool:
    """영상 입력으로 Higgsfield 스토리보드 그림이 필요한가(11/25).

    회사 계정(MCP)으로 만들고 영상 모델이 `image_references`를 받으며 캐릭터가 올린 원본이면
    그 원본을 참조로 쓰므로 필요 없다. 키프레임 방식(`keyframes.active`)이면 키프레임이 영상
    입력이라 필요 없다. 그 밖(start_image 모델·real_photo·캐릭터 없음·키 길)은 지금처럼."""
    if via != MCP:
        return True
    from marketing_agent import keyframes as _kf

    if _kf.active(rec, via, cid):
        return False
    return video_reference(rec, mcp_video_model(cid))[0] != "sheet_image"


def plan_seconds(plan_s: Any, model: str, setting: Any = "plan") -> tuple[int, int | None]:
    # 11/25 회사 설정 `setting`이 정수면 기획 길이 대신 그 초(같은 상한 규칙), "plan"이면 기획 길이
    if isinstance(setting, int) and not isinstance(setting, bool) and setting >= 1:
        plan_s = setting
    return _plan_seconds(plan_s, model)


def speed_value(raw: Any) -> float:
    """재생 속도 글("1.0"·"1.2"·"1.5") → 수. 모르면 1.0."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return value if 1.0 <= value <= 2.0 else 1.0


def sped_seconds(
    plan_s: Any, model: str, setting: Any = "plan", speed: Any = "1.0"
) -> tuple[int, int | None, int, float]:
    """(만들 초, 줄였으면 원래 기획 초, 기획 초, 실제 배속). 11/25 재생 속도(AI 티 줄이기).

    만들 길이 = round(기획 길이 × 배속). 모델 상한·서버 `VIDEO_DURATION_S`를 넘으면 상한으로
    줄이고, 실제 배속 = 만든 길이 ÷ 기획 길이(1.0 미만이면 1.0). 기획 길이 자체가 상한을 넘으면
    지금처럼 상한으로 줄인다(`seconds_clamped_from` = 기획 길이).
    배속 1.0이면 `plan_seconds` 그대로."""
    base, clamped = plan_seconds(plan_s, model, setting)
    target = speed_value(speed)
    if target <= 1.0 or clamped is not None:
        return base, clamped, base, 1.0
    made, _ = _plan_seconds(round(base * target), model)
    made = max(made, base)
    actual = round(made / base, 3)
    return made, None, base, actual if actual > 1.0 else 1.0


def _plan_seconds(plan_s: Any, model: str) -> tuple[int, int | None]:
    """(만들 초, 줄였으면 원래 초). 11/24: 기획 제작안 길이(`video_plan.duration_s`)를 쓰고,
    서버 설정 `MARKETING_HIGGSFIELD_VIDEO_DURATION_S`는 상한으로만, 모델 상한도 같이 본다.
    기획 길이가 없으면 지금 규칙(`video_seconds`)."""
    try:
        want = int(plan_s)
    except (TypeError, ValueError):
        return video_seconds(), None
    if want < 1:
        return video_seconds(), None
    cap = _mcp.video_max_seconds(model) or MAX_VIDEO_S
    try:
        if raw := os.getenv(VIDEO_DURATION_ENV, "").strip():
            cap = min(cap, max(1, int(raw)))
    except ValueError:
        pass
    return (want, None) if want <= cap else (cap, want)


async def attach_upload(
    meta: RequestMeta,
    record_id: str,
    part: str,
    asset: str,
    sha256: str,
    size_bytes: int,
    label: str | None,
    who: str | None,
) -> dict[str, Any]:
    """dashboard-api가 저장한 파일을 기록의 render.<part>로 붙인다(관문 A 뒤).

    다른 도구(Higgsfield 화면, 2D 제작 등)로 만든 결과도 제작 기록에 남기려는 길.
    """
    if asset not in UPLOAD_FILES.get(part, ()):
        raise RenderError("bad_asset")
    store = _creative.store()
    if store.get(record_id) is None:
        raise RenderError("not_found")
    path = _dir(record_id) / asset
    if not path.is_file() or _sha256(path) != sha256:
        raise RenderError("file_missing")
    cur = store.get_render(record_id).get(part) or {}
    if cur.get("status") in ("running", "awaiting_approval"):
        raise RenderError("in_progress")
    key = f"{meta.context_id}:creative_attach:{record_id}:{part}:{sha256[:16]}"
    params = {
        "record_id": record_id,
        "part": part,
        "asset": asset,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "summary": f"제작 기록 {record_id}에 {part} 파일 붙이기 (사람이 올림)",
    }
    decision = await execute(
        ATTACH_ACTION,
        params,
        meta.model_copy(update={"idempotency_key": key}),
        _evidence(record_id, f"{part} 파일 올림"),
    )
    if decision.status != "executed":
        raise RenderError(f"gate_{decision.status}")
    item = _set(
        record_id,
        part,
        status="done",
        error=None,
        asset=asset,
        sha256=sha256,
        source="upload",
        label=label,
        by=who,
        review_id=None,
    )
    if part in LATEST_VIDEO_PARTS:  # 새 최신 영상에 자막을 얹는다(올리는 응답은 기다리지 않는다)
        _spawn(_overlay_after_attach(record_id, _request_company.active()))
    return item
