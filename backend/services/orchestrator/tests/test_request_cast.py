"""홍보 영상 의뢰 등장 캐릭터 여럿 (11/27 B · 남녀 두 인물 + 그림 캐릭터).

사람이 화면에서 정한 cast만 의뢰 기록에 둔다(고객 글에서 만들지 않는다). 다음 단계(기획·마케팅)에
cast와 brand_character_record_id(= cast[0])를 넘긴다. 한 명이면 예전과 같은 기록."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_orchestrator.request_runner import RequestRunner, cast_line, clean_cast


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


B = _load("brand_character_helpers_cast", "test_request_brand_character.py")
LEAD, PEER = "CR-0123456789ab", "CR-aaaaaaaaaaaa"
MASCOT, FOURTH = "CR-bbbbbbbbbbbb", "CR-cccccccccccc"
CAST = [{"record_id": LEAD, "role_ko": " 주인공 "}, {"record_id": PEER, "role_ko": "동료"},
        {"record_id": MASCOT, "role_ko": "마스코트"}]  # fmt: skip


def test_clean_cast_rules() -> None:
    got, ok = clean_cast(None, CAST)
    assert ok and [m["role_ko"] for m in got] == ["주인공", "동료", "마스코트"]  # 앞뒤 공백 뗌
    assert clean_cast(LEAD, CAST)[1] and not clean_cast(PEER, CAST)[1]  # 첫 번째 = record_id
    assert not clean_cast(None, [*CAST, {"record_id": FOURTH}])[1]  # 4명째는 막는다
    assert not clean_cast(None, [CAST[0], CAST[0]])[1]  # 같은 캐릭터 두 번
    assert not clean_cast(None, [{"record_id": "CR-x"}])[1]
    assert not clean_cast(None, [{"record_id": LEAD, "role_ko": "가" * 21}])[1]
    assert clean_cast(LEAD, None) == ([], True) and clean_cast(LEAD, []) == ([], True)
    assert cast_line(got[:2]) == f"등장 캐릭터 2명(주인공 {LEAD}, 동료 {PEER})"


async def _until_confirm(fake: Any, cast: list[dict[str, str]] | None, record: str | None = None
                         ) -> tuple[Any, str, RequestRunner]:  # fmt: skip
    reqs, rid = B.make()
    r = RequestRunner(reqs, lambda: fake, retry_delay=0)
    await r.run(rid)
    assert fake.calls == []
    assert r.answer_brand_character(rid, record, by="태윤", cast=cast)[0] == "ok"
    await r.wait_idle()
    assert reqs.get(rid)["steps"]["confirm"]["status"] == "waiting_human"
    return reqs, rid, r


@pytest.mark.asyncio
async def test_three_characters_with_roles_go_to_planning_and_marketing() -> None:
    fake = B.Fake()
    reqs, rid, r = await _until_confirm(fake, CAST)
    row = reqs.get(rid)
    want = [{"record_id": LEAD, "role_ko": "주인공"}, {"record_id": PEER, "role_ko": "동료"},
            {"record_id": MASCOT, "role_ko": "마스코트"}]  # fmt: skip
    assert row["cast"] == want and row["brand_character_record_id"] == LEAD
    plan = next(c[1] for c in fake.calls if c[1].skill == "receive_insight")
    assert plan.input["cast"] == want and plan.input["brand_character_record_id"] == LEAD
    char = next(s for s in row["steps"].values() if (s.get("note") or "").startswith("등장 캐릭터"))
    assert "등장 캐릭터 3명(주인공" in char["note"]
    fake.answers["prepare_video_draft_from_plan"] = [B.Fake.prepared(B.MADE)]
    r.decide(rid, "confirm", True, by="준하")
    await r.wait_idle()
    prepare = next(c[1] for c in fake.calls if c[1].skill == "prepare_video_draft_from_plan")
    assert prepare.input["cast"] == want and prepare.input["brand_character_record_id"] == LEAD
    make_call = next(c[1] for c in fake.calls if c[1].skill == "generate_media")
    assert "cast" not in make_call.input  # 영상 제작은 마케팅이 정한 의뢰 기록 번호로


@pytest.mark.asyncio
async def test_a_fourth_character_or_a_wrong_lead_is_refused() -> None:
    reqs, rid = B.make()
    r = RequestRunner(reqs, lambda: B.Fake(), retry_delay=0)
    await r.run(rid)
    four = [*CAST, {"record_id": FOURTH, "role_ko": "행인"}]
    assert r.answer_brand_character(rid, None, cast=four) == ("invalid", False)
    assert r.answer_brand_character(rid, PEER, cast=CAST) == ("invalid", False)
    assert "cast" not in reqs.get(rid)


@pytest.mark.asyncio
async def test_one_character_keeps_todays_record() -> None:
    fake = B.Fake()
    reqs, rid, _ = await _until_confirm(fake, [{"record_id": LEAD, "role_ko": "주인공"}])
    row = reqs.get(rid)
    assert row["brand_character_record_id"] == LEAD and "cast" not in row
    plan = next(c[1] for c in fake.calls if c[1].skill == "receive_insight")
    assert "cast" not in plan.input and plan.input["brand_character_record_id"] == LEAD


@pytest.mark.asyncio
async def test_changing_before_the_video_request_reprepares_with_the_new_cast() -> None:
    fake = B.Fake()
    reqs, rid, r = await _until_confirm(fake, CAST[:2])
    fake.answers["prepare_video_draft_from_plan"] = [B.Fake.prepared(B.MADE),
                                                     B.Fake.prepared(B.MADE)]  # fmt: skip
    r.decide(rid, "confirm", True, by="준하")
    await r.wait_idle()
    # 영상 요청 전이면 바꾼다 (#1838): 새 cast로 다시 준비
    got = r.answer_brand_character(rid, None, by="태윤", cast=[CAST[1], CAST[2]])
    assert got[0] == "reprepared"
    await r.wait_idle()
    row = reqs.get(rid)
    assert [m["record_id"] for m in row["cast"]] == [PEER, MASCOT]
    assert row["brand_character_record_id"] == PEER
    # 한 명으로 줄이면 예전과 같은 기록
    assert r.answer_brand_character(rid, LEAD)[0] in ("reprepared", "busy", "video_requested")
    if reqs.get(rid)["brand_character_record_id"] == LEAD:
        assert "cast" not in reqs.get(rid)


def test_api_takes_the_cast_and_refuses_four(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    class Runner:
        def answer_brand_character(self, rid: str, record_id: Any, by: Any = None,
                                   cast: Any = None) -> tuple[str, bool]:  # fmt: skip
            seen.append((record_id, cast))
            return "ok", False

    monkeypatch.setattr(B.state, "manager", type("M", (), {"request_runner": Runner()})())
    client = TestClient(B.app)
    body = {"record_id": LEAD, "cast": [{"record_id": LEAD, "role_ko": "주인공"},
                                         {"record_id": PEER, "role_ko": "동료"}]}  # fmt: skip
    got = client.post("/api/requests/wr_x/brand-character", json=body, headers=B.HUMAN)
    assert got.status_code == 200, got.text
    assert seen[-1] == (LEAD, [{"record_id": LEAD, "role_ko": "주인공"},
                               {"record_id": PEER, "role_ko": "동료"}])  # fmt: skip
    four = {"cast": [{"record_id": x} for x in (LEAD, PEER, MASCOT, FOURTH)]}
    assert client.post("/api/requests/wr_x/brand-character", json=four,
                       headers=B.HUMAN).status_code == 422  # fmt: skip
    # cast 없이 보내면 지금처럼 (빈 목록)
    client.post("/api/requests/wr_x/brand-character", json={"record_id": LEAD}, headers=B.HUMAN)
    assert seen[-1] == (LEAD, [])
