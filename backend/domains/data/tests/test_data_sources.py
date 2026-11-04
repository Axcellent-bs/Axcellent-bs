"""data_sources: 출처 목록·상태·승인 (코드).

실제 네이버·YouTube를 부르지 않는다 (확인 호출은 가짜 함수).
- 키가 모두 있으면 사용 가능, 없으면 키 필요 + 키 이름 (값은 넣지 않음)
- 쇼핑인사이트는 확인 호출 1번이 성공해야 사용 가능, 결과 저장, 실패면 하루 뒤 다시
- 기존 출처는 승인, 새 출처는 회사 설정 승인 플래그가 있어야 승인
- 업종 태그만 쓰고 회사 이름은 없다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data_agent.skills import data_sources as ds

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)
KEYS = {
    "YOUTUBE_API_KEY": "y",
    "NAVER_HUB_CLIENT_ID": "i",
    "NAVER_HUB_CLIENT_SECRET": "s",
}


def _prober(ok: bool, calls: list[str]):  # noqa: ANN202
    async def fn(source_id: str) -> tuple[bool, str]:
        calls.append(source_id)
        return ok, "쇼핑인사이트 호출 성공" if ok else "UNAUTHORIZED: 401"

    return fn


def _run(tmp_path: Path, env: dict[str, str], ok: bool = True, now: datetime = NOW):  # noqa: ANN202
    calls: list[str] = []
    env = env | {"DATA_SOURCE_PROBE_DB": str(tmp_path / "probe.db")}
    out = asyncio.run(ds.list_sources(env, prober=_prober(ok, calls), now=now)).output
    return {s.source_id: s for s in out.sources}, calls, out


def test_every_source_has_capabilities_and_only_industry_tags(tmp_path: Path) -> None:
    by, _, out = _run(tmp_path, KEYS)
    assert set(by) == {"youtube", "google_trending", "naver_search_trend", "naver_shopping",
                       "data4library", "kosis"}  # fmt: skip
    assert by["data4library"].industries == ["출판·교육"] and by["kosis"].industries == ["all"]
    assert by["data4library"].kinds == ["consumption"] and by["kosis"].kinds == ["survey"]
    assert by["youtube"].kinds == ["viewing"] and by["naver_search_trend"].age
    assert all(s.docs_url.startswith("http") for s in out.sources)
    assert "캣북스" not in out.model_dump_json()  # 회사 이름 없음


def test_status_follows_keys_and_names_the_missing_key(tmp_path: Path) -> None:
    by, _, _ = _run(tmp_path, KEYS)
    assert by["youtube"].status == "available" and by["google_trending"].status == "available"
    assert by["data4library"].status == "key_required"
    assert "DATA4LIBRARY_API_KEY" in (by["data4library"].status_reason or "")
    assert by["kosis"].status == "key_required" and by["kosis"].key_envs == ["KOSIS_API_KEY"]
    none, calls, _ = _run(tmp_path / "n", {})
    assert none["naver_shopping"].status == "key_required" and calls == []  # 키 없으면 안 부름
    assert none["youtube"].status == "key_required"
    assert "y" not in str(by["youtube"].status_reason)  # 키 값은 넣지 않는다


def test_shopping_needs_one_successful_probe_which_is_stored(tmp_path: Path) -> None:
    by, calls, _ = _run(tmp_path, KEYS, ok=True)
    assert by["naver_shopping"].status == "available" and calls == ["naver_shopping"]
    again, calls2, _ = _run(tmp_path, KEYS, ok=False)  # 저장된 성공을 쓴다
    assert again["naver_shopping"].status == "available" and calls2 == []


def test_failed_probe_means_key_required_and_retries_after_a_day(tmp_path: Path) -> None:
    by, calls, _ = _run(tmp_path, KEYS, ok=False)
    assert by["naver_shopping"].status == "key_required" and calls == ["naver_shopping"]
    assert "확인 호출이 실패" in (by["naver_shopping"].status_reason or "")
    _, soon, _ = _run(tmp_path, KEYS, ok=True, now=NOW + timedelta(hours=2))
    assert soon == []
    later, retry, _ = _run(tmp_path, KEYS, ok=True, now=NOW + timedelta(days=1))
    assert retry == ["naver_shopping"] and later["naver_shopping"].status == "available"


def test_existing_sources_are_approved_and_new_ones_need_the_flag(tmp_path: Path) -> None:
    cfg = tmp_path / "tenant.toml"
    cfg.write_text('[sources]\napproved = ["data4library", "없는출처"]\n', encoding="utf-8")
    by, _, _ = _run(tmp_path, KEYS | {"DATA_TENANT_CONFIG": str(cfg)})
    assert by["youtube"].approved and by["naver_search_trend"].approved
    assert by["data4library"].approved and not by["naver_shopping"].approved
    assert not by["kosis"].approved
