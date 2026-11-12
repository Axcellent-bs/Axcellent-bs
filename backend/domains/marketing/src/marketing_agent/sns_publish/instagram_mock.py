"""Instagram 목 어댑터. 네트워크·토큰 없이 컨테이너 상태 전이와 에러 코드를 재현한다.

주입 가능한 실패 시나리오(`inject`):
- `fail_on(op, code, subcode, times=1)`: 그 호출에서 `IgApiError`를 times번 올린다.
- `lose_response_on("media_publish", processed=True)`: 게시는 **처리하되** 응답을 잃은 것처럼
  `IgResponseLost`. `processed=False`면 처리 전에 잃는다(게시되지 않은 채 응답 없음).
- `container_error(code, subcode)`: 상태 조회가 `ERROR`와 그 코드를 돌려준다.
- `in_progress_polls`: 상태 조회가 `IN_PROGRESS`를 이만큼 돌려준 뒤 `FINISHED`.
- `expire_container()`: 상태 조회가 `EXPIRED`.
호출 횟수는 `calls`에 쌓인다(테스트가 "executed 전 0회", "게시 1회"를 단언).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from marketing_agent.sns_publish.instagram import (
    CAROUSEL_MAX,
    CAROUSEL_MIN,
    CarouselRequest,
    ContainerRequest,
    ContainerStatus,
    IgApiError,
    IgResponseLost,
    MediaInfo,
    PublishingLimit,
    PublishOutcome,
)

OPS = (
    "create_container",
    "create_carousel_item",
    "create_carousel_container",
    "get_container_status",
    "media_publish",
    "get_media",
    "delete_media",
    "get_publishing_limit",
)


@dataclass
class _Failure:
    code: int
    subcode: int | None
    times: int


@dataclass
class _Container:
    request: ContainerRequest
    polls: int = 0
    finished: bool = False  # 상태 조회가 FINISHED를 돌려준 적이 있다
    published_media_id: str | None = None
    children: tuple[str, ...] = ()


class MockInstagramAdapter:
    name = "mock"
    supports_carousel = True

    def __init__(self, in_progress_polls: int = 0) -> None:
        self.in_progress_polls = in_progress_polls
        self.calls: Counter[str] = Counter()
        self.containers: dict[str, _Container] = {}
        self.media: dict[str, MediaInfo] = {}
        self.create_requests: list[ContainerRequest] = []
        self.carousel_item_refs: list[str] = []  # 자식으로 만든 media_ref(순서 그대로)
        self.carousel_requests: list[CarouselRequest] = []
        self.report_ai_flag: bool | None = True  # get_media가 돌려줄 is_ai_generated
        self.config_issue: str | None = None  # media_publish 응답의 config_issue
        self.limit_reads = 0
        self.quota_usage = 0  # get_publishing_limit이 돌려줄 값
        self.quota_total: int | None = 50
        self._failures: dict[str, list[_Failure]] = {}
        self._lose: dict[str, bool] = {}
        self._container_error: tuple[int, int | None] | None = None
        self._expired = False
        self._seq = 0

    # ---- 시나리오 주입 ----
    def fail_on(self, op: str, code: int, subcode: int | None = None, times: int = 1) -> None:
        assert op in OPS
        self._failures.setdefault(op, []).append(_Failure(code, subcode, times))

    def lose_response_on(self, op: str, processed: bool = True) -> None:
        self._lose[op] = processed

    def container_error(self, code: int, subcode: int | None = None) -> None:
        self._container_error = (code, subcode)

    def expire_container(self) -> None:
        self._expired = True

    def _maybe_fail(self, op: str) -> None:
        for failure in self._failures.get(op, []):
            if failure.times > 0:
                failure.times -= 1
                raise IgApiError(failure.code, failure.subcode)

    # ---- 인터페이스 ----
    async def create_container(self, request: ContainerRequest) -> str:
        self.calls["create_container"] += 1
        self._maybe_fail("create_container")
        self._seq += 1
        container_id = f"mock_container_{self._seq}"
        self.containers[container_id] = _Container(request)
        self.create_requests.append(request)
        return container_id

    async def create_carousel_item(self, media_ref: str) -> str:
        self.calls["create_carousel_item"] += 1
        self._maybe_fail("create_carousel_item")
        self._seq += 1
        container_id = f"mock_child_{self._seq}"
        # 자식은 AI 표시를 받지 않는다(is_ai_generated=False로 둔 이미지 컨테이너)
        self.containers[container_id] = _Container(
            ContainerRequest(media_ref, "image", "", False, False)
        )
        self.carousel_item_refs.append(media_ref)
        return container_id

    async def create_carousel_container(self, request: CarouselRequest) -> str:
        self.calls["create_carousel_container"] += 1
        self._maybe_fail("create_carousel_container")
        if not CAROUSEL_MIN <= len(request.child_ids) <= CAROUSEL_MAX:
            raise IgApiError(-1, None)  # 모르는 오류(2~10장이 아니다)
        if any(child not in self.containers for child in request.child_ids):
            raise IgApiError(24, 2207008)
        if any(not self.containers[child].finished for child in request.child_ids):
            raise IgApiError(9007, 2207027)  # 자식이 아직 FINISHED가 아니다
        self._seq += 1
        container_id = f"mock_carousel_{self._seq}"
        self.containers[container_id] = _Container(
            ContainerRequest(
                "carousel", "carousel", request.caption, request.is_ai_generated, False
            ),
            children=tuple(request.child_ids),
        )
        self.carousel_requests.append(request)
        return container_id

    async def get_container_status(self, container_id: str) -> ContainerStatus:
        self.calls["get_container_status"] += 1
        self._maybe_fail("get_container_status")
        container = self.containers.get(container_id)
        if container is None:
            raise IgApiError(24, 2207008)
        if container.published_media_id:
            return ContainerStatus("PUBLISHED", media_id=container.published_media_id)
        if self._expired:
            return ContainerStatus("EXPIRED")
        if self._container_error is not None:
            code, subcode = self._container_error
            return ContainerStatus("ERROR", error_code=code, error_subcode=subcode)
        container.polls += 1
        if container.polls <= self.in_progress_polls:
            return ContainerStatus("IN_PROGRESS")
        container.finished = True
        return ContainerStatus("FINISHED")

    async def media_publish(self, container_id: str) -> str | PublishOutcome:
        self.calls["media_publish"] += 1
        self._maybe_fail("media_publish")
        container = self.containers.get(container_id)
        if container is None:
            raise IgApiError(24, 2207008)
        if not container.finished and not container.published_media_id:
            raise IgApiError(9007, 2207027)  # 부모가 FINISHED가 되기 전 게시
        if self._lose.get("media_publish") is False:
            raise IgResponseLost("media_publish")
        if container.published_media_id:  # 실제 API도 이미 게시한 컨테이너는 다시 게시하지 않는다
            raise IgApiError(24, 2207006)
        self._seq += 1
        media_id = f"mock_media_{self._seq}"
        container.published_media_id = media_id
        request = container.request
        self.media[media_id] = MediaInfo(
            media_id=media_id,
            permalink=f"https://www.instagram.com/p/mock{self._seq}/",
            timestamp="2026-11-05T00:00:00+0000",
            media_product_type="REELS" if request.media_kind == "reel" else "FEED",
            is_ai_generated=self.report_ai_flag if request.is_ai_generated else False,
        )
        if "media_publish" in self._lose:
            raise IgResponseLost("media_publish")
        if self.config_issue:
            return PublishOutcome(media_id, self.config_issue)
        return media_id

    async def get_publishing_limit(self) -> PublishingLimit:
        self.limit_reads += 1  # 읽기 전용이라 `calls`(쓰기 호출 횟수)에 넣지 않는다
        self._maybe_fail("get_publishing_limit")
        return PublishingLimit(self.quota_usage, self.quota_total)

    async def get_media(self, media_id: str) -> MediaInfo:
        self.calls["get_media"] += 1
        self._maybe_fail("get_media")
        info = self.media.get(media_id)
        if info is None:
            raise IgApiError(24, 2207006)
        return info

    async def delete_media(self, media_id: str) -> None:
        self.calls["delete_media"] += 1
        self._maybe_fail("delete_media")
        self.media.pop(media_id, None)
