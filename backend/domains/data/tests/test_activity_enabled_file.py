"""activity_report의 켠 서비스는 라우터와 같은 함수로 읽는다: 켠 서비스 파일을 바꾸면 따라간다."""

from __future__ import annotations

import json
import os

from ax_agent_template import routing
from data_agent.skills.activity_report import enabled_service_ids


def test_enabled_service_ids_follow_the_enabled_services_file(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(routing, "_enabled_cache", None)
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    path = tmp_path / "enabled_services.json"
    path.write_text(json.dumps({"enabled": ["data", "finance"]}), encoding="utf-8")
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(path))
    assert enabled_service_ids() == ["finance", "data"]  # SERVICES 순서

    path.write_text(json.dumps({"enabled": ["coupon"]}), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert enabled_service_ids() == ["coupon"]  # 재시작 없이

    monkeypatch.delenv(routing.ENABLED_FILE_ENV)
    assert enabled_service_ids() == ["inquiry"]  # 파일이 없으면 환경변수
