"""커스텀 영상 공급자 스펙(선언형 JSON)과 검사. 코드를 올리지 않고 설정만으로 공급자를 붙인다.

스펙이 정하는 것: 엔드포인트·인증 헤더·요청 본문 자리표시자·작업 ID 위치·상태값 표기·결과 URL 위치.
보안 규칙은 모두 여기서 저장 전에 검사하고(`validate_spec`), 호출 때 어댑터가 다시 지킨다.

- 주소: https·443·호스트 이름만(계정·쿼리·IP 리터럴 거부). 호스트는 운영자 환경변수
  `MARKETING_CUSTOM_VIDEO_ALLOWED_HOSTS`(쉼표 목록)에 있어야 한다. 비어 있으면 커스텀
공급자는 못 쓴다.
- 키: 환경변수 **이름**만 받는다(`MARKETING_CUSTOM_`로 시작). 값은 저장·로그·응답 어디에도 없다.
  다른 공급자 키 변수를 가리키지 못하게 접두어를 강제한다.
- 본문: 자리표시자 `{{prompt}} {{model}} {{durationSec}} {{resolution}}
  {{aspectRatio}}`만 JSON 값에 넣는다.
- 응답 매핑: 점 표기 부분집합(`data.items[0].url`)만. 임의 식 없음.
- 가격(`pricing.perSecondUsd`)과 길이·해상도 상한이 필수다(없으면 비용 상한 때문에 호출이 막힌다).
- 이미지 선업로드가 필요한 공급자는 범위 밖이다(공개 URL을 받는 텍스트→영상만).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ax_secret_store import get_secret

PROVIDER_PREFIX = "custom:"
ALLOWED_HOSTS_ENV = "MARKETING_CUSTOM_VIDEO_ALLOWED_HOSTS"
SECRET_REF_PREFIX = "MARKETING_CUSTOM_"
MAX_PROVIDERS = 20

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,31}")
SECRET_REF_RE = re.compile(r"MARKETING_CUSTOM_[A-Z0-9_]{3,48}")
HEADER_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,39}")
PATH_RE = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/{}?-]{0,200}")
JSONPATH_RE = re.compile(r"(\$\.)?[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+|\[\d+\])*")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z]+)\s*\}\}")
RESOLUTION_RE = re.compile(r"[0-9]{3,4}p")
BODY_VARS = ("prompt", "model", "durationSec", "resolution", "aspectRatio")
FORBIDDEN_HEADERS = frozenset(
    {
        "host", "content-length", "content-type", "transfer-encoding", "connection", "cookie",
        "set-cookie", "proxy-authorization", "te", "upgrade", "expect", "forwarded",
        "x-forwarded-for", "x-forwarded-host", "x-real-ip",
    }
)  # fmt: skip
MAX_BODY_BYTES = 8 * 1024


class SpecError(ValueError):
    """스펙이 규칙을 어겼다. 메시지는 사람에게 보이는 사유다(값·키는 넣지 않는다)."""


def allowed_hosts() -> frozenset[str]:
    raw = os.getenv(ALLOWED_HOSTS_ENV, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def template_vars(text: str) -> set[str]:
    return {m.group(1) for m in PLACEHOLDER_RE.finditer(text)}


def _body_vars(value: Any, found: set[str]) -> None:
    if isinstance(value, str):
        found |= template_vars(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or "{{" in k:
                raise ValueError("본문 키에는 자리표시자를 쓸 수 없다")
            _body_vars(v, found)
    elif isinstance(value, list):
        for v in value:
            _body_vars(v, found)
    elif not (value is None or isinstance(value, bool | int | float)):
        raise ValueError("본문 값 형식이 맞지 않다")


def check_host(url: str, label: str) -> str:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise SpecError(f"{label}: 주소 형식이 맞지 않다") from None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise SpecError(f"{label}: https 주소만 쓸 수 있다")
    if parts.username is not None or parts.password is not None:
        raise SpecError(f"{label}: 주소에 계정을 넣을 수 없다")
    if parts.query or parts.fragment or (parts.path not in ("", "/")):
        raise SpecError(f"{label}: 호스트까지만 적는다(경로는 따로)")
    if port not in (None, 443):
        raise SpecError(f"{label}: 443 포트만 쓸 수 있다")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise SpecError(f"{label}: IP 주소는 쓸 수 없다. 호스트 이름을 쓴다")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise SpecError(f"{label}: 사내·로컬 이름은 쓸 수 없다")
    return host


class Auth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["header", "jwt_hs256"] = "header"
    headerName: str = "Authorization"  # noqa: N815
    valueTemplate: str = "Bearer {{secret}}"  # noqa: N815
    secretRef: str  # noqa: N815  header: 키 변수 이름. jwt: SecretKey 변수 이름
    accessKeyRef: str | None = None  # noqa: N815  jwt: AccessKey 변수 이름

    @model_validator(mode="after")
    def _check(self) -> Auth:
        if not HEADER_RE.fullmatch(self.headerName) or self.headerName.lower() in FORBIDDEN_HEADERS:
            raise ValueError("auth.headerName을 쓸 수 없다")
        if self.valueTemplate.count("{{secret}}") != 1 or len(self.valueTemplate) > 80:
            raise ValueError("auth.valueTemplate에는 {{secret}}이 한 번 있어야 한다")
        if template_vars(self.valueTemplate) != {"secret"}:
            raise ValueError("auth.valueTemplate에는 {{secret}}만 쓸 수 있다")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.valueTemplate):
            raise ValueError("auth.valueTemplate에 제어문자가 있다")
        if not SECRET_REF_RE.fullmatch(self.secretRef):
            raise ValueError(f"auth.secretRef는 {SECRET_REF_PREFIX}로 시작하는 대문자 변수 이름")
        if self.type == "jwt_hs256":
            if not self.accessKeyRef or not SECRET_REF_RE.fullmatch(self.accessKeyRef):
                raise ValueError("auth.accessKeyRef가 필요하다(jwt)")
            if self.accessKeyRef == self.secretRef:
                raise ValueError("accessKeyRef와 secretRef는 달라야 한다")
        elif self.accessKeyRef is not None:
            raise ValueError("accessKeyRef는 jwt에서만 쓴다")
        return self


def _json_path(value: str, label: str) -> str:
    if not JSONPATH_RE.fullmatch(value) or len(value) > 100:
        raise ValueError(f"{label}: 점 표기 경로만 쓸 수 있다")
    return value


def _req_path(value: str, label: str, allowed: set[str]) -> str:
    if (
        not PATH_RE.fullmatch(value)
        or value.startswith("//")
        or ".." in value
        or "#" in value
        or "\\" in value
    ):
        raise ValueError(f"{label}: 경로 형식이 맞지 않다")
    if not template_vars(value) <= allowed:
        raise ValueError(f"{label}: 쓸 수 없는 자리표시자")
    return value


class Submit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["POST", "PUT"] = "POST"
    path: str
    bodyTemplate: dict[str, Any]  # noqa: N815
    omitIfEmpty: list[str] = Field(default_factory=list, max_length=10)  # noqa: N815
    taskIdPath: str  # noqa: N815
    pollUrlPath: str | None = None  # noqa: N815  응답이 상태 조회 주소를 줄 때(같은 호스트만)

    @model_validator(mode="after")
    def _check(self) -> Submit:
        _req_path(self.path, "submit.path", {"model"})
        _json_path(self.taskIdPath, "submit.taskIdPath")
        if self.pollUrlPath:
            _json_path(self.pollUrlPath, "submit.pollUrlPath")
        found: set[str] = set()
        _body_vars(self.bodyTemplate, found)
        if not found <= set(BODY_VARS):
            raise ValueError("submit.bodyTemplate: 쓸 수 없는 자리표시자")
        if "prompt" not in found:
            raise ValueError("submit.bodyTemplate에는 {{prompt}}가 있어야 한다")
        if len(json.dumps(self.bodyTemplate)) > MAX_BODY_BYTES:
            raise ValueError("submit.bodyTemplate이 너무 크다")
        return self


class Poll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["GET", "POST"] = "GET"
    path: str
    intervalSec: int = Field(default=5, ge=2, le=60)  # noqa: N815
    timeoutSec: int = Field(default=600, ge=10, le=900)  # noqa: N815
    statusPath: str  # noqa: N815
    successValues: list[str] = Field(min_length=1, max_length=8)  # noqa: N815
    failureValues: list[str] = Field(default_factory=list, max_length=8)  # noqa: N815
    errorPath: str | None = None  # noqa: N815

    @model_validator(mode="after")
    def _check(self) -> Poll:
        _req_path(self.path, "poll.path", {"taskId", "model"})
        _json_path(self.statusPath, "poll.statusPath")
        if self.errorPath:
            _json_path(self.errorPath, "poll.errorPath")
        return self


class ResultSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    videoUrlPath: str  # noqa: N815
    path: str | None = None  # 결과를 따로 조회해야 할 때(fal, MiniMax): 이 경로로 한 번 더 GET
    pathVars: dict[str, str] = Field(default_factory=dict, max_length=4)  # noqa: N815
    urlNeedsAuth: bool = False  # noqa: N815  결과 URL이 baseUrl 호스트일 때만 인증 헤더를 붙인다
    allowedHosts: list[str] = Field(default_factory=list, max_length=8)  # noqa: N815

    @model_validator(mode="after")
    def _check(self) -> ResultSpec:
        _json_path(self.videoUrlPath, "result.videoUrlPath")
        for name, expr in self.pathVars.items():
            if not re.fullmatch(r"[A-Za-z]{1,20}", name) or name in ("taskId", "model"):
                raise ValueError("result.pathVars 이름이 맞지 않다")
            _json_path(expr, "result.pathVars")
        if self.path is not None:
            _req_path(self.path, "result.path", {"taskId", "model", *self.pathVars})
        return self


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolutions: list[str] = Field(min_length=1, max_length=4)
    minDurationSec: int = Field(default=3, ge=1, le=60)  # noqa: N815
    maxDurationSec: int = Field(ge=1, le=60)  # noqa: N815
    maxPolls: int = Field(default=120, ge=1, le=200)  # noqa: N815

    @model_validator(mode="after")
    def _check(self) -> Limits:
        if not all(RESOLUTION_RE.fullmatch(r) for r in self.resolutions):
            raise ValueError("limits.resolutions는 720p 같은 형식")
        if self.minDurationSec > self.maxDurationSec:
            raise ValueError("limits: 최소 길이가 최대보다 크다")
        return self


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    perSecondUsd: Decimal = Field(gt=0, le=10)  # noqa: N815
    source: str = Field(min_length=3, max_length=200)  # 가격을 확인한 곳(문서 주소·날짜)


class CustomVideoSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = Field(min_length=1, max_length=60)
    enabled: bool = False  # 기본 꺼짐. 사람이 승인해 켠다
    baseUrl: str  # noqa: N815
    defaultModel: str = Field(default="", max_length=64)  # noqa: N815
    auth: Auth
    extraHeaders: dict[str, str] = Field(default_factory=dict, max_length=8)  # noqa: N815
    submit: Submit
    poll: Poll
    result: ResultSpec
    limits: Limits
    pricing: Pricing

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not ID_RE.fullmatch(value):
            raise ValueError("id는 영문 소문자·숫자·-, 2~32자")
        return value

    @field_validator("extraHeaders")
    @classmethod
    def _headers(cls, value: dict[str, str]) -> dict[str, str]:
        for name, text in value.items():
            if not HEADER_RE.fullmatch(name) or name.lower() in FORBIDDEN_HEADERS:
                raise ValueError("extraHeaders 이름을 쓸 수 없다")
            if len(text) > 200 or "{{" in text or any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise ValueError("extraHeaders 값을 쓸 수 없다(자리표시자·제어문자 불가)")
        return value

    @property
    def provider_name(self) -> str:
        return PROVIDER_PREFIX + self.id


def validate_spec(raw: Any) -> CustomVideoSpec:
    """원본 JSON → 검사된 스펙. 어긋나면 SpecError(사람이 읽는 사유, 값은 담지 않는다)."""
    try:
        spec = CustomVideoSpec.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        if first["type"] == "value_error":
            why = str(first["msg"]).removeprefix("Value error, ")[:120]
        else:
            why = str(first["type"])
        raise SpecError(f"{where}: {why}") from None
    allow = allowed_hosts()
    base_host = check_host(spec.baseUrl, "baseUrl")
    if base_host not in allow:
        raise SpecError(f"baseUrl: 호스트가 운영자 허용 목록({ALLOWED_HOSTS_ENV})에 없다")
    for entry in spec.result.allowedHosts:
        host = entry.strip().lower()
        if host != entry or not re.fullmatch(r"[a-z0-9.-]{3,100}", host):
            raise SpecError("result.allowedHosts: 호스트 이름만 적는다")
        if host not in allow:
            raise SpecError("result.allowedHosts: 운영자 허용 목록에 없는 호스트")
    return spec


def secret_refs(spec: CustomVideoSpec) -> list[str]:
    return [spec.auth.secretRef] + ([spec.auth.accessKeyRef] if spec.auth.accessKeyRef else [])


def public_view(spec: CustomVideoSpec) -> dict[str, Any]:
    """화면·API에 내는 모양. 키 값은 애초에 없고, 변수 이름과 설정 여부만 있다."""
    data = spec.model_dump(mode="json")
    data["keyConfigured"] = all(get_secret(r).strip() for r in secret_refs(spec))
    return data
