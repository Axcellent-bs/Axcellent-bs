"""콘티 포즈 실패 사다리 (11/26, 공용). 가짜 호출만 쓴다.

1단 성공 → 호출 1번 / 1단 실패 → 2단 동시 / 400 _model → 3단 기본 모델 / 전체 시간 상한 하나 /
규칙 포즈 낱말 표."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from ax_agent_template import pose_ladder as pl
from ax_agent_template import pose_render as pr
from ax_agent_template.llm import LLMInputError, LLMResponseError

IDS = [1, 2, 3, 4]


class Fake:
    """whole·one 호출을 센다. fail_whole·fail_ones: 실패시킬 것."""

    def __init__(self, name: str = "llm", *, fail_whole: BaseException | None = None,
                 fail_ones: set[int] | None = None, delay: float = 0.0) -> None:  # fmt: skip
        self.name, self.fail_whole, self.fail_ones = name, fail_whole, fail_ones or set()
        self.delay = delay
        self.whole_calls = 0
        self.one_calls: list[int] = []
        self.active = self.peak = 0


async def whole(c: Fake) -> dict[int, str]:
    c.whole_calls += 1
    if c.fail_whole is not None:
        raise c.fail_whole
    return {i: f"{c.name}:{i}" for i in IDS}


async def one(c: Fake, i: int) -> str:
    c.one_calls.append(i)
    c.active += 1
    c.peak = max(c.peak, c.active)
    try:
        await asyncio.sleep(c.delay or 0.01)
        if i in c.fail_ones:
            raise LLMResponseError("x", status_code=500)
        return f"{c.name}:{i}"
    finally:
        c.active -= 1


def _run(llm: Fake | None, **kw: Any) -> pl.LadderResult[str]:
    return asyncio.run(pl.ladder(IDS, whole, one, llm, timeout=kw.pop("timeout", 5.0), **kw))


def _bad(message: str) -> LLMResponseError:
    return LLMResponseError("400", status_code=400, provider_message=message)


def test_first_step_success_is_one_call() -> None:
    llm = Fake()
    res = _run(llm)
    assert res.calls == 1 and llm.whole_calls == 1 and llm.one_calls == []
    assert res.got == {i: f"llm:{i}" for i in IDS} and res.steps == ["whole"]


@pytest.mark.parametrize(
    "exc", [_bad("output_format.schema invalid"), LLMInputError("strict 한도를 넘음")]
)
def test_400_or_limit_splits_per_scene_at_once(exc: BaseException) -> None:
    llm = Fake(fail_whole=exc, fail_ones={3})
    res = _run(llm)
    assert sorted(llm.one_calls) == IDS and llm.peak == len(IDS)  # 장면별 4번 동시
    assert res.calls == 1 + len(IDS) and set(res.got) == {1, 2, 4}  # 3은 4단(규칙)으로
    assert res.steps == ["whole", "2단 장면별"]


def test_timeouts_and_unknown_errors_do_not_split() -> None:
    llm = Fake(fail_whole=RuntimeError("끊김"))
    res = _run(llm)
    assert res.got == {} and llm.one_calls == [] and res.calls == 1


def test_no_llm_calls_nothing() -> None:
    res = _run(None)
    assert res.got == {} and res.calls == 0


def test_model_400_retries_with_the_base_model() -> None:
    llm = Fake(fail_whole=_bad("model: not found"), fail_ones=set(IDS))
    llm.fail_ones = set(IDS)
    base = Fake("base")
    made: list[str] = []

    def factory() -> Fake:
        made.append("base")
        return base

    res = _run(llm, base_llm=factory)
    assert made == ["base"] and sorted(base.one_calls) == IDS
    assert res.got == {i: f"base:{i}" for i in IDS}
    assert res.steps == ["whole", "2단 장면별", "3단 기본 모델"]
    other = Fake(fail_whole=_bad("schema"), fail_ones=set(IDS))
    made.clear()
    _run(other, base_llm=factory)
    assert made == []  # _model 400이 아니면 기본 모델을 만들지도 않는다


def test_one_total_time_limit() -> None:
    llm = Fake(fail_whole=_bad("schema"), delay=1.0)
    started = time.monotonic()
    res = _run(llm, timeout=0.3)
    assert time.monotonic() - started < 0.9  # 장면 4개 × 1초가 아니라 상한 하나
    assert res.got == {}


@pytest.mark.parametrize(
    ("text", "check"),
    [
        ("주인공이 지도를 가리킨다", lambda p: p.arm_r.raise_ == 90),
        ("카메라를 보며 손을 흔들고 마무리", lambda p: p.arm_r.raise_ >= 140),
        ("무대에서 마이크를 든다", lambda p: p.arm_r.bend >= 100),
        ("스마트폰으로 여행지를 검색", lambda p: p.prop == "phone"),
        ("뒤를 돌아보는 여행자", lambda p: p.yaw == 90),
        ("깜짝 놀라 얼어붙는다", lambda p: p.arm_l.raise_ == p.arm_r.raise_ == 100
         and p.head.pitch < 0),
        ("만세를 부른다", lambda p: p.arm_l.raise_ >= 150 and p.arm_r.raise_ >= 150),
        ("커피를 마신다", lambda p: p.prop == "cup"),
        ("close-up of her face", lambda p: p.shot == "close"),
        ("wide shot of the station", lambda p: p.shot == "full"),
        ("가만히 서 있다", lambda p: (p.shot, p.yaw, p.arm_r.raise_, p.prop) == ("medium", 0, 0,
                                                                                    None)),
    ],
)  # fmt: skip
def test_rule_pose_words(text: str, check: Any) -> None:
    pose = pl.rule_pose(text)
    assert check(pose)
    assert pose.label_ko == text[:10]
    key = pr.key_pose(pose, "", 2, text)  # 계약 범위 안에서 그대로 그려진다
    assert key is not None and pr.render(key)


def test_rule_detail() -> None:
    assert pl.rule_detail(4) == "rule_pose_4" and pl.rule_detail(0) == ""
