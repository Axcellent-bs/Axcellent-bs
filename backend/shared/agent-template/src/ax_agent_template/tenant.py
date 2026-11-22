"""지금 회사: 시스템 관리 › 회사 연결에서 고른 회사의 설정 폴더(`config/tenants/<회사>/`).

캣북스 기본값은 없다. 회사를 연결하기 전에는 어느 회사도 아니다(`current()`가 None).
고르는 순서: `AX_TENANT`(설치하는 사람이 정한 값) → 회사 연결 파일의 `tenant` → 없음.

`setting(이름)`은 run_all이 내보내던 회사 설정 환경변수와 같은 이름으로 값을 준다.
환경변수를 정했으면 그것, 아니면 지금 회사 폴더 `tenant.toml`에서 찾는다(재시작 없이 따라감).
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
import unicodedata
from pathlib import Path
from typing import Any

TENANT_ENV = "AX_TENANT"
TENANTS_DIR_ENV = "AX_TENANTS_DIR"
DEFAULT_TENANTS_DIR = Path(__file__).resolve().parents[4] / "config" / "tenants"
TENANT_FILE = "tenant.toml"
# 이 이름들은 환경변수가 없으면 회사 폴더에서 찾는다
DERIVED = (
    "AX_ORG_CONFIG",
    "DATA_TENANT_CATALOG",
    "DATA_TREND_CONFIG",
    "DATA_YOUTUBE_CONFIG",
    "DATA_TENANT_TIMEZONE",
    "DATA_TENANT_CONFIG",
    "FINANCE_PER_HEAD_MONTHLY",
)
# tenant.toml 키 → 회사 폴더 안 파일 경로인 것
_FILE_KEYS = {
    "AX_ORG_CONFIG": "org",
    "DATA_TREND_CONFIG": "trends",
    "DATA_YOUTUBE_CONFIG": "youtube",
}

logger = logging.getLogger("ax.tenant")
_cache: dict[Path, tuple[int, dict[str, Any]]] = {}


def tenants_dir() -> Path:
    """회사 폴더들이 있는 곳: `AX_TENANTS_DIR` → 레포 backend/config/tenants → 작업 폴더.

    도커 이미지는 패키지를 site-packages에 깔아(--no-editable) 레포 경로가 맞지 않는다.
    그때는 작업 폴더(WORKDIR /app = backend)의 config/tenants를 본다.
    """
    raw = os.getenv(TENANTS_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    if DEFAULT_TENANTS_DIR.is_dir():
        return DEFAULT_TENANTS_DIR
    return Path.cwd() / "config" / "tenants"


def _read(folder: Path) -> dict[str, Any] | None:
    path = folder / TENANT_FILE
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("회사 설정을 읽지 못했다: %s (%s)", path, type(exc).__name__)
        return None
    _cache[path] = (stamp, raw)
    return raw


def list_tenants() -> list[dict[str, str]]:
    """고를 수 있는 회사: `tenant.toml`이 있는 폴더. [{id, name}]."""
    root = tenants_dir()
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    out = []
    for folder in folders:
        raw = _read(folder)
        if raw is not None:
            out.append({"id": folder.name, "name": str(raw.get("name") or folder.name)})
    return out


def is_tenant(name: str) -> bool:
    return (
        bool(name)
        and "/" not in name
        and "\\" not in name
        and name not in (".", "..")
        and (_read(tenants_dir() / name) is not None)
    )


def current() -> str | None:
    """지금 회사 id. 연결 전·모르는 회사면 None."""
    from ax_agent_template.company_connection import read_connection

    name = os.getenv(TENANT_ENV, "").strip()
    if not name:
        found = read_connection()
        name = (found.tenant or "") if found else ""
    if not name:
        name = _remembered()
    return name if is_tenant(name) else None


# 설치 기록: 회사 설정은 연결이 아니라 설치에 딸린다. 연결을 끊었다 다시 저장해도 유지되게
# 회사 연결 파일 옆 `install_tenant.json`에 마지막으로 정한 회사를 남긴다
# (11/18 연결 해제로 회사 설정이 사라짐)
INSTALL_TENANT_FILE = "install_tenant.json"


def _install_file() -> Path | None:
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name(INSTALL_TENANT_FILE) if conn else None


def _remembered() -> str:
    import json

    path = _install_file()
    if path is None:
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    name = data.get("tenant") if isinstance(data, dict) else None
    return name if isinstance(name, str) else ""


def remember(name: str) -> None:
    """이 설치의 회사 설정을 남긴다(회사 연결을 저장할 때)."""
    import json

    path = _install_file()
    if path is None or not is_tenant(name):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"tenant": name}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def setting(env_name: str) -> str:
    """회사 설정 값. 환경변수 → 지금 회사 폴더. 없으면 ""."""
    given = os.getenv(env_name, "").strip()
    if given or env_name not in DERIVED:
        return given
    name = current()
    if name is None:
        return ""
    folder = (tenants_dir() / name).resolve()
    raw = _read(folder) or {}
    if env_name in _FILE_KEYS:
        value = raw.get(_FILE_KEYS[env_name])
        if not isinstance(value, str) or not value:
            return ""
        path = (folder / value).resolve()
        return path.as_posix() if path.is_file() else ""
    if env_name == "DATA_TENANT_CATALOG":
        catalog = raw.get("catalog")
        if not isinstance(catalog, str):
            return ""
        return (folder / catalog).resolve().as_posix() if catalog.endswith(".toml") else catalog
    if env_name == "DATA_TENANT_TIMEZONE":
        tz = raw.get("timezone")
        return tz if isinstance(tz, str) else ""
    if env_name == "DATA_TENANT_CONFIG":
        return (folder / TENANT_FILE).as_posix()
    finance = raw.get("finance")
    per_head = finance.get("per_head_monthly") if isinstance(finance, dict) else None
    ok = isinstance(per_head, int) and not isinstance(per_head, bool) and per_head >= 0
    return str(per_head) if ok else ""


# ---------------------------------------------------------------- 영상 브랜드 (주인공·배경·화풍)
# 회사 설정 `tenant.toml [brand]` (모두 선택). 기획 영상 제작안·마케팅 영상 프롬프트가 쓴다.
# 칸마다 길이 상한(영문 120자·한글 60자)과 금칙(글자 요청·실존 인물·브랜드·숫자·광고·주입어,
# 마케팅 scene_templates.toml과 같은 목록)을 보고, 걸리거나 없으면 그 칸만 기본값을 쓴다.
BRAND_DEFAULTS: dict[str, str] = {
    "character_en": "original illustrated characters, not real people",
    "character_ko": "원본 일러스트 캐릭터",
    "setting_en": "a setting that fits the company's products",
    "setting_ko": "회사 상품에 어울리는 배경",
    "style_en": "flat illustration style",
}
_BRAND_MAX = {"en": 120, "ko": 60}
_BRAND_WORDS = (
    # 글자 요청
    "text", "word", "words", "letter", "letters", "caption", "title", "slogan", "headline",
    "logo", "watermark", "sign", "signage", "writing", "written", "saying", "says", "typography",
    "subtitle", "subtitles", "글자", "문구", "텍스트", "자막", "제목", "로고", "간판", "워터마크",
    "슬로건",
    # 실존 인물
    "celebrity", "famous", "lookalike", "look-alike", "portrait of", "real person", "politician",
    "actor", "actress", "idol", "influencer",
    "닮은", "유명인", "연예인", "정치인", "실존", "아이돌",
    # 실제 브랜드
    "nike", "adidas", "apple", "google", "samsung", "disney", "pixar", "ghibli", "marvel",
    "starbucks", "coca-cola", "netflix", "youtube", "instagram", "tiktok", "kakao", "naver",
    "yes24", "kyobo", "aladin", "harry potter", "삼성", "애플", "디즈니", "지브리", "카카오",
    "네이버", "교보", "알라딘", "스타벅스", "해리포터", "유튜브", "인스타그램", "틱톡",
    # 광고(과장·단정)
    "best", "no.1", "number one", "guaranteed", "guarantee", "cure", "free", "cheapest", "only",
    "최고", "1위", "최저가", "무조건", "보장", "완치", "유일", "최초", "공짜", "특효",
    # 주입
    "ignore", "instruction", "instructions", "system", "prompt", "assistant", "override",
    "jailbreak", "무시", "지시", "명령", "시스템", "프롬프트", "어시스턴트",
)  # fmt: skip
_BRAND_TEXT = re.compile(r"[A-Za-z가-힣 ,'-]+")


def _brand_ok(key: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = unicodedata.normalize("NFKC", value).strip()
    if not text or len(text) > _BRAND_MAX[key.rsplit("_", 1)[1]]:
        return False
    if not _BRAND_TEXT.fullmatch(text):  # 숫자·주소·기호는 받지 않는다
        return False
    low = text.lower()
    for w in _BRAND_WORDS:
        if w.isascii():
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low):
                return False
        elif w in low:
            return False
    return True


def brand() -> dict[str, str]:
    """지금 회사의 영상 브랜드 5칸(character_en·character_ko·setting_en·setting_ko·style_en).

    회사가 없거나 `[brand]`가 없거나 칸이 금칙·길이에 걸리면 그 칸은 `BRAND_DEFAULTS`."""
    name = current()
    raw = (_read(tenants_dir() / name) or {}) if name else {}
    given = raw.get("brand")
    given = given if isinstance(given, dict) else {}
    out = {}
    for key, default in BRAND_DEFAULTS.items():
        value = given.get(key)
        out[key] = " ".join(str(value).split()) if _brand_ok(key, value) else default
    return out


# ---------------------------------------------------------------- 인스타 레이더 계정
# 회사 연결 › 인스타 레이더 계정에서 저장한 목록.
# 있으면 회사 설정 trends.toml [instagram] radar_accounts보다 먼저다.
# 파일: `AX_IG_RADAR_FILE` → 회사 연결 파일 옆 `instagram_radar.json`.
# dashboard-api가 쓰고 data가 읽는다
IG_RADAR_FILE_ENV = "AX_IG_RADAR_FILE"
IG_RADAR_FILE = "instagram_radar.json"


def ig_radar_file() -> Path | None:
    raw = os.getenv(IG_RADAR_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name(IG_RADAR_FILE) if conn else None


def ig_radar_accounts() -> list[str] | None:
    """화면에서 저장한 레이더 계정. 저장한 적 없거나 깨졌으면 None(회사 설정 파일을 쓴다)."""
    import json

    path = ig_radar_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        return None
    return [a for a in accounts if isinstance(a, str) and a]


# ---------------------------------------------------------------- 인스타 레이더 자동 목록
# data 경쟁사 찾기(company_accounts.find)가 business_discovery로 확인한 계정 + 사람 include
# (exclude 뺌, 팔로워 큰 순). 화면 저장 목록도 회사 trends.toml 목록도 없을 때만 레이더가 쓴다.
# 파일: `AX_IG_RADAR_AUTO_FILE` → 회사 연결 파일 옆 `instagram_radar_auto.json`
# (ig_radar_file과 같은 자리 규칙). 회사 연결 파일이 없으면 None이고 쓰지 않는다.
# data가 쓰고 data·dashboard-api가 읽는다
IG_RADAR_AUTO_FILE_ENV = "AX_IG_RADAR_AUTO_FILE"
IG_RADAR_AUTO_FILE = "instagram_radar_auto.json"


def ig_radar_auto_file() -> Path | None:
    raw = os.getenv(IG_RADAR_AUTO_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name(IG_RADAR_AUTO_FILE) if conn else None


def ig_radar_auto_accounts() -> list[str] | None:
    """경쟁사 찾기가 남긴 자동 목록. 파일이 없거나 깨졌거나 다른 회사 것이면 None."""
    import json

    path = ig_radar_auto_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        return None
    owner = data.get("tenant")
    if isinstance(owner, str) and owner and owner != (current() or owner):
        return None  # 회사를 바꿨으면 예전 회사의 경쟁사를 쓰지 않는다
    return [a for a in data["accounts"] if isinstance(a, str) and a]


def write_ig_radar_auto(payload: dict[str, Any]) -> Path | None:
    """자동 목록 파일을 쓴다(통째로 바꿈). 자리가 없으면 쓰지 않고 None."""
    import json

    path = ig_radar_auto_file()
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
