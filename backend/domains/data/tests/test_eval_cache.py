"""평가 데이터 캐시(`DATA_EVAL_CACHE_DIR`): 캐시를 써도 데이터와 평가 결과가 완전히 같아야 한다.

캐시는 생성 시간을 줄이려는 것뿐이다. 캐시 없이 만든 데이터, 처음 만들며 저장한 데이터(빗나감),
저장한 파일에서 읽은 데이터(적중)가 DB 전체·결제 기록까지 같은지, 그 데이터로 돌린 케이스
결과가 같은지 본다. 종류별 1개(되도록 만들기 빠른 holdout)만 돌려 CI 시간을 덜 늘린다.
전체 평가 비교는 PR에서 한 번 손으로 돌려 확인한다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(EVAL))

import eval_world  # noqa: E402

pytestmark = pytest.mark.eval


def _load(name: str):  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(f"data_eval_cache_{name}", EVAL / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run():  # noqa: ANN201
    return _load("run")


@pytest.fixture(scope="module")
def sample(run) -> list[dict]:  # noqa: ANN001
    """종류마다 1개. 만들기 빠른 holdout에서 고르고, 거기 없는 종류(세일·빈 구간)만 정답지에서."""
    scenarios, _ = run.load_cases()
    picked = {c["kind"]: c for c in reversed(run.load_holdout())}
    for case in scenarios:
        picked.setdefault(case["kind"], case)
    return list(picked.values())


# 만든 시각(벽시계)이 들어가는 칸. shop-api 시드·주문·outbox가 시나리오 시계 대신 지금 시각을 쓴다.
# 캐시와 상관없이 실행마다 다르고, 평가가 부르는 조회(failed-orders·metric-series)는 읽지 않는다.
WALL_CLOCK = {
    "sellers": {"created_at"},
    "products": {"created_at", "updated_at"},
    "buyers": {"created_at"},
    "orders": {"promised_delivery_at"},
    "outbox_events": {"created_at"},
}


def _dump(world) -> tuple[dict[str, list[dict]], list]:  # noqa: ANN001
    """DB 전체(표마다 모든 행·칸)와 결제 기록. 벽시계 칸만 가린다."""
    tables: dict[str, list[dict]] = {}
    with world.db.tx() as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in sorted(names):
            rows = []
            for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid'):
                values = dict(row)
                for column in WALL_CLOCK.get(name, ()):
                    values[column] = "<wall-clock>"
                if name == "outbox_events":
                    payload = json.loads(values["payload"])
                    payload.pop("occurred_at")
                    values["payload"] = payload
                rows.append(values)
            tables[name] = rows
    return tables, list(world.payments)


def _no_generate(spec):  # noqa: ANN001, ANN202
    raise AssertionError("캐시 적중인데 데이터를 새로 만들었다")


def test_cached_world_is_identical(sample, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    for case in sample:
        monkeypatch.delenv(eval_world.CACHE_ENV, raising=False)
        fresh = eval_world.build(case)

        monkeypatch.setenv(eval_world.CACHE_ENV, str(tmp_path))
        missed = eval_world.build(case)  # 빗나감: 만들고 저장한다
        assert eval_world.cache_path(case, tmp_path).is_file()
        with monkeypatch.context() as m:
            m.setattr(eval_world, "generate", _no_generate)
            hit = eval_world.build(case)  # 적중: 파일에서 읽는다

        assert _dump(missed) == _dump(fresh), case["id"]
        assert _dump(hit) == _dump(fresh), case["id"]
        assert eval_world.truth(hit) == eval_world.truth(fresh), case["id"]


def test_case_results_are_identical_with_cache(run, sample, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """빗나감(만들고 저장) 데이터는 위 테스트가 본다. 여기서는 캐시 없음과 적중을 돌려 비교한다.

    케이스 결과는 데이터(위에서 같음을 확인)만으로 정해진다. 판정·감시 재현이 느려서 holdout의
    결제 장애·정상 1개씩만 돌린다.
    """
    picked = [c for c in sample if c["kind"] in ("payment_fault", "normal")]
    for case in picked:
        monkeypatch.delenv(eval_world.CACHE_ENV, raising=False)
        without = asyncio.run(run.run_case(case))
        monkeypatch.setenv(eval_world.CACHE_ENV, str(tmp_path))
        eval_world.build(case)  # 캐시에 저장
        with monkeypatch.context() as m:
            m.setattr(eval_world, "generate", _no_generate)
            hit = asyncio.run(run.run_case(case))
        assert hit == without, case["id"]


def test_key_follows_spec_and_code(tmp_path, monkeypatch) -> None:
    spec = {"id": "x", "window": {"from": "2026-09-01T10:00:00Z", "to": "2026-09-01T11:00:00Z"}}
    # 정답은 키에 넣지 않는다 (make_cases가 정답 없이 같은 데이터를 만든다)
    assert eval_world.spec_hash(spec) == eval_world.spec_hash({**spec, "expected": {"a": 1}})
    assert eval_world.spec_hash(spec) != eval_world.spec_hash({**spec, "id": "y"})

    code = tmp_path / "gen.py"
    code.write_text("A = 1\n", encoding="utf-8")
    monkeypatch.setattr(eval_world, "CODE_FILES", (code,))
    monkeypatch.setattr(eval_world, "_BACKEND", tmp_path)
    monkeypatch.setattr(eval_world, "SHOP_API_SRC", tmp_path / "shop")
    monkeypatch.setattr(eval_world, "_code_hash", None)
    before = eval_world.code_hash()
    code.write_text("A = 2\n", encoding="utf-8")
    monkeypatch.setattr(eval_world, "_code_hash", None)
    assert eval_world.code_hash() != before
    assert eval_world.cache_path(spec, tmp_path).parent.name == eval_world.code_hash()


def test_shop_api_source_is_part_of_the_key() -> None:
    files = eval_world._source_files()
    assert eval_world.SHOP_API_SRC / "ax_shop_api" / "service.py" in files
    assert eval_world.SHOP_API_SRC / "ax_shop_api" / "seed.py" in files
    assert all("__pycache__" not in f.parts for f in files)


def test_no_env_means_no_cache(sample, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv(eval_world.CACHE_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    eval_world.build(sample[-1])
    assert list(tmp_path.iterdir()) == []


def test_old_code_folders_are_pruned_and_broken_files_rebuilt(
    sample, tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    stale = tmp_path / "0000000000000000"
    stale.mkdir()
    (stale / "old.sqlite").write_bytes(b"old")
    monkeypatch.setattr(eval_world, "_pruned", set())
    monkeypatch.setenv(eval_world.CACHE_ENV, str(tmp_path))
    case = sample[-1]
    path = eval_world.cache_path(case, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a sqlite file")
    world = eval_world.build(case)
    assert not stale.exists()
    monkeypatch.delenv(eval_world.CACHE_ENV)
    assert _dump(world) == _dump(eval_world.build(case))
