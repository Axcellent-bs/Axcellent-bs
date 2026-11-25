"""캐릭터를 영상 요청 전까지 바꾸기 (11/26).

- 영상 초안 준비(prepare) 전: 지금처럼 적기만 한다
- prepare 뒤·영상 요청 전: 새 캐릭터를 적고 준비·제작 단계를 처음 상태로 되돌려 다시 시킨다.
  준비 단계에는 예전 의뢰 기록 번호(`replaces_creative_record_id`)를 같이 넘기고(마케팅이
  멈춤 표시), 기획 결과(carry)는 남아 있어 같은 기획으로 다시 준비한다. 예전 제작 단계의 긴
  작업 결과는 반영하지 않는다
- 영상 요청 뒤(held·completed·rejected_by_human): `video_requested`, 아무것도 바꾸지 않는다
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_request_brand_character import (  # noqa: E402
    BRAND,
    MADE,
    Fake,
    done_event,
    until_confirm,
    until_video_held,
)

OTHER = "CR-aaaaaaaaaaaa"
REMADE = "CR-bbbbbbbbbbbb"


async def until_drawing(fake: Fake):  # noqa: ANN201
    """캐릭터 BRAND로 준비가 끝나고 제작 단계가 그림을 그리는 중(waiting_async)."""
    reqs, rid, r = await until_confirm(fake)
    fake.answers["prepare_video_draft_from_plan"] = [Fake.prepared(MADE)]
    assert r.set_brand_character(rid, BRAND, by="준하") == "ok"
    r.decide(rid, "confirm", True, by="준하")
    await r.wait_idle()
    assert reqs.get(rid)["steps"]["make"]["status"] == "waiting_async"
    return reqs, rid, r


@pytest.mark.asyncio
async def test_before_prepare_only_the_answer_changes() -> None:
    fake = Fake()
    reqs, rid, r = await until_confirm(fake)
    assert r.set_brand_character(rid, BRAND) == "ok"
    assert r.answer_brand_character(rid, OTHER)[0] == "ok"
    assert reqs.get(rid)["brand_character_record_id"] == OTHER
    assert "replaces_creative_record_id" not in reqs.get(rid)
    assert "prepare_video_draft_from_plan" not in fake.skills()  # 아직 사람 확인 전


@pytest.mark.asyncio
async def test_after_prepare_the_same_request_is_prepared_again_with_the_new_character() -> None:
    fake = Fake()
    reqs, rid, r = await until_drawing(fake)
    fake.answers["prepare_video_draft_from_plan"] = [Fake.prepared(REMADE)]
    fake.answers["generate_media"] = [Fake.working("t2")]
    outcome, outdated = r.answer_brand_character(rid, OTHER, by="태윤")
    assert (outcome, outdated) == ("reprepared", True)
    await r.wait_idle()

    prepares = [c[1] for c in fake.calls if c[1].skill == "prepare_video_draft_from_plan"]
    assert len(prepares) == 2
    again = prepares[-1].input
    assert again["brand_character_record_id"] == OTHER
    assert again["replaces_creative_record_id"] == MADE  # 예전 기록에 멈춤 표시
    assert again["proposal_id"] == prepares[0].input["proposal_id"]  # 같은 기획(carry를 남겼다)
    assert again["video_plan"] == prepares[0].input["video_plan"]
    makes = [c[1] for c in fake.calls if c[1].skill == "generate_media"]
    assert len(makes) == 2 and makes[-1].input["creative_record_id"] == REMADE
    row = reqs.get(rid)
    assert row["request_id"] == rid and row["brand_character_record_id"] == OTHER
    assert row["steps"]["prepare"]["result_ref"] == REMADE
    assert row["steps"]["make"]["status"] == "waiting_async"
    assert row["steps"]["make"]["async_task_id"] == "t2"
    assert any("다시 준비" in m["message"] for m in row["log"])

    # 예전 제작 단계(t1)의 결과는 반영하지 않는다
    stale = done_event("t1", False, outcome="video_held", review_id="rev_old")
    assert r.receive_async("completed", "marketing", stale, f"ctx_{rid}") != "resumed"
    assert reqs.get(rid)["steps"]["make"]["status"] == "waiting_async"
    assert reqs.get(rid)["steps"]["make"]["review_id"] is None


@pytest.mark.asyncio
async def test_after_the_video_request_the_character_is_locked() -> None:
    fake = Fake()
    reqs, rid, r = await until_video_held(fake)
    before = reqs.get(rid)
    assert r.answer_brand_character(rid, OTHER) == ("video_requested", False)
    after = reqs.get(rid)
    assert after["brand_character_record_id"] == BRAND
    assert after["steps"] == before["steps"]
    assert fake.skills().count("prepare_video_draft_from_plan") == 1


@pytest.mark.asyncio
async def test_while_preparing_it_asks_to_wait() -> None:
    fake = Fake()
    reqs, rid, r = await until_drawing(fake)
    reqs.update_step(rid, "prepare", status="running")
    assert r.answer_brand_character(rid, OTHER) == ("busy", False)
    assert reqs.get(rid)["brand_character_record_id"] == BRAND
