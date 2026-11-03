"""오늘 판정 게시판: 오늘(한국 날짜) 이미 낸 네이버 급상승 판정과 자동 묶음을 프로세스 안에 남긴다.

trend_popular처럼 네이버를 부르지 않는 skill이 "오늘 판정 결과가 있으면" 그것만 붙이려고 쓴다.
- 판정: trend_scan·trend_candidates·content_recommend가 낸 판정(`TrendVerdict`)을
  그룹 이름·키워드마다
  남긴다(공백·대소문자 무시). 같은 이름을 다시 판정하면 새 값으로 바꾼다
- 자동 묶음: trend_candidates가 기준을 넘어 묶은 이름 쌍
- 한국 날짜가 바뀌면 비운다. 저장하지 않는다(프로세스를 다시 켜면 빈다)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone

from ax_contracts.schemas.data import TrendVerdict

KST = timezone(timedelta(hours=9))


def _today() -> date:
    return datetime.now(UTC).astimezone(KST).date()


def _key(name: str) -> str:
    return "".join(name.lower().lstrip("#").split())


@dataclass
class TodayBoard:
    today: Callable[[], date] = _today
    _day: date | None = None
    _verdicts: dict[str, TrendVerdict] = field(default_factory=dict)
    _pairs: list[tuple[str, str]] = field(default_factory=list)

    def _roll(self) -> None:
        day = self.today()
        if day != self._day:
            self._day, self._verdicts, self._pairs = day, {}, []

    def record_verdict(self, names: Iterable[str], verdict: TrendVerdict) -> None:
        self._roll()
        for name in names:
            if name and _key(name):
                self._verdicts[_key(name)] = verdict

    def verdict(self, names: Iterable[str]) -> TrendVerdict | None:
        """이름들 중 오늘 판정이 있는 것 (급상승이 있으면 그것을 먼저)."""
        self._roll()
        found = [self._verdicts[_key(n)] for n in names if n and _key(n) in self._verdicts]
        rising = [v for v in found if v.status == "rising"]
        return (rising or found or [None])[0]

    def record_pair(self, a: str, b: str) -> None:
        self._roll()
        pair = (_key(a), _key(b))
        if pair not in self._pairs:
            self._pairs.append(pair)

    def pairs(self) -> list[tuple[str, str]]:
        self._roll()
        return list(self._pairs)

    def clear(self) -> None:
        self._day, self._verdicts, self._pairs = None, {}, []


BOARD = TodayBoard()  # 서버 프로세스 하나가 같이 쓴다
