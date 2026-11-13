"""결제 지표 측정과 `shop.degraded`·`shop.recovered` 발행 판정. 프레임워크에 의존하지 않는다.

shop-api가 직접 측정한다(Zabbix는 보조 신호). 기준 초안은 **p95 1,000ms 초과 또는 5xx 2% 초과가
1분 지속**이고 기준값은 환경변수로 바꾼다(평소 p95 180ms·5xx 0.3%).
시간은 호출하는 쪽이 `now`(초)로 넘겨서 테스트에서 시계를 움직일 수 있다.
"""

from __future__ import annotations

import math
import os
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from ax_contracts.schemas import EventType, ShopDegradedData
from ax_shop_api import outbox
from ax_shop_api.db import Db

COMPONENT = "payment"
# 시험·시연 장애 종류 (`ShopDegradedData.fault_kind`와 같은 값)
FaultKind = Literal["switch", "pg_slow", "payment_version"]


@dataclass(frozen=True)
class Thresholds:
    p95_latency_ms: float = 1000.0
    error_rate_5xx: float = 0.02
    window_s: float = 60.0
    sustain_s: float = 60.0
    min_samples: int = 5

    @classmethod
    def from_env(cls) -> Thresholds:
        def number(name: str, default: float) -> float:
            return float(os.getenv(f"SHOP_{name}") or default)

        return cls(
            p95_latency_ms=number("DEGRADED_P95_MS", cls.p95_latency_ms),
            error_rate_5xx=number("DEGRADED_5XX_RATE", cls.error_rate_5xx),
            window_s=number("MONITOR_WINDOW_S", cls.window_s),
            sustain_s=number("DEGRADED_SUSTAIN_S", cls.sustain_s),
            min_samples=int(number("MONITOR_MIN_SAMPLES", cls.min_samples)),
        )


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]  # nearest-rank


class Monitor:
    def __init__(self, db: Db, thresholds: Thresholds | None = None) -> None:
        self.db = db
        self.thresholds = thresholds or Thresholds.from_env()
        self._samples: deque[tuple[float, float, bool]] = deque()  # (시각, 지연 ms, 5xx 여부)
        self._breach_since: float | None = None
        # 장애 중 정상 표본만 이어지기 시작한 시각. `sustain_s` 이어져야 복구로 본다
        self._normal_since: float | None = None
        # 장애 중 스위치를 끈 쪽. 해제는 복구가 아니라서 복구 신호가 나갈 때 싣기만 한다
        self._released_by: str | None = None
        self.degraded = False
        self._incident_key: str | None = None
        self._metric_code: str | None = None
        # 장애가 났을 때 켜져 있던 시험·시연 장애 종류를 알려 주는 함수(쇼핑몰이 넣는다).
        # `shop.degraded`의 `fault_kind`로 싣는다. 모르면 None
        self.fault_kind: Callable[[], FaultKind | None] = lambda: None

    def record(self, now: float, latency_ms: float, server_error: bool) -> None:
        self._samples.append((now, latency_ms, server_error))

    def _breach(self, now: float) -> tuple[str, float, float] | None:
        """기준을 넘은 지표 `(metric_code, 값, 기준값)`. 표본이 모자라면 판단하지 않는다."""
        limit = self.thresholds
        while self._samples and now - self._samples[0][0] > limit.window_s:
            self._samples.popleft()
        if len(self._samples) < limit.min_samples:
            return None
        p95 = _p95([latency for _, latency, _ in self._samples])
        rate = sum(1 for *_, error in self._samples if error) / len(self._samples)
        if p95 > limit.p95_latency_ms:
            return "p95_latency_ms", p95, limit.p95_latency_ms
        if rate > limit.error_rate_5xx:
            return "error_rate_5xx", rate, limit.error_rate_5xx
        return None

    def _enqueue(
        self,
        event_type: EventType,
        metric: str,
        value: float | None,
        threshold: float | None,
        since: float | None = None,
        recovery: str | None = None,
        released_by: str | None = None,
        fault_kind: FaultKind | None = None,
    ) -> None:
        data = ShopDegradedData(
            component=COMPONENT,
            metric_code=metric,
            value=value,
            threshold=threshold,
            since=datetime.fromtimestamp(since, UTC) if since is not None else None,
            recovery=recovery,
            released_by=released_by,
            fault_kind=fault_kind,
        )
        with self.db.tx() as conn:
            if event_type == EventType.SHOP_DEGRADED:
                # DB를 새로 만들면 순번이 dgd_0001부터 다시 시작해 이벤트 ID(키의 해시)가
                # 지난 장애와 겹치고, 받는 쪽이 중복으로 버린다. 무작위 접미사로 겹치지 않게 한다.
                number = self.db.next_id(conn, "degraded", "dgd_")
                self._incident_key = f"{number}-{uuid.uuid4().hex[:8]}"
            outbox.enqueue(
                conn,
                event_type,
                data.model_dump(mode="json"),
                f"{event_type.value}:{self._incident_key}",
            )

    def evaluate(self, now: float) -> None:
        """기준 초과가 `sustain_s` 이어지면 `shop.degraded`, 장애 중 정상이 `sustain_s` 이어지면
        `shop.recovered`.

        지속 시간은 기준을 넘기 시작한 첫 표본(느리거나 5xx인 표본)의 시각부터 센다.
        복구도 같은 기준으로 지표만 보고 판정한다. 장애 스위치를 껐어도 표본이 정상으로 이어지기
        전에는 복구가 아니다. 장애 중 스위치를 껐으면 `recovery=fault_released`와 끈 쪽을 싣고,
        끄지 않고 풀렸으면 `metrics_normal`이다.
        """
        breach = self._breach(now)
        if breach is None:
            if len(self._samples) >= self.thresholds.min_samples:  # 표본이 충분한데 정상
                self._breach_since = None
                if self.degraded:
                    if self._normal_since is None:
                        self._normal_since = now
                    if now - self._normal_since >= self.thresholds.sustain_s:
                        self._recovered()
            return
        self._normal_since = None
        if self._breach_since is None:
            slow = self.thresholds.p95_latency_ms
            bad = [at for at, latency, error in self._samples if latency > slow or error]
            self._breach_since = min(bad, default=now)
        if not self.degraded and now - self._breach_since >= self.thresholds.sustain_s:
            self.degraded = True
            self._metric_code = breach[0]
            self._enqueue(
                EventType.SHOP_DEGRADED, *breach, since=self._breach_since,
                fault_kind=self.fault_kind(),
            )  # fmt: skip

    def released(self, released_by: str | None = None) -> None:
        """켜져 있던 장애 스위치를 껐을 때: 스위치 때문에 쌓인 표본만 버리고 "해제됨"을 남긴다.

        복구 신호는 보내지 않는다. 장애 상태(`degraded`)는 그대로이고, 새 표본이 정상으로
        `sustain_s` 이어지면 `evaluate`가 `shop.recovered`(`recovery=fault_released`)를 보낸다.
        스위치와 상관없는 장애였다면 새 표본도 기준을 넘어 장애 상태가 이어진다.
        """
        self._samples.clear()
        self._breach_since = None
        self._normal_since = None
        if self.degraded:
            self._released_by = released_by or ""

    def _recovered(self) -> None:
        """복구 신호. 값·기준은 비운다(장애 지표가 아니라 정상 지속으로 판정했다)."""
        by = self._released_by
        self.degraded = False
        self._normal_since = None
        self._released_by = None
        self._enqueue(
            EventType.SHOP_RECOVERED,
            self._metric_code or "",
            None,
            None,
            recovery="metrics_normal" if by is None else "fault_released",
            released_by=by or None,
        )
