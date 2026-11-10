"""holdout 평가 하네스가 깨지지 않았는지만 본다(작은 dev seed). 숫자 기준은 평가 README가 정한다."""

import importlib.util
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / "eval" / "holdout.py"


def load():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("support_holdout_eval", HARNESS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dev_seed_runs_and_every_area_is_scored() -> None:
    harness = load()
    summary = harness.summarize(harness.run(harness.DEV_SEED, 30))
    assert set(summary["areas"]) == set(harness.AREAS)
    assert all(a["total"] > 0 for a in summary["areas"].values())
    assert summary["rate"] >= 0.95  # 하네스와 코드가 같이 틀어지면 알린다


def test_holdout_corpus_is_only_used_for_the_holdout_seed() -> None:
    harness = load()
    dev = harness.make_abuse_cases(__import__("random").Random("x"), harness.ABUSE_BANK["dev"], 50)
    holdout_terms = {t for terms in harness.ABUSE_BANK["holdout"].values() for t in terms}
    assert not any(any(term in c["text"] for term in holdout_terms) for c in dev)
