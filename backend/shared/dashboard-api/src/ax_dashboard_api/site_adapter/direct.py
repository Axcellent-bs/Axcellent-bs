"""③-쓰기 직접 쓰기 (W3): 짝 파일 `writes[<동작>]`대로 본문을 계산하고 고객사에 한 번 보낸다.

설계: `docs/superpowers/specs/2026-11-18-site-adapter-design.md` "③-쓰기" 판정 5·7,
"직접 쓰기 결과", "짝 파일 쓰기 항목 모양", "보안 요구".

본문 식 (계산은 코드로만, LLM이 본문을 만들지 않는다). 값의 기준은 요청 본문이다.
- 점 경로·`const:`·`sum:`: 읽기 짝과 같다(`mapping.pick`)
- `first:a,b`: 계산되는 첫 값 (아래 식도 섞어 쓸 수 있다)
- `path:<이름>`: shop-api 경로 값 (예: 문의 `seq`)
- `key_code:<접두>`: 멱등 키로 만든 고정 코드(`[A-Z0-9_-]` 30자 이하). 같은 쓰기면 같은 코드
- `now_plus:<일>`: 지금(UTC)에서 그 날 수 뒤의 ISO 시각
- `json:<값>`: JSON 값 그대로 (`json:true`, `json:["web"]`)
- `link:<자원>.<칸>`: shop-api 번호(그 칸 값)의 고객사 id (`links`). 없으면 계산 실패
본문 칸이 없거나(점 경로) 값이 비었으면(`json:null` 말고) 계산 실패 → 할 일함.

고객사 호출: 읽기(`upstream`)와 같은 주소·키·transport(검사한 IP로만 붙음, 리다이렉트 안 따라감,
시간 제한). 응답은 믿지 않는 데이터라 `MAX_RESPONSE_BYTES`까지만 읽고, 오류 코드는 모양을 검사한
글자만, 결과 칸은 짧은 글자·숫자·참거짓만 옮긴다.

키에 쓰기 권한 없음(고객사가 401·403): `<변환기 폴더>/write_denied.json`
`{"actions": {"<동작>": "<시각>"}}`. 이후 같은 동작은 판정 5로 할 일함 (W4 화면이 이 표시를 보인다).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

import httpx

from ax_dashboard_api.site_adapter import links, upstream
from ax_dashboard_api.site_adapter.mapping import (
    _PATH_PARAM,
    MissingField,
    SiteMapping,
    WriteMap,
    adapter_dir,
    pick,
)

DENIED_FILE = "write_denied.json"
# 고객사 응답 본문 상한 (넘으면 그 뒤는 읽지 않는다)
MAX_RESPONSE_BYTES = 64 * 1024
# 결과 칸으로 옮길 글자 상한
MAX_RESULT_TEXT = 200
# 사이트 오류 문구는 이만큼만 AI에 넘긴다 (장부에는 남기지 않는다)
MAX_SITE_MESSAGE = 120
REJECT_CODES = frozenset({400, 404, 409, 422})
DENY_CODES = frozenset({401, 403})

_SITE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,100}")

logger = logging.getLogger("ax.site_adapter")

Outcome = Literal["done", "rejected", "denied", "partial", "unknown"]


class CalcError(Exception):
    """본문 식을 계산하지 못했다. `why`: `missing`(칸·값 없음)·`link`(번호 짝 없음)."""

    def __init__(self, why: Literal["missing", "link"], expr: str) -> None:
        super().__init__(f"{why}: {expr}")
        self.why = why
        self.expr = expr


@dataclass
class Context:
    """본문 식의 재료. `source`는 요청 본문이라 장부·로그에 남기지 않는다."""

    source: dict[str, Any]
    path_values: dict[str, str]
    # key_code: 재료 (보낸 서비스·동작·멱등 키). 원문은 코드 계산에만 쓴다
    key_material: str
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Plan:
    """고객사에 보낼 쓰기 하나 (계산 끝)."""

    method: str
    url: str
    # 장부·로그용: 짝 파일의 경로 틀(고객사 id를 채우기 전)과 호스트
    path_template: str
    host: str
    body: dict[str, Any]
    headers: dict[str, str] = field(repr=False)
    ok: list[int]
    result: dict[str, str]
    # "반영은 됐고 뒤따르는 일만 실패"를 뜻하는 고객사 오류 코드 (짝의 `partial_codes`)
    partial_codes: list[str] = field(default_factory=list)


@dataclass
class Reply:
    """고객사 응답 (응답이 없으면 `status`가 None)."""

    status: int | None
    body: Any = None
    request_id: str | None = None
    error_kind: str | None = None  # 응답이 없을 때의 까닭 (예외 이름)


def key_code(prefix: str, material: str) -> str:
    """멱등 키로 만든 고정 코드. 접두는 21자까지, 뒤에 지문 8자 이상을 붙여 30자 이하."""
    tail = hashlib.sha256(material.encode("utf-8")).hexdigest().upper()
    return f"{prefix[:21]}-{tail}"[:30]


def evaluate(expr: str, ctx: Context) -> Any:
    """본문 식 하나. 칸이 없으면 `CalcError`."""
    if expr.startswith("path:"):
        name = expr[len("path:") :]
        if name not in ctx.path_values:
            raise CalcError("missing", expr)
        return ctx.path_values[name]
    if expr.startswith("key_code:"):
        return key_code(expr[len("key_code:") :], ctx.key_material)
    if expr.startswith("now_plus:"):
        at = ctx.now + timedelta(days=int(expr[len("now_plus:") :]))
        return at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if expr.startswith("json:"):
        return json.loads(expr[len("json:") :])
    if expr.startswith("link:"):
        resource, _, name = expr[len("link:") :].partition(".")
        shop = ctx.path_values.get(name, ctx.source.get(name))
        if not isinstance(shop, (str, int)) or isinstance(shop, bool) or shop == "":
            raise CalcError("missing", expr)
        site_id = links.find(resource, str(shop))
        if site_id is None:
            raise CalcError("link", expr)
        return site_id
    if expr.startswith("first:"):
        link_missing = False
        for part in expr[len("first:") :].split(","):
            try:
                value = evaluate(part.strip(), ctx)
            except CalcError as exc:
                link_missing = link_missing or exc.why == "link"
                continue
            if value not in (None, ""):
                return value
        raise CalcError("link" if link_missing else "missing", expr)
    try:
        return pick(ctx.source, expr)
    except MissingField as exc:
        raise CalcError("missing", expr) from exc
    except (TypeError, ValueError) as exc:  # sum:에 숫자가 아닌 값
        raise CalcError("missing", expr) from exc


def _filled(expr: str, value: Any) -> bool:
    """본문 칸에 보낼 값인지. `json:`으로 일부러 적은 값 말고는 빈 값을 보내지 않는다."""
    return expr.startswith("json:") or value not in (None, "")


def _path_value(expr: str, value: Any) -> str:
    """경로 칸 값: 글자·정수만, 경로를 벗어나지 않게 한 칸으로 감싼다."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CalcError("missing", expr)
    text = str(value)
    if text in ("", ".", ".."):
        raise CalcError("missing", expr)
    return quote(text, safe="")


