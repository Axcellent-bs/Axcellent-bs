"""취약점 소식 맞춰 보기: KEV·NVD(가짜 소식)로 긴급·경고·해당 없음·점검 실패를 판정하고,
긴급이면 안전 서비스에 보류를, 벗어나면 해제를 알린다 (못 보내면 다음에 다시)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from dev_agent.vcs import vuln_watch as vw

pytestmark = pytest.mark.role("dev")

GITLAB = "cpe:2.3:a:gitlab:gitlab"
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def _cve(cve_id: str, **bounds: str) -> dict[str, Any]:
    match = {"vulnerable": True, "criteria": f"{GITLAB}:*:*:*:*:*:*:*:*", **bounds}
    return {"id": cve_id, "configurations": [{"nodes": [{"cpeMatch": [match]}]}]}


KEV = {
    "vulnerabilities": [
        {"cveID": "CVE-2026-1001", "vendorProject": "GitLab", "product": "GitLab CE/EE"},
        {"cveID": "CVE-2024-0002", "vendorProject": "GitLab", "product": "GitLab"},
        {"cveID": "CVE-2026-9999", "vendorProject": "Jenkins", "product": "Jenkins"},
    ]
}
CVES = {
    # 19.5.1 / 18.11.13에서 고침
    "CVE-2026-1001": _cve(
        "CVE-2026-1001", versionStartIncluding="18.0.0", versionEndExcluding="18.11.13"
    ),
    "CVE-2024-0002": _cve("CVE-2024-0002", versionEndExcluding="16.0.0"),  # 오래전에 고침
}


class FakeFeed:
    def __init__(self, matches: dict[str, int] | None = None, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.matches = matches or {}
        self.fail = fail

    async def __call__(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((url, params))
        if self.fail:
            raise httpx.ConnectError("소식 서버 없음")
        if url == vw.KEV_URL:
            return KEV
        if "cveId" in params:
            return {"vulnerabilities": [{"cve": CVES[params["cveId"]]}]}
        version = params["virtualMatchString"].rsplit(":", 1)[1]
        n = self.matches.get(version, 0)
        return {
            "totalResults": n,
            "vulnerabilities": [{"cve": {"id": f"CVE-X-{i}"}} for i in range(n)],
        }


def _watch(feed: FakeFeed, sent: list | None = None, fail_send: bool = False) -> vw.Watch:
    async def send(n: vw.ConnectionHoldNotice) -> None:
        if fail_send:
            raise httpx.ConnectError("창구 없음")
        if sent is not None:
            sent.append(n)

    return vw.Watch(vw.Feed(feed, nvd_gap=0), send)


def run(coro):
    return asyncio.run(coro)


def test_version_range() -> None:
    m = {
        "criteria": f"{GITLAB}:*:*",
        "versionStartIncluding": "18.0",
        "versionEndExcluding": "18.11.13",
    }
    assert vw.in_range("18.11.12", m, GITLAB) and vw.in_range("18.0.0-ee", m, GITLAB)
    assert not vw.in_range("18.11.13", m, GITLAB) and not vw.in_range("17.9", m, GITLAB)
    exact = {"criteria": f"{GITLAB}:16.0.1:*:*"}
    assert vw.in_range("16.0.1", exact, GITLAB) and not vw.in_range("16.0.2", exact, GITLAB)
    assert not vw.in_range("1.0", {"criteria": "cpe:2.3:a:other:x:*"}, GITLAB)
    assert not vw.in_range("1.0", {"criteria": f"{GITLAB}:*:*"}, GITLAB)  # 범위가 없으면 아님


def test_emergency_then_patched_sends_hold_then_release() -> None:
    sent: list[vw.ConnectionHoldNotice] = []
    w = _watch(FakeFeed({"18.11.12": 4, "18.11.13": 0}), sent)

    async def go():
        j = await w.update("conn-1", "gitlab", "18.11.12", False, NOW)
        await w.flush()
        again = await w.update("conn-1", "gitlab", "18.11.12", False, NOW)
        await w.flush()  # 같은 판정이면 다시 보내지 않는다
        fixed = await w.update("conn-1", "gitlab", "18.11.13", False, NOW)
        await w.flush()
        return j, again, fixed

    j, again, fixed = run(go())
    assert (j.verdict, j.kev_cves, j.cve_total) == ("emergency", ["CVE-2026-1001"], 4)
    assert again.verdict == "emergency"
    assert fixed.verdict == "clear"
    assert [(n.hold, n.version, n.kev_cves) for n in sent] == [
        (True, "18.11.12", ["CVE-2026-1001"]),
        (False, "18.11.13", []),
    ]
    assert sent[0].judged_at == NOW.isoformat() and "KEV CVE-2026-1001" in sent[0].reason


def test_warning_only_does_not_hold() -> None:
    sent: list = []
    w = _watch(FakeFeed({"19.6.0": 2}), sent)
    j = run(w.update("conn-1", "gitlab", "19.6.0", False, NOW))
    run(w.flush())
    assert (j.verdict, j.cve_total, j.cves) == ("warning", 2, ["CVE-X-0", "CVE-X-1"])
    assert sent == []


def test_feed_failure_does_not_block_or_release() -> None:
    sent: list = []
    feed = FakeFeed({"18.11.12": 1})
    w = _watch(feed, sent)
    run(w.update("conn-1", "gitlab", "18.11.12", False, NOW))
    run(w.flush())
    assert [n.hold for n in sent] == [True]
    feed.fail = True
    w.feed.clear()
    j = run(w.update("conn-1", "gitlab", "18.11.12", False, NOW))
    run(w.flush())
    assert j.verdict == "feed_failed" and "못 받음" in j.error
    assert [n.hold for n in sent] == [True]  # 모르면 해제도 하지 않는다


def test_hosted_and_unknown_products_are_skipped() -> None:
    feed = FakeFeed()
    w = _watch(feed)
    assert run(w.update("c", "github.com", "", True, NOW)).verdict == ""
    assert run(w.update("c", "ghes", "", False, NOW)).verdict == ""  # 버전 모름
    assert feed.calls == []


def test_unsent_notice_is_retried() -> None:
    w = _watch(FakeFeed({"18.11.12": 1}), fail_send=True)
    run(w.update("conn-1", "gitlab", "18.11.12", False, NOW))
    assert run(w.flush()) == 0
    st = w.holds["conn-1"]
    assert not st.sent and "못 보냄" in st.error
    sent: list = []

    async def ok(n):
        sent.append(n)

    w.send = ok
    assert run(w.flush()) == 1 and w.holds["conn-1"].sent and len(sent) == 1
    # 연결을 지우면 보류 해제를 알린다
    w.forget("conn-1")
    run(w.flush())
    assert [n.hold for n in sent] == [True, False]
