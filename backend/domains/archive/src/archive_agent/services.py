"""아카이브가 쓰는 부품 묶음(저장소·클라이언트·시계·문장 생성). 핸들러는 이 묶음 하나만 받는다.

목 모드(`HR_MOCK=1`)에서는 memory·안전 레이어를 목으로 대체한다. 테스트·데모는 `set_services`로
고정 시계를 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from archive_agent.audit import AuditLog
from archive_agent.clients.memory import MemoryClient, MockMemoryClient
from archive_agent.clients.safety import GateClient, MockGate, TemplateGate
from archive_agent.config import ArchiveSettings, Clock, system_clock
from archive_agent.judge import DiagnoseFn, JudgeFn, judge_via_hr, request_diagnosis_via_hr
from archive_agent.narrative import Narrator
from archive_agent.org_areas import MOCK_AREAS, load_org_areas
from archive_agent.retrieval.cards import DenseCardSearch, build_dense_search
from archive_agent.retrieval.duplicates import SameDecisionJudge, TitleSuggester
from archive_agent.retrieval.verify import RelevanceVerifier
from archive_agent.store import Store
from archive_agent.util import dumps, loads
from ax_agent_template import company_ai
from ax_agent_template.llm import LLMClient


class IdempotencyStore:
    """같은 요청을 두 번 처리하지 않도록 첫 결과를 저장한다."""

    def __init__(self, store: Store, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def get(self, key: str) -> dict[str, Any] | None:
        row = self._store.one("SELECT result FROM idempotency WHERE key = ?", (key,))
        return loads(row["result"]) if row else None

    def put(self, key: str, result: dict[str, Any]) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO idempotency(key, result, created_at) VALUES (?, ?, ?)",
            (key, dumps(result), self._clock().isoformat()),
        )


@dataclass
class Services:
    settings: ArchiveSettings
    store: Store
    clock: Clock
    memory: MemoryClient
    gate: GateClient
    narrator: Narrator
    audit: AuditLog
    idem: IdempotencyStore
    # 계획 비교가 아는 영역
    areas: Callable[[], list[str]] = field(default=lambda: list(MOCK_AREAS))
    dense: DenseCardSearch | None = None  # None이면 계획 비교는 Dice로 찾는다
    verifier: RelevanceVerifier | None = None  # None이면 Dense 후보를 LLM으로 거르지 않는다
    llm: Any = None  # None이면 LLM이 필요한 기능은 쓸 수 없다
    judge: JudgeFn | None = None  # 이슈 단계 시간의 판정(인사 AI). None이면 판정을 비워 둔다
    # 판정 저장 뒤 반복 확인(인사 AI → 총괄 진단 요청). None이면 하지 않는다
    diagnose: DiagnoseFn | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def build_services(
    settings: ArchiveSettings | None = None,
    *,
    clock: Clock | None = None,
    llm: LLMClient | None = None,
    dense: DenseCardSearch | None = None,
    verifier: RelevanceVerifier | None = None,
    judge: JudgeFn | None = None,
    diagnose: DiagnoseFn | None = None,
) -> Services:
    """설정에 맞는 부품을 만든다. 목 모드면 목 클라이언트와 가상 영역을 쓴다."""
    settings = settings or ArchiveSettings.from_env()
    clock = clock or system_clock
    store = Store(settings.db_path)
    memory: MemoryClient = MockMemoryClient(store, clock)
    if settings.memory_mode == "http":
        from archive_agent.clients.real import HttpMemoryClient

        memory = HttpMemoryClient()
    if llm is None and not settings.mock:
        # 실제 모드에서 LLM 설정(`HR_LLM_*` 또는 `LLM_*`)이 있으면 문장 생성과 검증에 쓴다
        llm_settings = company_ai.settings_for("hr")  # 회사 키가 있으면 회사 값(11/23)
        if llm_settings.model and llm_settings.base_url:
            llm = LLMClient("hr", llm_settings)
    if dense is None and settings.dense_search and not settings.mock:
        dense = build_dense_search(settings.db_path)
    if verifier is None and settings.dense_verify and llm is not None:
        verifier = RelevanceVerifier(llm)
    gate: GateClient
    if settings.mock:
        gate = MockGate(store, clock, hold_ttl_minutes=settings.hold_ttl_minutes)
    else:  # 실제 안전 레이어는 템플릿 execute()를 거친다
        gate = TemplateGate()
    areas: Callable[[], list[str]] = (lambda: list(MOCK_AREAS)) if settings.mock else load_org_areas
    services = Services(
        settings=settings,
        store=store,
        clock=clock,
        memory=memory,
        gate=gate,
        narrator=Narrator(llm),
        audit=AuditLog(store, clock),
        idem=IdempotencyStore(store, clock),
        areas=areas,
        dense=dense,
        verifier=verifier,
        llm=llm,
        judge=judge if judge is not None else (None if settings.mock else judge_via_hr),
        diagnose=(
            diagnose
            if diagnose is not None
            else (None if settings.mock else request_diagnosis_via_hr)
        ),
    )
    if llm is not None and dense is not None and not settings.mock:
        # 같은 주제로 본 결정 쌍을 가르고(같은 결정/내용이 바뀜/별개) 묶음의 대표 제목을 제안한다
        services.extras.setdefault("same_judge", SameDecisionJudge(llm))
        services.extras.setdefault("title_suggester", TitleSuggester(llm))
    return services


_services: Services | None = None


def get_services() -> Services:
    """프로세스 안의 기본 부품 묶음. 처음 부를 때 환경변수로 만든다."""
    global _services
    if _services is None:
        _services = build_services()
    return _services


def set_services(services: Services | None) -> None:
    """테스트·데모에서 부품 묶음을 바꾼다(None이면 다음 호출 때 다시 만든다)."""
    global _services
    _services = services