def plan(m: SiteMapping, wm: WriteMap, ctx: Context) -> Plan:
    """짝대로 보낼 쓰기를 계산한다.

    칸·번호 짝이 없으면 `CalcError`, 연결이 없으면 `UpstreamError`.
    """
    params = {name: _path_value(e, evaluate(e, ctx)) for name, e in wm.path_params.items()}
    path = _PATH_PARAM.sub(lambda found: params[found.group(1)], wm.path)
    body: dict[str, Any] = {}
    for name, expr in wm.body.items():
        value = evaluate(expr, ctx)
        if not _filled(expr, value):
            raise CalcError("missing", expr)
        body[name] = value
    base = upstream.base_url()
    return Plan(
        method=wm.method,
        url=base + path,
        path_template=wm.path,
        host=httpx.URL(base).host,
        body=body,
        headers=upstream.auth_headers(m.auth_header, m.auth_scheme, upstream.saved_token()),
        ok=list(wm.ok),
        result=dict(wm.result),
        partial_codes=list(wm.partial_codes),
    )


async def send(p: Plan) -> Reply:
    """고객사에 한 번 보낸다(다시 보내지 않는다). 응답 본문은 상한까지만 읽는다."""
    try:
        async with upstream.client() as client:
            async with client.stream(
                p.method, p.url, json=p.body if p.body else None, headers=p.headers
            ) as resp:
                request_id = resp.headers.get("x-request-id")
                raw = bytearray()
                try:
                    async for chunk in resp.aiter_bytes():
                        raw += chunk
                        if len(raw) > MAX_RESPONSE_BYTES:
                            break
                except httpx.HTTPError:
                    raw = bytearray()  # 코드는 받았다: 본문만 버린다
                status = resp.status_code
    except httpx.HTTPError as exc:
        return Reply(None, error_kind=type(exc).__name__)
    body: Any = None
    if raw and len(raw) <= MAX_RESPONSE_BYTES:
        try:
            body = json.loads(bytes(raw).decode("utf-8"))
        except ValueError:
            body = None
    rid = request_id if request_id and _REQUEST_ID.fullmatch(request_id) else None
    return Reply(status, body, rid)


