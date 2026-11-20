"""회사 연결: 고객사 판매 서버(shop-api 규격) 주소를 화면에서 정한다.

- 저장: `AX_COMPANY_CONNECTION_FILE`이 가리키는 JSON 파일
  (`{"shop_api_url", "enabled", "verified_at", "updated_at", "updated_by"}`).
  회사 폴더를 켰으면(`AX_COMPANIES_DIR`, 설계 1-4) `<회사 폴더>/catbooks/company_connection.json`에
  두고, `AX_COMPANY_CONNECTION_FILE` 자리에 옛 파일이 있으면 처음 읽을 때 옮긴다(`company_files`).
  `AX_COMPANY_CONNECTION_FILE`은 그대로 설치 기준 자리다: 계정·연결 키·설치 기록처럼 회사 밖
  파일이 그 옆에 있다(`connection_file()`). 연결 파일을 읽고 쓰는 곳은 `connection_path()`.
  쓰는 곳은 dashboard-api 한 곳이다 (시스템 관리 > 회사 연결, 사람 토큰).
- 읽기: `config.agent_url("shop-api")`가 부를 때마다 바뀐 시각·크기로 확인해 읽는다
  (재시작 없이 반영).
  순서는 이 파일 → `AX_SHOP_API_URL` → 기본 포트 표다.
- `enabled`가 false면 주소는 남겨 두고 쓰지 않는다(내부 데이터 모드). 없으면 true(예전 파일).
- `kind`: `shop_api`(기본, 없으면 예전 파일) = 고객사가 AXcellent 규격 판매 서버를 쓴다.
  `adapter` = 고객사 자기 API를 dashboard-api 사이트 변환기(`/site-adapter`)가 바꿔 준다.
  이때 `adapter.base_url`이 고객사 API 주소이고, AI들이 받는 shop-api 주소는 변환기다.
- 주소 정책(`check_host`): 링크 로컬·메타데이터·0.0.0.0은 늘 거절한다.
  사내 주소(localhost, 10/8, 172.16/12, 192.168/16)는 받고, 사내 밖은 https이면서
  `AX_COMPANY_SHOP_ALLOWED_HOSTS`(쉼표 목록, 설치 운영자가 정함)에 있어야 받는다.
  에이전트 토큰과 승인된 쓰기가 이 주소로 가기 때문이다. 이름은 IP로 풀어서 본다.
  실제 호출은 `pinned_http`의 transport로 연결 직전에 다시 풀고 검사한 IP로만 붙는다
  (DNS 재바인딩 차단).
- 바꿀 때마다 `<연결 파일>.history.jsonl`에 한 줄씩 쌓는다 (언제·누가·무엇을·이전 주소·새 주소).
- 파일이 없으면 연결이 없는 것이다(로컬 개발 기본, 경고 없음).
  깨졌으면 경고를 한 번 남기고 없는 것으로 본다.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

CONNECTION_FILE_ENV = "AX_COMPANY_CONNECTION_FILE"
ALLOWED_HOSTS_ENV = "AX_COMPANY_SHOP_ALLOWED_HOSTS"
MAX_URL_LENGTH = 300
# kind=adapter일 때 AI들이 부를 변환기 경로 (dashboard-api 안)
ADAPTER_PATH = "/site-adapter"
Kind = Literal["shop_api", "adapter"]
KINDS: tuple[str, ...] = ("shop_api", "adapter")

logger = logging.getLogger("ax.company_connection")


@dataclass(frozen=True)
class Connection:
    """저장된 회사 연결 한 건."""

    shop_api_url: str
    verified_at: str | None
    updated_at: str | None
    updated_by: str | None
    enabled: bool = True
    kind: Kind = "shop_api"
    adapter: dict[str, str] | None = None  # kind=adapter: {"base_url": 고객사 API 주소}
    tenant: str | None = None  # 회사 설정 폴더 id (config/tenants/<id>, ax_agent_template.tenant)

    def as_dict(self) -> dict[str, Any]:
        return {
            "shop_api_url": self.shop_api_url,
            "enabled": self.enabled,
            "verified_at": self.verified_at,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "kind": self.kind,
            "adapter": dict(self.adapter) if self.adapter else None,
            "tenant": self.tenant,
        }


def connection_file() -> str:
    """설치 기준 자리(예전 연결 파일 경로). 설정하지 않았으면 빈 문자열(연결 기능 꺼짐).

    계정·연결 키·설치 기록 같은 회사 밖 파일이 이 옆에 있다. 연결 파일 자체는 `connection_path()`.
    """
    return os.getenv(CONNECTION_FILE_ENV, "").strip()


CONNECTION_NAME = "company_connection.json"
HISTORY_SUFFIX = ".history.jsonl"


def connection_path() -> str:
    """연결 파일을 읽고 쓰는 경로. 회사 폴더를 켰으면 캣북스 폴더(옛 파일은 처음에 옮긴다)."""
    anchor = connection_file()
    if not anchor:
        return ""
    from ax_agent_template import company_files

    if not company_files.folders_on():
        return anchor
    path = company_files.company_file(
        CONNECTION_NAME, source=Path(anchor), extras=(HISTORY_SUFFIX,)
    )
    return str(path)


def validate_shop_url(raw: str) -> str:
    """판매 서버 주소를 검사해 끝의 `/`를 뗀 값으로 돌려준다. 어긋나면 ValueError.

    http·https만, 호스트 필수, 계정(`user:pw@`)·쿼리·프래그먼트는 받지 않는다.
    """
    url = (raw or "").strip()
    if not url:
        raise ValueError("주소를 입력하세요")
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"주소가 너무 깁니다 ({MAX_URL_LENGTH}자 이하)")
    if any(c.isspace() for c in url):
        raise ValueError("주소에 공백이 있습니다")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("주소는 http:// 또는 https://로 시작해야 합니다")
    if not parts.hostname:
        raise ValueError("주소에 호스트가 없습니다")
    if parts.username is not None or parts.password is not None:
        raise ValueError("주소에 계정(user:pw@)을 넣지 마세요")
    if parts.query or parts.fragment or url.endswith(("?", "#")):
        raise ValueError("주소에 ?나 #을 넣지 마세요")
    try:
        _ = parts.port  # 숫자가 아니거나 범위를 벗어난 포트는 여기서 ValueError
    except ValueError as exc:
        raise ValueError("포트가 올바르지 않습니다") from exc
    return url.rstrip("/")


def allowed_hosts() -> frozenset[str]:
    """사내 밖에서 받을 판매 서버 호스트 (설치 운영자가 정한 쉼표 목록, 소문자)."""
    raw = os.getenv(ALLOWED_HOSTS_ENV, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _resolve(host: str) -> list[IpAddress]:
    """호스트를 IP로 푼다. IP면 그대로. 못 풀면 ValueError."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValueError(f"호스트를 찾을 수 없습니다 ({host})") from exc
    found: list[IpAddress] = []
    for info in infos:
        addr = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if addr not in found:
            found.append(addr)
    if not found:
        raise ValueError(f"호스트를 찾을 수 없습니다 ({host})")
    return found


