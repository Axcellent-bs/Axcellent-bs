"""캐시 키의 코드 버전 (`skills/code_version.py`).

코드가 바뀌면 다시 만드는지는 각 skill 테스트에 있다 (`*_is_rebuilt_when_the_code_changes`).
"""

from __future__ import annotations

from data_agent.skills import code_version


def test_source_hash_is_stable_and_follows_the_files() -> None:
    a = code_version.source_hash("domain_profile")
    assert a == code_version.source_hash("domain_profile") and len(a) == 12
    assert a != code_version.source_hash("source_plan")
    assert a != code_version.source_hash("domain_profile", "source_plan")
    assert code_version.source_hash("없는모듈") != code_version.source_hash("없는모듈2")