def classify(reply: Reply, ok: list[int], partial_codes: list[str] | None = None) -> Outcome:
    """직접 쓰기 결과 표. 짝의 `ok`가 아닌 2xx·3xx(리다이렉트)·429·5xx·응답 없음은 모름.

    짝의 `partial_codes`에 든 고객사 오류 코드는 `partial`(반영은 됐고 뒤따르는 일만 실패)이다.
    """
    if reply.status is None:
        return "unknown"
    if reply.status in ok:
        return "done"
    if partial_codes and site_error(reply.body)[0] in partial_codes:
        return "partial"
    if reply.status in REJECT_CODES:
        return "rejected"
    if reply.status in DENY_CODES:
        return "denied"
    return "unknown"


def site_error(body: Any) -> tuple[str | None, str | None]:
    """고객사 오류 `{error:{code,message}}`에서 (코드, 짧은 문구). 모양이 아니면 None."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    message = error.get("message")
    site_code = code if isinstance(code, str) and _SITE_CODE.fullmatch(code) else None
    short = None
    if isinstance(message, str) and message.strip():
        short = "".join(c for c in message if c.isprintable())[:MAX_SITE_MESSAGE]
    return site_code, short


def _scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value[:MAX_RESULT_TEXT]
    return None


def result_fields(result: dict[str, str], body: Any) -> dict[str, Any]:
    """짝의 `result`대로 고객사 응답 칸을 shop-api 응답 칸으로 옮긴다(없는 칸은 None)."""
    out: dict[str, Any] = {}
    for name, expr in result.items():
        try:
            out[name] = _scalar(pick(body, expr)) if isinstance(body, dict) else None
        except (MissingField, TypeError, ValueError):
            out[name] = None
    return out


def denied_actions() -> dict[str, str]:
    """키에 쓰기 권한이 없다고 기록된 동작 (동작 → 기록 시각)."""
    try:
        raw = json.loads((adapter_dir() / DENIED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    actions = raw.get("actions") if isinstance(raw, dict) else None
    if not isinstance(actions, dict):
        return {}
    return {str(k): str(v) for k, v in actions.items()}


def mark_denied(action: str) -> None:
    """그 동작을 "키에 쓰기 권한 없음"으로 적는다 (쓰기 장부 잠금 안에서 부른다)."""
    folder = adapter_dir()
    folder.mkdir(parents=True, exist_ok=True)
    actions = denied_actions()
    actions[action] = datetime.now(UTC).isoformat(timespec="seconds")
    tmp = folder / (DENIED_FILE + ".tmp")
    tmp.write_text(json.dumps({"actions": actions}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, folder / DENIED_FILE)