def _blocked(addr: IpAddress) -> bool:
    """늘 막는 주소: 링크 로컬(클라우드 메타데이터 포함)·0.0.0.0·멀티캐스트."""
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_link_local or addr.is_unspecified or addr.is_multicast


_PRIVATE = tuple(
    ipaddress.ip_network(n)
    for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7")
)


def _private(addr: IpAddress) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in _PRIVATE)


def check_host(url: str) -> None:
    """판매 서버 주소의 호스트 정책. 어긋나면 ValueError (이유는 화면에 그대로 보인다).

    이름을 풀어 본 결과만 검사하므로, 실제로 부를 때는 `pinned_http.guarded_transport`처럼
    검사한 IP로만 연결하는 transport를 같이 써야 DNS 재바인딩을 막는다.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    check_addrs(host, parts.scheme, _resolve(host))


def check_addrs(host: str, scheme: str, addrs: list[IpAddress]) -> None:
    """이미 풀어 둔 IP 목록으로 `check_host`와 같은 정책을 본다. 어긋나면 ValueError."""
    host = host.lower()
    if any(_blocked(a) for a in addrs):
        raise ValueError("링크 로컬·메타데이터·0.0.0.0 주소는 연결할 수 없습니다")
    if all(_private(a) for a in addrs):
        return
    if host not in allowed_hosts():
        raise ValueError(f"사내 밖 주소는 허용 목록({ALLOWED_HOSTS_ENV})에 있어야 합니다")
    if scheme != "https":
        raise ValueError("사내 밖 주소는 https로만 연결할 수 있습니다")


def _parse(raw: Any, path: str) -> Connection:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 맨 위가 객체여야 한다")
    url = raw.get("shop_api_url")
    if not isinstance(url, str):
        raise ValueError(f"{path}: shop_api_url(문자열)이 필요하다")

    def text(key: str) -> str | None:
        value = raw.get(key)
        return value if isinstance(value, str) else None

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"{path}: enabled는 true·false여야 한다")
    url = validate_shop_url(url)
    check_host(url)  # 손으로 고친 파일도 정책을 벗어나면 쓰지 않는다
    kind = raw.get("kind", "shop_api")
    if kind not in KINDS:
        raise ValueError(f"{path}: kind는 {', '.join(KINDS)} 중 하나여야 한다")
    adapter = _adapter(raw.get("adapter"), path) if kind == "adapter" else None
    tenant = raw.get("tenant")
    if tenant is not None and not isinstance(tenant, str):
        raise ValueError(f"{path}: tenant는 글자여야 한다")
    return Connection(
        shop_api_url=url,
        verified_at=text("verified_at"),
        updated_at=text("updated_at"),
        updated_by=text("updated_by"),
        enabled=enabled,
        kind=kind,
        adapter=adapter,
        tenant=tenant or None,
    )


def _adapter(raw: Any, path: str) -> dict[str, str]:
    """kind=adapter의 고객사 API 설정. 주소는 판매 서버 주소와 같은 정책을 지난다."""
    base = raw.get("base_url") if isinstance(raw, dict) else None
    if not isinstance(base, str):
        raise ValueError(f"{path}: kind=adapter면 adapter.base_url(문자열)이 필요하다")
    base = validate_shop_url(base)
    check_host(base)
    return {"base_url": base}


# 바뀐 시각·크기가 같으면 다시 읽지 않는다. 깨진 파일 경고도 같은 상태면 한 번만 남긴다
_cache: tuple[str, int, int, Connection | None] | None = None


def read_connection() -> Connection | None:
    """저장된 회사 연결. 파일 경로가 없거나 파일이 없거나 깨졌으면 None."""
    global _cache
    path = connection_path()
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None  # 연결을 저장한 적 없음: 정상
    key = (path, stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[:3] == key:
        return _cache[3]
    try:
        found: Connection | None = _parse(json.loads(Path(path).read_text(encoding="utf-8")), path)
    except (OSError, ValueError) as exc:  # json.JSONDecodeError는 ValueError다
        logger.warning("회사 연결 파일이 깨져 쓰지 않는다: %s (%s)", path, exc)
        found = None
    _cache = (*key, found)
    return found


def shop_api_override() -> str | None:
    """화면에서 정한 판매 서버 주소. 없거나 꺼져 있으면(내부 데이터 모드) None.

    kind=adapter면 고객사 주소가 아니라 dashboard-api 사이트 변환기 주소를 준다.
    """
    found = read_connection()
    if not found or not found.enabled:
        return None
    if found.kind == "adapter":
        from ax_agent_template.config import agent_url  # config가 이 모듈을 import한다

        return agent_url("dashboard-api") + ADAPTER_PATH
    return found.shop_api_url


def company_hosts() -> frozenset[str]:
    """저장된 회사 연결이 가리키는 호스트들 (판매 서버·사이트 변환기 고객사 API, 소문자).

    AI들이 회사 판매 서버를 부를 때 이 호스트만 `pinned_http`로 검사한 IP에 붙인다.
    """
    found = read_connection()
    if not found:
        return frozenset()
    urls = [found.shop_api_url, *(found.adapter or {}).values()]
    return frozenset(h for u in urls if (h := (urlsplit(u).hostname or "").lower()))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _history_path() -> Path:
    path = connection_path()
    return Path(path).with_name(Path(path).name + HISTORY_SUFFIX)


def _record(action: str, before: Connection | None, after: Connection | None, by: str) -> None:
    """바꾼 기록 한 줄 (감사용). 쓰지 못해도 연결 변경은 막지 않고 경고만 남긴다."""
    line = {
        "at": _now(),
        "by": by,
        "action": action,
        "from": before.shop_api_url if before else None,
        "from_enabled": before.enabled if before else None,
        "to": after.shop_api_url if after else None,
        "to_enabled": after.enabled if after else None,
    }
    try:
        with _history_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("회사 연결 기록을 남기지 못했다: %s", exc)


def read_history(limit: int = 20) -> list[dict[str, Any]]:
    """최근 변경 기록 (새것부터). 없거나 읽을 수 없으면 빈 목록."""
    if not connection_file():
        return []
    try:
        lines = _history_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
        if len(out) >= limit:
            break
    return out


def save_connection(
    url: str,
    *,
    verified_at: str | None,
    updated_by: str,
    enabled: bool = True,
    kind: Kind = "shop_api",
    adapter: dict[str, str] | None = None,
    tenant: str | None = None,
) -> Connection:
    """연결을 저장한다(임시 파일에 쓰고 바꿔치기). 파일 경로가 없으면 RuntimeError."""
    path = connection_path()
    if not path:
        raise RuntimeError(f"{CONNECTION_FILE_ENV}가 설정되지 않아 저장할 수 없다")
    url = validate_shop_url(url)
    check_host(url)
    if kind not in KINDS:
        raise ValueError(f"kind는 {', '.join(KINDS)} 중 하나여야 합니다")
    adapter_conf = _adapter(adapter, CONNECTION_FILE_ENV) if kind == "adapter" else None
    if tenant is not None:
        from ax_agent_template.tenant import is_tenant, remember

        if not is_tenant(tenant):
            raise ValueError(f"회사 설정이 없습니다: {tenant}")
        remember(tenant)
    before = read_connection()
    saved = Connection(
        shop_api_url=url,
        verified_at=verified_at,
        updated_at=_now(),
        updated_by=updated_by,
        enabled=enabled,
        kind=kind,
        adapter=adapter_conf,
        tenant=tenant,
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(saved.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    action = "save" if before is None or before.shop_api_url != saved.shop_api_url else "mode"
    if action == "mode" and before is not None and before.enabled == saved.enabled:
        action = "recheck"
    _record(action, before, saved, updated_by)
    return saved


def clear_connection(updated_by: str = "unknown") -> bool:
    """연결 파일을 지운다. 지웠으면 True, 원래 없었으면 False."""
    path = connection_path()
    if not path:
        return False
    before = read_connection()
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    _record("clear", before, None, updated_by)
    return True
