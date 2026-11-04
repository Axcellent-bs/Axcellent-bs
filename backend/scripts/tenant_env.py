"""회사 설정 폴더(`config/tenants/<회사>/`)를 읽어 run_all.sh가 쓸 환경변수를 정한다.

회사 한 곳 = 카탈로그 + 커넥터 + 조직도. `tenant.toml` 한 장이 셋을 가리킨다.

    python scripts/tenant_env.py          # AX_TENANT(기본 catbooks)
    eval "$(python scripts/tenant_env.py)"

출력은 `export 이름=값` 줄이다. 이미 정한 값이 먼저다(지금 쓰는 사람이 깨지지 않게):
- 조직도 `AX_ORG_CONFIG`: 환경변수 → `config/org.yaml`(각자 덮어쓰기, 커밋 안 함) → 회사 폴더 `org`
- 카탈로그 `DATA_TENANT_CATALOG`: 환경변수 → 회사 폴더 `catalog`
- 재무 인당 월 단가 `FINANCE_PER_HEAD_MONTHLY`: 환경변수 → 회사 폴더 `[finance] per_head_monthly`
  (없으면 재무 기본값 `finance_policy.toml`)
- 검색어 급상승 기본 키워드 `DATA_TREND_CONFIG`: 환경변수 → 회사 폴더 `trends`(있을 때만)
- YouTube 스냅샷 회사 키워드 `DATA_YOUTUBE_CONFIG`: 환경변수 → 회사 폴더 `youtube`(있을 때만)
- 회사 시간대 `DATA_TENANT_TIMEZONE`: 환경변수 → 회사 폴더 `timezone`(있을 때만)
- 회사 설정 파일 `DATA_TENANT_CONFIG`: 환경변수 → 회사 폴더 `tenant.toml` (data 도메인 프로필이
  `[profile] about`·`[profile.override]`를 읽는다)
설정이 틀리면(폴더·카탈로그 없음, 커넥터가 카탈로그와 다름) 이유를 stderr에 쓰고 2로 끝난다.
"""

from __future__ import annotations

import os
import shlex
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
TENANT_ENV = "AX_TENANT"
DEFAULT_TENANT = "catbooks"
CATALOG_DIR = BACKEND / "domains" / "data" / "src" / "data_agent" / "catalogs"
NO_ORG = "조직도 없음: 꺼진 서비스로 가는 일은 no_assignee로 실패합니다"


class TenantError(ValueError):
    pass


def tenant_dir(name: str, root: Path = BACKEND / "config" / "tenants") -> Path:
    return root / name


def catalog_path(folder: Path, value: str) -> Path:
    """`catalog` 값: 이름이면 data 카탈로그 폴더, `.toml`이면 회사 폴더 기준 경로."""
    if value.endswith(".toml"):
        return (folder / value).resolve()
    return CATALOG_DIR / f"{value}.toml"


