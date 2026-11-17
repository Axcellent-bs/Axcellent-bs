"""판매 저장소 분석 결과 캐시 (11/18).

같은 skill·같은 입력은 판매 기록이 바뀌기 전까지 다시 계산하지 않는다.

- 키 = (skill, 고객사, 정규화한 입력, 저장소 버전, 카탈로그 id·판, 한국 날짜, 저장소)
  - 저장소 버전(`SalesStore.data_version`)은 수집이 새 행을 저장할 때마다 오른다 → 새 기록이
    들어오면 자연히 다른 키가 된다 (수집기는 다른 프로세스여도 저장소 파일로 알 수 있다)
  - 한국 날짜: "오늘·최근 N일" 기간은 날짜가 바뀌면 달라진다
  - 저장소(`SalesStore.cache_token`): 시험처럼 저장소를 바꿔 끼워도 섞이지 않게
  - 저장소 생성 id(`SalesStore.store_id`, 11/17): `sales_collect --reset`으로 새로 만든 저장소가
    옛 저장소와 같은 버전 번호를 다시 써도 옛 결과가 섞이지 않게 (미리 계산도 같은 키)
- 메모리 LRU, 최대 `MAX_ENTRIES`(256)개. 만든 지 `MAX_AGE`(30분)가 지난 항목은 다시 계산한다(수집이
  멈췄는데 예전 답을 계속 주지 않게)
- 같은 키를 동시에 부르면 한 번만 계산하고 나머지는 그 결과를 받는다 (미리 계산과 화면 요청이
  겹칠 때)
- 이전 결과 먼저 (11/17): 같은 skill·입력·저장소 생성 id의 예전 결과(저장소 버전만 다른 것)가
  있으면 그걸 바로 주고(`Computed.stale`, 출력 basis의 `stale`·`computed_at`) 새 계산은 뒤 스레드로
  한 번만 돌려 캐시를 갱신한다. 미리 계산은 `stale_ok=False`로 기다려 새로 계산한다
- 바깥 출처(KOSIS·네이버·정보나루)는 각 커넥터의 하루 캐시 규칙 그대로다. 여기서는 판매 저장소를
  읽는 계산만 담는다
- 새 skill도 `cached(...)` 하나로 같은 캐시를 쓴다 (계산 함수는 `SalesStore.reading`·`analysis` 읽기
  연결을 쓴다)
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import SalesBasis
from data_agent.sales_quality import KST
from data_agent.sales_store import SalesStore

logger = logging.getLogger(__name__)
MAX_ENTRIES = 256
MAX_AGE = timedelta(minutes=30)


@dataclass(frozen=True)
class Computed[T]:
    value: T
    computed_at: datetime  # 계산한 시각 (UTC)
    hit: bool  # 캐시에서 꺼냈는지
    stale: bool = False  # 판매 기록이 바뀌기 전 예전 결과 (새 계산은 뒤에서 돈다)


class ResultCache:
    """키별 결과 + 묶음(`family`, 키에서 저장소 버전만 뺀 것)별 마지막 결과.

    이전 결과 먼저 (11/17): 같은 묶음의 예전 결과(저장소 버전만 다른 것)가 있으면 그걸 바로
    돌려주고(`stale`), 새 계산은 뒤 스레드로 한 번만 돌려 캐시를 갱신한다. 같은 키 계산이 이미
    돌고 있으면 또 시작하지 않는다. 저장소 생성 id가 다르면 다른 묶음이라 예전 결과로 쓰지 않는다.
    """

    def __init__(self, size: int = MAX_ENTRIES, max_age: timedelta = MAX_AGE) -> None:
        self.size = size
        self.max_age = max_age
        self._items: OrderedDict[Hashable, tuple[Any, datetime]] = OrderedDict()
        self._last: OrderedDict[Hashable, tuple[Hashable, Any, datetime]] = OrderedDict()
        self._lock = threading.Lock()
        self._busy: dict[Hashable, threading.Lock] = {}
        self._running: set[Hashable] = set()  # 뒤에서 계산 중인 키
        self._threads: list[threading.Thread] = []

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._last.clear()

    def wait(self, timeout: float = 60.0) -> None:
        """뒤에서 도는 갱신이 끝날 때까지 기다린다 (시험·측정용)."""
        with self._lock:
            threads, self._threads = list(self._threads), []
        for t in threads:
            t.join(timeout)

    def _previous(self, family: Hashable, key: Hashable) -> tuple[Any, datetime] | None:
        with self._lock:
            last = self._last.get(family)
            if last is None or last[0] == key:
                return None
            return last[1], last[2]

    def _store(self, key: Hashable, family: Hashable | None, value: Any) -> datetime:
        at = datetime.now(UTC).replace(microsecond=0)
        with self._lock:
            self._items[key] = (value, at)
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)
            if family is not None:
                self._last[family] = (key, value, at)
                self._last.move_to_end(family)
                while len(self._last) > self.size:
                    self._last.popitem(last=False)
            self._busy.pop(key, None)
        return at

    def _refresh(self, key: Hashable, family: Hashable, compute: Callable[[], Any]) -> None:
        """뒤 스레드로 새 계산을 한 번 시작한다 (같은 키가 돌고 있으면 그대로 둔다)."""
        with self._lock:
            if key in self._running:
                return
            self._running.add(key)
            busy = self._busy.setdefault(key, threading.Lock())

        def go() -> None:
            try:
                with busy:
                    if key in self._items:  # 그새 다른 호출이 계산했다
                        return
                    self._store(key, family, compute())
            except Exception:  # noqa: BLE001 - 갱신 실패: 다음 호출이 다시 계산한다
                logger.warning("판매 분석 뒤 계산 실패", exc_info=True)
            finally:
                with self._lock:
                    self._running.discard(key)

        thread = threading.Thread(target=go, name="sales-cache-refresh", daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def __len__(self) -> int:
        return len(self._items)

    def _fresh(self, key: Hashable, now: datetime) -> tuple[Any, datetime] | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if now - item[1] > self.max_age:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return item

    def get_or_compute[T](
        self,
        key: Hashable,
        compute: Callable[[], T],
        now: datetime,
        family: Hashable | None = None,
        *,
        stale_ok: bool = True,
    ) -> Computed[T]:
        """캐시에 있으면 꺼내고, 같은 묶음의 예전 결과가 있으면(`stale_ok`) 그걸 주고 뒤에서
        갱신, 둘 다 없으면 여기서 계산한다 (같은 키는 한 번만)."""
        found = self._fresh(key, now)
        if found is not None:
            return Computed(found[0], found[1], True)
        if family is not None and stale_ok:
            previous = self._previous(family, key)
            if previous is not None:
                self._refresh(key, family, compute)
                return Computed(previous[0], previous[1], True, stale=True)
        with self._lock:
            busy = self._busy.setdefault(key, threading.Lock())
        with busy:  # 같은 키는 한 번만 계산 (뒤에서 돌고 있으면 그 결과를 기다린다)
            found = self._fresh(key, now)
            if found is not None:
                return Computed(found[0], found[1], True)
            value = compute()
            return Computed(value, self._store(key, family, value), False)


CACHE = ResultCache()


def normalize(inputs: Mapping[str, Any]) -> str:
    """입력을 키 문자열로 (키 순서·공백과 상관없이 같은 입력은 같은 값)."""
    return json.dumps(
        inputs, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def cached[T](  # noqa: PLR0913 - 키 재료를 그대로 받는다
    skill: str,
    store: SalesStore,
    tenant_id: str,
    inputs: Mapping[str, Any],
    catalog_id: str,
    catalog_version: str | None,
    now: datetime,
    compute: Callable[[], T],
    cache: ResultCache | None = None,
    *,
    stale_ok: bool = True,
) -> Computed[T]:
    """판매 저장소를 읽는 계산 하나를 캐시로 감싼다 (분석 skill들이 같이 쓰는 함수).

    묶음(family) = 키에서 저장소 버전만 뺀 것. 같은 묶음의 예전 결과가 있으면 먼저 준다
    (`stale_ok=False`면 기다려 새로 계산한다: 미리 계산)."""
    family = (
        skill, tenant_id, normalize(inputs), catalog_id, catalog_version,
        now.astimezone(KST).date().isoformat(), store.cache_token, store.store_id,
    )  # fmt: skip
    key = (family, store.data_version(tenant_id))
    return (cache or CACHE).get_or_compute(key, compute, now, family, stale_ok=stale_ok)


def stamp(evidence: Evidence, computed: Computed[Any]) -> Evidence:
    """근거에 계산 시각과 캐시에서 나왔는지를 남긴다 (collected_at = 계산 시각)."""
    at = computed.computed_at.astimezone(KST)
    if computed.stale:
        tail = " (판매 기록이 바뀌기 전 결과, 갱신 중)"
    else:
        tail = " (캐시에서 꺼냄)" if computed.hit else ""
    note = f"계산 {at:%Y-%m-%d %H:%M}{tail}"
    summary = f"{evidence.data_summary}, {note}" if evidence.data_summary else note
    return evidence.model_copy(
        update={"collected_at": computed.computed_at, "data_summary": summary}
    )


def mark[M: BaseModel](output: M, computed: Computed[Any]) -> M:
    """출력의 basis에 계산 시각과 예전 결과인지(stale)를 적는다. basis가 없으면 그대로."""
    basis = getattr(output, "basis", None)
    if not isinstance(basis, SalesBasis):
        return output
    return output.model_copy(
        update={
            "basis": basis.model_copy(
                update={"computed_at": computed.computed_at, "stale": computed.stale}
            )
        }
    )
