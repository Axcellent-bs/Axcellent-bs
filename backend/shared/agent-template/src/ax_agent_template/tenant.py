"""지금 회사: 시스템 관리 › 회사 연결에서 고른 회사의 설정 폴더(`config/tenants/<회사>/`).

캣북스 기본값은 없다. 회사를 연결하기 전에는 어느 회사도 아니다(`current()`가 None).
고르는 순서: `AX_TENANT`(설치하는 사람이 정한 값) → 회사 연결 파일의 `tenant` → 없음.
캣북스가 아닌 회사 요청(`company_scope.active()`)은 `AX_TENANT`·설치 기록을 건너뛰고
그 회사 연결의 `tenant`만 본다(여러 회사 설계 6.2).

`setting(이름)`은 run_all이 내보내던 회사 설정 환경변수와 같은 이름으로 값을 준다.
환경변수를 정했으면 그것, 아니면 지금 회사 폴더 `tenant.toml`에서 찾는다(재시작 없이 따라감).
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
import unicodedata
from collections.abc import Callable
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
    from ax_agent_template.company_connection import company_of, read_connection
    from ax_agent_template.company_scope import CATBOOKS

    catbooks = company_of() == CATBOOKS
    # `AX_TENANT`·설치 기록은 설치 하나에 하나라 캣북스 것이다(`AX_SITE_API_TOKEN`과 같은 규칙).
    # 다른 회사 요청(company_scope.active())은 그 회사 연결의 회사 설정만 본다(설정이 섞이지 않게)
    name = os.getenv(TENANT_ENV, "").strip() if catbooks else ""
    if not name:
        found = read_connection()  # 지금 요청 회사의 연결 (요청 밖이면 캣북스)
        name = (found.tenant or "") if found else ""
    if not name and catbooks:
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


# ---------------------------------------------------------------- 홍보 영상 방향 (11/24)
# "재미 먼저, 브랜드는 끝에": 대상층이 좋아하는 형식·소재로 웃기고 브랜드는 끝에 붙인다.
# 같은 `[brand]` 섹션의 두 칸(promo_style·brand_exposure). brand() 5칸과 따로 읽는다
# (5칸은 영상 프롬프트에 그대로 들어가는 글이라 금칙·길이 규칙이 다르다).
# 고르는 순서: 화면 저장(시스템 관리 › 회사 연결 › 브랜드, 회사별) → tenant.toml [brand] → 기본값.
# 화면 저장 파일: `AX_PROMO_FILE` → 회사 연결 파일 옆 `promo_style.json`
# ({"companies": {"<회사>": {"promo_style": …, "brand_exposure": …}}}).
# dashboard-api가 쓰고 data가 읽는다
PROMO_STYLES = ("fun_first", "product")
BRAND_EXPOSURES = ("end_card", "prop", "caption")
PROMO_DEFAULTS: dict[str, str] = {"promo_style": "fun_first", "brand_exposure": "end_card"}
_PROMO_CHOICES = {"promo_style": PROMO_STYLES, "brand_exposure": BRAND_EXPOSURES}
PROMO_FILE_ENV = "AX_PROMO_FILE"
PROMO_FILE = "promo_style.json"


def promo_file() -> Path | None:
    raw = os.getenv(PROMO_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name(PROMO_FILE) if conn else None


def promo_saved_all() -> dict[str, dict[str, str]]:
    """화면 저장 파일 전체 (회사 → 칸). 없거나 깨졌으면 빈 dict. 모르는 값은 뺀다."""
    import json

    path = promo_file()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    companies = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(companies, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, row in companies.items():
        if isinstance(name, str) and isinstance(row, dict):
            good = {k: v for k, v in row.items() if k in _PROMO_CHOICES and v in _PROMO_CHOICES[k]}
            if good:
                out[name] = good
    return out


def promo_tenant(name: str | None = None) -> dict[str, str]:
    """회사 설정 파일 tenant.toml [brand]의 두 칸 (모르는 값·없는 칸은 뺀다)."""
    name = name if name is not None else current()
    raw = (_read(tenants_dir() / name) or {}) if name else {}
    given = raw.get("brand")
    given = given if isinstance(given, dict) else {}
    return {k: v for k, v in given.items() if k in _PROMO_CHOICES and v in _PROMO_CHOICES[k]}


def promo(name: str | None = None) -> dict[str, str]:
    """회사의 홍보 영상 방향 두 칸 {promo_style, brand_exposure}. 칸마다 화면 저장 → 파일 → 기본값.

    다른 회사의 화면 저장 값은 쓰지 않는다(회사 이름으로 나눠 저장)."""
    name = name if name is not None else current()
    saved = promo_saved_all().get(name, {}) if name else {}
    from_file = promo_tenant(name)
    return {k: saved.get(k) or from_file.get(k) or d for k, d in PROMO_DEFAULTS.items()}


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


def ig_radar_meme_accounts() -> list[str] | None:
    """화면에서 저장한 밈·크리에이터 계정 (같은 파일의 `meme_accounts`, 11/24).

    저장한 적 없으면 None(회사 설정 trends.toml [instagram] radar_meme_accounts를 쓴다)."""
    import json

    path = ig_radar_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    accounts = data.get("meme_accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        return None
    return [a for a in accounts if isinstance(a, str) and a]


# ---------------------------------------------------------------- 유튜브 밈 키워드 (11/24)
# 회사 연결 › 유튜브 밈 키워드에서 저장한 목록. 있으면 회사 설정 trends.toml [youtube] meme_keywords
# 보다 먼저다. 파일: `AX_YT_MEME_KEYWORDS_FILE` → 회사 연결 파일 옆 `youtube_meme_keywords.json`.
# dashboard-api가 쓰고 data가 읽는다
YT_MEME_KEYWORDS_FILE_ENV = "AX_YT_MEME_KEYWORDS_FILE"
YT_MEME_KEYWORDS_FILE = "youtube_meme_keywords.json"


def yt_meme_keywords_file() -> Path | None:
    raw = os.getenv(YT_MEME_KEYWORDS_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name(YT_MEME_KEYWORDS_FILE) if conn else None


def yt_meme_keywords() -> list[str] | None:
    """화면에서 저장한 유튜브 밈 키워드. 저장한 적 없거나 깨졌으면 None(회사 설정 파일을 쓴다)."""
    import json

    path = yt_meme_keywords_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    words = data.get("keywords") if isinstance(data, dict) else None
    if not isinstance(words, list):
        return None
    return [w for w in words if isinstance(w, str) and w.strip()]


# ---------------------------------------------------------------- 인스타 레이더 자동 목록
# data 경쟁사 찾기(company_accounts.find)가 business_discovery로 확인한 계정 + 사람 include
# (exclude 뺌, 팔로워 큰 순). 화면 저장 목록도 회사 trends.toml 목록도 없을 때만 레이더가 쓴다.
# 파일: `AX_IG_RADAR_AUTO_FILE` → 회사 폴더를 켰으면
# `<회사 폴더>/<지금 회사>/instagram_radar_auto.json`
# (여러 회사 1-5, 11/23: 회사 데이터라 회사 폴더. 옛 자리 = 회사 연결 파일 옆은 처음 읽을 때
# company_files 이관 규칙으로 옮기고 `.moved`를 남긴다. `AX_COMPANY_FOLDERS=off`면 옛 자리로 되살림)
# → 아니면 회사 연결 파일 옆. 회사 연결 파일이 없으면 None이고 쓰지 않는다.
# data가 쓰고 data·dashboard-api가 읽는다
IG_RADAR_AUTO_FILE_ENV = "AX_IG_RADAR_AUTO_FILE"
IG_RADAR_AUTO_FILE = "instagram_radar_auto.json"


def ig_radar_auto_file() -> Path | None:
    from ax_agent_template import company_files
    from ax_agent_template.companies import canonical_company_id
    from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

    cid = canonical_company_id(current()) or TENANT_ID_CATBOOKS
    raw = os.getenv(IG_RADAR_AUTO_FILE_ENV, "").strip()
    if raw:
        company_files.restore_old(IG_RADAR_AUTO_FILE, target=Path(raw), company_id=cid)
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    legacy = Path(conn).with_name(IG_RADAR_AUTO_FILE) if conn else None
    if company_files.folders_on():
        return company_files.company_file(IG_RADAR_AUTO_FILE, source=legacy, company_id=cid)
    if legacy is not None:  # 되돌리기(off): 옛 자리가 비어 있으면 회사 폴더 값으로 되살린다
        company_files.restore_old(IG_RADAR_AUTO_FILE, target=legacy, company_id=cid)
    return legacy


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


# ---------------------------------------------------------------- 인스타 레이더 계정 메타 (11/24)
# 계정마다 kind(industry·creator·meme·brand)·ages·genders·topic·tag_source(ai·human)·followers.
# 파일: `AX_IG_RADAR_META_FILE` → 회사 폴더를 켰으면 `<회사 폴더>/<회사>/instagram_radar_meta.json`
# → 아니면 회사 연결 파일 옆. 파일 안에 회사 id를 두고 다른 회사 것이면 읽지 않는다.
# data(AI 태그·팔로워)와 dashboard-api(사람 수정)가 같이 쓴다: 읽고 바로 고쳐 쓴다
# (`update_ig_radar_meta`).
# 사람이 고친 줄(tag_source=human)은 AI가 덮지 않는다(data 쪽 규칙)
IG_RADAR_META_FILE_ENV = "AX_IG_RADAR_META_FILE"
IG_RADAR_META_FILE = "instagram_radar_meta.json"


def _meta_company(company_id: str | None) -> str:
    from ax_agent_template.companies import canonical_company_id
    from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

    return canonical_company_id(company_id or current()) or TENANT_ID_CATBOOKS


def ig_radar_meta_file(company_id: str | None = None) -> Path | None:
    from ax_agent_template import company_files

    raw = os.getenv(IG_RADAR_META_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    legacy = Path(conn).with_name(IG_RADAR_META_FILE) if conn else None
    if company_files.folders_on():
        return company_files.company_file(IG_RADAR_META_FILE, source=legacy,
                                          company_id=_meta_company(company_id))  # fmt: skip
    return legacy


def ig_radar_meta(company_id: str | None = None) -> dict[str, dict[str, Any]]:
    """계정 → 메타 줄. 파일이 없거나 깨졌거나 다른 회사 것이면 빈 표."""
    import json

    path = ig_radar_meta_file(company_id)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("company_id") != _meta_company(company_id):
        return {}
    rows = data.get("accounts")
    if not isinstance(rows, dict):
        return {}
    return {str(k): v for k, v in rows.items() if isinstance(v, dict)}


def ig_radar_meta_state(company_id: str | None = None) -> dict[str, Any]:
    """파일 전체 (accounts 말고 `ai_day` 같은 칸도). 없거나 다른 회사 것이면 빈 표."""
    import json

    path = ig_radar_meta_file(company_id)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ok = isinstance(data, dict) and data.get("company_id") == _meta_company(company_id)
    return data if ok else {}


def update_ig_radar_meta(change: Callable[[dict[str, Any]], dict[str, Any]],
                         company_id: str | None = None) -> Path | None:  # fmt: skip
    """파일을 읽어 `change`(전체 dict → 새 dict)로 고쳐 바로 바꿔 쓴다. 자리가 없으면 None."""
    import json

    path = ig_radar_meta_file(company_id)
    if path is None:
        return None
    cid = _meta_company(company_id)
    data = ig_radar_meta_state(company_id) or {"company_id": cid, "accounts": {}}
    if not isinstance(data.get("accounts"), dict):
        data["accounts"] = {}
    data = change(data)
    data["company_id"] = cid
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