def read_tenant(folder: Path) -> dict[str, str]:
    """`tenant.toml`을 읽고 확인한다. 카탈로그 파일이 있고, 커넥터가 카탈로그와 같아야 한다."""
    path = folder / "tenant.toml"
    if not path.is_file():
        raise TenantError(f"회사 설정이 없습니다: {path}")
    with path.open("rb") as f:
        raw = tomllib.load(f)
    for key in ("catalog", "connector"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise TenantError(f"{path}: {key}(문자열)가 필요합니다")
    catalog = catalog_path(folder, raw["catalog"])
    if not catalog.is_file():
        raise TenantError(f"{path}: 카탈로그 파일이 없습니다: {catalog}")
    with catalog.open("rb") as f:
        in_catalog = tomllib.load(f).get("connector", "shop-api")
    if in_catalog != raw["connector"]:
        raise TenantError(
            f"{path}: 커넥터 {raw['connector']!r}가 카탈로그의 connector {in_catalog!r}와 다릅니다"
        )
    org = raw.get("org", "org.yaml")
    finance = raw.get("finance") or {}
    per_head = finance.get("per_head_monthly") if isinstance(finance, dict) else None
    if per_head is not None and (
        isinstance(per_head, bool) or not isinstance(per_head, int) or per_head < 0
    ):
        raise TenantError(f"{path}: [finance] per_head_monthly는 0 이상 정수여야 합니다")
    trends = raw.get("trends")
    youtube = raw.get("youtube")
    timezone = raw.get("timezone")
    return {
        "name": str(raw.get("name") or folder.name),
        "catalog": raw["catalog"] if not raw["catalog"].endswith(".toml") else catalog.as_posix(),
        "connector": raw["connector"],
        "org": (folder / org).resolve().as_posix() if isinstance(org, str) and org else "",
        "per_head_monthly": "" if per_head is None else str(per_head),
        "trends": (folder / trends).resolve().as_posix()
        if isinstance(trends, str) and trends
        else "",
        "youtube": (folder / youtube).resolve().as_posix()
        if isinstance(youtube, str) and youtube
        else "",
        "timezone": timezone if isinstance(timezone, str) else "",
    }


def resolve(
    env: Mapping[str, str],
    *,
    root: Path = BACKEND / "config" / "tenants",
    local_org: Path = BACKEND / "config" / "org.yaml",
) -> tuple[dict[str, str], list[str]]:
    """(내보낼 환경변수, 안내 줄). 이미 정한 값은 내보내지 않는다."""
    name = (env.get(TENANT_ENV) or DEFAULT_TENANT).strip()
    folder = tenant_dir(name, root)
    if not folder.is_dir():
        raise TenantError(f"회사 설정 폴더가 없습니다: {folder} ({TENANT_ENV}={name})")
    tenant = read_tenant(folder)
    out: dict[str, str] = {TENANT_ENV: name}
    notes = [f"회사: {name} ({tenant['name']}), 커넥터 {tenant['connector']}"]

    if env.get("AX_ORG_CONFIG"):
        org, why = env["AX_ORG_CONFIG"], "환경변수"
    elif local_org.is_file():
        org, why = local_org.resolve().as_posix(), "config/org.yaml"
        out["AX_ORG_CONFIG"] = org
    else:
        org, why = tenant["org"], "회사 폴더"
        if org:
            out["AX_ORG_CONFIG"] = org
    if org and Path(org).is_file():
        notes.append(f"조직도: {org} ({why})")
    else:
        out.pop("AX_ORG_CONFIG", None)
        notes.append(f"{NO_ORG} (찾은 곳: {org or '없음'})")

    if env.get("DATA_TENANT_CATALOG"):
        notes.append(f"카탈로그: {env['DATA_TENANT_CATALOG']} (환경변수)")
    else:
        out["DATA_TENANT_CATALOG"] = tenant["catalog"]
        notes.append(f"카탈로그: {tenant['catalog']} (회사 폴더)")

    if env.get("FINANCE_PER_HEAD_MONTHLY"):
        notes.append(f"재무 인당 월 단가: {env['FINANCE_PER_HEAD_MONTHLY']}원 (환경변수)")
    elif tenant["per_head_monthly"]:
        out["FINANCE_PER_HEAD_MONTHLY"] = tenant["per_head_monthly"]
        notes.append(f"재무 인당 월 단가: {tenant['per_head_monthly']}원 (회사 폴더)")

    if env.get("DATA_TREND_CONFIG"):
        notes.append(f"검색어 급상승 키워드: {env['DATA_TREND_CONFIG']} (환경변수)")
    elif tenant["trends"] and Path(tenant["trends"]).is_file():
        out["DATA_TREND_CONFIG"] = tenant["trends"]
        notes.append(f"검색어 급상승 키워드: {tenant['trends']} (회사 폴더)")

    if env.get("DATA_YOUTUBE_CONFIG"):
        notes.append(f"YouTube 회사 키워드: {env['DATA_YOUTUBE_CONFIG']} (환경변수)")
    elif tenant["youtube"] and Path(tenant["youtube"]).is_file():
        out["DATA_YOUTUBE_CONFIG"] = tenant["youtube"]
        notes.append(f"YouTube 회사 키워드: {tenant['youtube']} (회사 폴더)")

    if env.get("DATA_TENANT_TIMEZONE"):
        notes.append(f"회사 시간대: {env['DATA_TENANT_TIMEZONE']} (환경변수)")
    elif tenant["timezone"]:
        out["DATA_TENANT_TIMEZONE"] = tenant["timezone"]
        notes.append(f"회사 시간대: {tenant['timezone']} (회사 폴더)")

    if env.get("DATA_TENANT_CONFIG"):
        notes.append(f"회사 설정 파일: {env['DATA_TENANT_CONFIG']} (환경변수)")
    else:
        out["DATA_TENANT_CONFIG"] = (folder / "tenant.toml").resolve().as_posix()
        notes.append("회사 설정 파일: tenant.toml (회사 폴더)")
    return out, notes


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔에서도 한글이 깨지지 않게
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    try:
        out, notes = resolve(os.environ)
    except (TenantError, OSError, tomllib.TOMLDecodeError) as error:
        print(f"회사 설정 오류: {error}", file=sys.stderr)
        return 2
    for line in notes:
        print(line, file=sys.stderr)
    for key, value in out.items():
        print(f"export {key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
