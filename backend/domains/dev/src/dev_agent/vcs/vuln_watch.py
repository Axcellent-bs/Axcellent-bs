"""취약점 소식 맞춰 보기 (연결 보안 제안 3번, #소통 11/03 합의).

연결 대장(`inventory.py`)의 사내 설치형 서버 버전을 취약점 소식과 맞춰 보고 판정한다.

판정
- `emergency` 긴급: CISA KEV(실제 악용 중)에 있고 그 CVE의 NVD 취약 범위에 이 버전이 든다
  → 안전 서비스에 "이 연결 쓰기 보류"를 근거(CVE·버전·판정 시각)와 함께 알린다
- `warning` 경고: NVD에서 이 제품·버전에 걸리는 CVE가 있다 (KEV 아님) → 개발 화면에만 표시
- `clear` 해당 없음: 둘 다 없다 → 기록만
- `feed_failed` 점검 실패: 소식을 못 받았다 → "점검 실패"만, 쓰기는 막지 않는다
  (관문 fail-closed의 예외, 태윤 합의)

- 클라우드(github.com 등, 제공사가 패치)와 버전을 모르는 연결은 맞춰 보지 않는다.
- 긴급이던 연결이 패치되어 긴급이 아니게 되면 "보류 해제"를 알린다.
- 안전 서비스 알림: `POST {safety}/api/safety/connection-holds` (`ConnectionHoldNotice`).
  받는 쪽은 태윤 님이 만든다. 받지 못하면(창구 없음·연결 실패) 대기로 두고 다음 점검 때 다시 보낸다.
  총괄 사건으로는 열지 않고 안전 서비스의 보류 흐름(`safety.action_held`)으로 사람에게 간다
  (준하 합의).
- 소식 출처: CISA KEV JSON(하루 한 번), NVD CVE API 2.0 (`NVD_API_KEY`가 있으면 더 빨리 부른다).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

Verdict = Literal["emergency", "warning", "clear", "feed_failed", ""]

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


@dataclass(frozen=True)
class Product:
    """연결 대장 제품 키 → KEV 이름·NVD CPE 앞부분."""

    kev_vendor: str
    kev_product: str  # KEV product에 이 글자가 들어 있으면 같은 제품으로 본다
    cpe: str  # cpe:2.3:a:<vendor>:<product>


# 사내 설치형만 (클라우드는 제공사가 패치). 새 연결 종류는 여기에 더한다
PRODUCTS: dict[str, Product] = {
    "ghes": Product("GitHub", "Enterprise Server", "cpe:2.3:a:github:enterprise_server"),
    "gitlab": Product("GitLab", "GitLab", "cpe:2.3:a:gitlab:gitlab"),
    "jenkins": Product("Jenkins", "Jenkins", "cpe:2.3:a:jenkins:jenkins"),
    "argocd": Product("Argo", "Argo CD", "cpe:2.3:a:argoproj:argo_cd"),
}


class ConnectionHoldNotice(BaseModel):
    """개발 → 안전 서비스: 이 연결의 쓰기를 보류(또는 해제)해 달라.

    받는 쪽(안전 서비스)이 생기면 `ax_contracts`로 옮긴다. 비밀 값은 넣지 않는다."""

    connection_id: str
    hold: bool  # True = 보류, False = 해제
    product: str
    version: str
    kev_cves: list[str] = Field(default_factory=list)  # 근거 CVE (KEV + 취약 범위)
    judged_at: str  # 판정 시각 (ISO)
    reason: str


@dataclass
class Judgement:
    verdict: Verdict = ""
    kev_cves: list[str] = field(default_factory=list)
    cves: list[str] = field(default_factory=list)  # 경고: NVD에서 걸린 CVE (앞 몇 개)
    cve_total: int = 0
    checked_at: str = ""
    error: str = ""


# ---------------------------------------------------------------- 버전 범위

_NUM = re.compile(r"\d+(?:\.\d+)*")


def _ver(v: str) -> tuple[int, ...] | None:
    m = _NUM.search(v or "")
    return tuple(int(x) for x in m.group(0).split(".")) if m else None


def _cmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def in_range(version: str, match: dict[str, Any], cpe: str) -> bool:
    """NVD cpeMatch 한 줄이 이 제품·버전을 취약하다고 보는가."""
    crit = str(match.get("criteria") or "")
    if not match.get("vulnerable", True) or not crit.startswith(cpe + ":"):
        return False
    v = _ver(version)
    if v is None:
        return False
    exact = crit[len(cpe) + 1 :].split(":", 1)[0]
    if exact not in ("*", "-", ""):
        e = _ver(exact)
        return e is not None and _cmp(v, e) == 0
    bounds = (
        ("versionStartIncluding", lambda c: c >= 0),
        ("versionStartExcluding", lambda c: c > 0),
        ("versionEndIncluding", lambda c: c <= 0),
        ("versionEndExcluding", lambda c: c < 0),
    )
    seen = False
    for key, ok in bounds:
        raw = match.get(key)
        if raw:
            b = _ver(str(raw))
            if b is None or not ok(_cmp(v, b)):
                return False
            seen = True
    return seen


def cve_hits(version: str, cve: dict[str, Any], cpe: str) -> bool:
    """NVD CVE 하나(`vulnerabilities[].cve`)의 설정 중 하나라도 이 버전에 걸리는가."""
    for conf in cve.get("configurations") or []:
        for node in conf.get("nodes") or []:
            if any(in_range(version, m, cpe) for m in node.get("cpeMatch") or []):
                return True
    return False


# ---------------------------------------------------------------- 소식 가져오기

Fetch = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def _http_fetch(url: str, params: dict[str, Any]) -> dict[str, Any]:
    headers = {"User-Agent": "axcellent-dev-vuln-watch"}
    key = os.getenv("NVD_API_KEY")
    if key and url.startswith(NVD_URL):
        headers["apiKey"] = key
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.get(url, params=params, headers=headers)
    res.raise_for_status()
    return res.json()


class Feed:
    """KEV·NVD 소식. 하루 안에 같은 것은 다시 부르지 않는다 (메모리 캐시)."""

    def __init__(self, fetch: Fetch | None = None, *, nvd_gap: float | None = None):
        self._fetch = fetch or _http_fetch
        # 키 없는 NVD는 30초에 5번까지 → 부를 때마다 조금 쉰다
        self._gap = nvd_gap if nvd_gap is not None else (0.7 if os.getenv("NVD_API_KEY") else 6.5)
        self._kev: list[dict[str, Any]] | None = None
        self._cves: dict[str, dict[str, Any]] = {}
        self._matches: dict[str, tuple[int, list[str]]] = {}
        self._lock = asyncio.Lock()
        self._last_nvd = 0.0

    def clear(self) -> None:
        self._kev, self._cves, self._matches = None, {}, {}

    async def _nvd(self, params: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._last_nvd + self._gap - loop.time()
            if wait > 0 and self._last_nvd:
                await asyncio.sleep(wait)
            try:
                return await self._fetch(NVD_URL, params)
            finally:
                self._last_nvd = loop.time()

    async def kev(self) -> list[dict[str, Any]]:
        if self._kev is None:
            data = await self._fetch(KEV_URL, {})
            self._kev = list(data.get("vulnerabilities") or [])
        return self._kev

    async def cve(self, cve_id: str) -> dict[str, Any]:
        if cve_id not in self._cves:
            data = await self._nvd({"cveId": cve_id})
            vulns = data.get("vulnerabilities") or [{}]
            self._cves[cve_id] = dict(vulns[0].get("cve") or {})
        return self._cves[cve_id]

    async def matches(self, cpe: str, version: str) -> tuple[int, list[str]]:
        """이 제품·버전에 걸리는 NVD CVE 수와 앞 몇 개 id."""
        key = f"{cpe}:{version}"
        if key not in self._matches:
            data = await self._nvd(
                {"virtualMatchString": f"{cpe}:{version}", "noRejected": "", "resultsPerPage": 20}
            )
            ids = [str((v.get("cve") or {}).get("id")) for v in data.get("vulnerabilities") or []]
            self._matches[key] = (int(data.get("totalResults") or len(ids)), ids)
        return self._matches[key]


def kev_for(rows: list[dict[str, Any]], p: Product) -> list[str]:
    """KEV에서 이 제품의 CVE id."""
    out: list[str] = []
    for r in rows:
        vendor = str(r.get("vendorProject") or "").lower()
        product = str(r.get("product") or "").lower()
        if vendor == p.kev_vendor.lower() and p.kev_product.lower() in product:
            out.append(str(r.get("cveID")))
    return out


async def judge(feed: Feed, product: str, version: str, now: datetime) -> Judgement:
    """제품·버전 하나를 판정한다. 소식을 못 받으면 `feed_failed` (쓰기는 막지 않는다)."""
    p = PRODUCTS.get(product)
    j = Judgement(checked_at=now.isoformat())
    if p is None or not version:
        return j  # 맞춰 볼 수 없는 제품·버전 (판정 없음)
    try:
        hits = []
        for cve_id in kev_for(await feed.kev(), p):
            if cve_hits(version, await feed.cve(cve_id), p.cpe):
                hits.append(cve_id)
        total, ids = await feed.matches(p.cpe, version)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        j.verdict, j.error = "feed_failed", f"취약점 소식을 못 받음 ({type(exc).__name__})"
        return j
    j.kev_cves, j.cve_total, j.cves = hits, total, ids[:5]
    j.verdict = "emergency" if hits else "warning" if total else "clear"
    return j


# ---------------------------------------------------------------- 안전 서비스 알림

Send = Callable[[ConnectionHoldNotice], Awaitable[None]]


async def send_to_safety(notice: ConnectionHoldNotice) -> None:
    """안전 서비스에 보류·해제를 알린다. 실패는 예외 (부른 쪽이 대기로 두고 다시 보낸다)."""
    from ax_agent_template.config import agent_token, agent_url, is_mock

    if is_mock() and not os.getenv("AX_SAFETY_URL"):
        raise RuntimeError("목 모드: 안전 서비스 없음")
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            f"{agent_url('safety')}/api/safety/connection-holds",
            json=notice.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {agent_token('dev')}"},
        )
    res.raise_for_status()


@dataclass
class HoldState:
    """연결 하나의 보류 알림 상태."""

    notice: ConnectionHoldNotice
    sent: bool = False
    error: str = ""


class Watch:
    """판정을 모아 두고, 긴급 보류·해제를 안전 서비스에 보낸다 (못 보낸 건 다시 보낸다)."""

    def __init__(self, feed: Feed | None = None, send: Send | None = None):
        self.feed = feed or Feed()
        self.send = send or send_to_safety
        self.judgements: dict[str, Judgement] = {}
        self.holds: dict[str, HoldState] = {}  # 연결 id → 마지막 보류·해제 알림

    def reset(self) -> None:
        self.judgements.clear()
        self.holds.clear()
        self.feed.clear()

    async def update(
        self, cid: str, product: str, version: str, hosted: bool, now: datetime
    ) -> Judgement:
        """연결 하나를 판정하고 보류·해제가 바뀌면 알림을 만든다."""
        j = Judgement(checked_at=now.isoformat())
        if not hosted:
            j = await judge(self.feed, product, version, now)
        self.judgements[cid] = j
        if j.verdict == "feed_failed":
            return j  # 모르면 보류도 해제도 하지 않는다
        held = cid in self.holds and self.holds[cid].notice.hold
        if j.verdict == "emergency" and (not held or self.holds[cid].notice.version != version):
            why = f"KEV {', '.join(j.kev_cves)} · {product} {version} 취약 범위"
            self.holds[cid] = HoldState(
                ConnectionHoldNotice(
                    connection_id=cid, hold=True, product=product, version=version,
                    kev_cves=j.kev_cves, judged_at=j.checked_at, reason=why,
                )
            )  # fmt: skip
        elif j.verdict != "emergency" and held:
            self.holds[cid] = HoldState(
                ConnectionHoldNotice(
                    connection_id=cid, hold=False, product=product, version=version,
                    judged_at=j.checked_at, reason=f"{product} {version} 긴급 취약 범위 벗어남",
                )
            )  # fmt: skip
        return j

    def forget(self, cid: str) -> None:
        self.judgements.pop(cid, None)
        st = self.holds.get(cid)
        if st is not None and st.notice.hold:
            # 지운 연결의 보류는 해제를 알린다 (안전 서비스에 남지 않게)
            n = st.notice.model_copy(
                update={"hold": False, "kev_cves": [], "reason": "연결을 지움",
                        "judged_at": datetime.now(UTC).isoformat()}
            )  # fmt: skip
            self.holds[cid] = HoldState(n)

    async def flush(self) -> int:
        """못 보낸 보류·해제 알림을 보낸다. 보낸 수."""
        n = 0
        for st in self.holds.values():
            if st.sent:
                continue
            try:
                await self.send(st.notice)
            except Exception as exc:  # noqa: BLE001 - 창구 없음·연결 실패는 다음에 다시
                st.error = f"안전 서비스에 못 보냄 ({type(exc).__name__})"
                continue
            st.sent, st.error = True, ""
            n += 1
        return n


watch = Watch()
