"""프로세스 안의 개발 서비스 하나 (dev, 8111). A2A 핸들러와 REST가 같은 서비스를 본다."""

from __future__ import annotations

from dev_agent.service import DevService

_service: DevService | None = None


def get_service() -> DevService:
    global _service
    if _service is None:
        _service = DevService(real_example=True)
    return _service


def set_service(service: DevService) -> DevService:
    """테스트에서 시계·관문을 바꾼 서비스를 끼울 때 쓴다."""
    global _service
    _service = service
    return service
