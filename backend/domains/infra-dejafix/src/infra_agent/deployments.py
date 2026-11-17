"""배포 이력: 원인 진단과 롤백 대상(직전 정상 버전)을 정하는 원본.

실제 배포 시스템이 없어 JSON 파일 하나가 배포 기록이다. 기본은 패키지 안의 `data/deployments.json`
(planning 초기 데이터와 같은 버전·시각)이고 `INFRA_DEPLOYMENTS_FILE`로 바꿀 수 있다.
롤백한 배포는 "되돌림"으로 표시해 직전 정상 버전을 고를 때 건너뛴다.

배포 도구(GitHub Actions·Jenkins)가 연결된 서비스는 이력 미리 받기(`deploy_poll`)가
`replace_service`로 그 서비스의 시연 이력을 도구의 성공한 실행으로 바꾼다. 도구 실행의 `deploy_id`는
`<서비스>:<실행 id>`라 다시 받아도 같고, 그래서 "되돌림" 표시가 이어진다.
연결이 있는 서비스의 시연 이력은 첫 받기 전에도 쓰지 않는다(진단 후보·롤백 대상에서 뺀다).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

from infra_agent import deploy_tools
from infra_agent.deploy_tools import ToolRun


@dataclass(frozen=True)
class Deployment:
    deploy_id: str
    service: str
    version: str
    deployed_at: datetime
    components: tuple[str, ...]
    change_summary: str
    irreversible: tuple[str, ...] = ()
    # 이 배포가 실제로 바꾼 대상. `SHOP_PAYMENT_CONFIG`면 쇼핑몰 결제 설정 버전이라, 롤백이
    # 쇼핑몰 결제 설정을 이 기록의 이전 버전으로 실제로 되돌린다(시연 "잘못된 배포").
    # None은 기록만 있는 배포
    applies_to: str | None = None
    # 배포 도구에서 받은 실행이면 도구 이름(`github_actions`·`jenkins`). None은 시연 이력.
    # 도구 실행은 서비스 전체를 배포하므로 그 서비스가 가진 구성요소(시연 이력 기준)를 모두 단다
    source: str | None = None
    # 우리가 롤백으로 띄운 다시 배포. 장애를 만든 변경이 아니라 원인 후보로 보지 않는다
    is_rollback: bool = False
    # 버전이 진짜 버전이 아니라 커밋 앞자리(GitHub 실행 제목에 버전이 없음)다. 이 버전으로는
    # 다시 배포하지 않는다
    version_from_sha: bool = False


@dataclass
class Redeploy:
    """롤백으로 띄운 다시 배포 기록. 도구 이력에서 그 실행을 찾아 버전·롤백 표시를 맞춘다."""

    service: str
    version: str
    at: datetime
    run_id: str | None = None  # 도구가 준 실행 id (Jenkins는 대기열 id라 빌드와 다르다)
    claimed: str | None = None  # 이 기록으로 찾은 도구 실행 id


# 다시 배포 기록보다 이만큼 앞선 실행까지 같은 실행으로 본다 (도구와 우리 시계 차이)
REDEPLOY_SKEW = timedelta(minutes=2)


SHOP_PAYMENT_CONFIG = "shop_payment_config"


def _parse(row: dict[str, Any]) -> Deployment:
    return Deployment(
        deploy_id=row["deploy_id"],
        service=row["service"],
        version=row["version"],
        deployed_at=datetime.fromisoformat(row["deployed_at"].replace("Z", "+00:00")),
        components=tuple(row.get("components", [])),
        change_summary=row.get("change_summary", ""),
        irreversible=tuple(row.get("irreversible", [])),
        applies_to=row.get("applies_to") if isinstance(row.get("applies_to"), str) else None,
    )


def connected_services() -> set[str]:
    """보관함에 배포 도구가 연결된 서비스. 보관함이 없거나 깨졌으면 빈 집합."""
    return {tool.service for tool in deploy_tools.all_tools()}


def load_rows() -> list[dict[str, Any]]:
    override = os.getenv("INFRA_DEPLOYMENTS_FILE")
    if override:
        text = Path(override).read_text(encoding="utf-8")
    else:
        text = (
            resources.files("infra_agent")
            .joinpath("data/deployments.json")
            .read_text(encoding="utf-8")
        )
    return list(json.loads(text).get("deployments", []))


@dataclass
class DeploymentHistory:
    deployments: list[Deployment]
    rolled_back: set[str] = field(default_factory=set)  # deploy_id
    redeploys: list[Redeploy] = field(default_factory=list)

    # 지금 배포 도구가 연결된 서비스 (보관함). 그 서비스의 시연 이력은 쓰지 않는다
    connected: Callable[[], set[str]] = field(default=connected_services, repr=False)
    # 서비스 → 구성요소 (시연 이력의 `components`에서). 도구 실행은 구성요소를 모르므로 이 값을 단다
    components_by_service: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for d in self.deployments:
            self._learn_components(d)

    def _learn_components(self, d: Deployment) -> None:
        if d.source is None and d.components:
            self.components_by_service.setdefault(d.service, set()).update(d.components)

    @classmethod
    def load(cls) -> DeploymentHistory:
        return cls([_parse(row) for row in load_rows()])

    def live(self) -> list[Deployment]:
        """쓰는 이력. 배포 도구가 연결됐거나 도구 이력이 있는 서비스는 시연 이력을 뺀다.

        그래서 연결 직후 첫 받기 전에는 그 서비스의 원인 후보·롤백 대상이 없다(원인 미확정 경로).
        """
        tooled = self.connected() | {d.service for d in self.deployments if d.source is not None}
        return [d for d in self.deployments if d.source is not None or d.service not in tooled]

    def for_component(self, component: str | None) -> list[Deployment]:
        """그 구성요소를 바꾼 배포, 최근 것이 앞.

        우리가 롤백으로 띄운 다시 배포(`is_rollback`)는 이전 버전으로 돌아간 것이라 넣지 않는다.
        """
        rows = [
            d
            for d in self.live()
            if not d.is_rollback and (component is None or component in d.components)
        ]
        return sorted(rows, key=lambda d: d.deployed_at, reverse=True)

    def find(self, service: str, version: str) -> Deployment | None:
        """같은 버전이 여러 번 배포됐으면(시연을 되풀이) 되돌리지 않은 것 중 가장 최근 것."""
        rows = [d for d in self.live() if d.service == service and d.version == version]
        live = [d for d in rows if d.deploy_id not in self.rolled_back]
        return max(live or rows, key=lambda d: d.deployed_at, default=None)

    def running(self, service: str) -> Deployment | None:
        """그 서비스에서 되돌리지 않은 가장 최근 배포 (지금 돌고 있는 버전)."""
        rows = [d for d in self.live() if d.service == service]
        rows = [d for d in rows if d.deploy_id not in self.rolled_back]
        return max(rows, key=lambda d: d.deployed_at, default=None)

    def previous_good(self, target: Deployment) -> Deployment | None:
        """같은 서비스에서 대상 배포 바로 앞의, 되돌리지 않은 배포."""
        earlier = [
            d
            for d in self.live()
            if d.service == target.service
            and d.deployed_at < target.deployed_at
            and d.deploy_id not in self.rolled_back
        ]
        return max(earlier, key=lambda d: d.deployed_at, default=None)

    def mark_rolled_back(self, target: Deployment) -> None:
        self.rolled_back.add(target.deploy_id)

    def record_redeploy(
        self, service: str, version: str, *, at: datetime, run_id: str | None
    ) -> Redeploy:
        """롤백으로 띄운 다시 배포를 남긴다. 다음 이력 받기에서 그 실행을 롤백으로 표시한다."""
        note = Redeploy(service=service, version=version, at=at, run_id=run_id)
        self.redeploys.append(note)
        return note

    def _claim(self, service: str, run: ToolRun) -> Redeploy | None:
        """이 실행이 우리가 띄운 다시 배포면 그 기록.

        실행 id를 알면 그 id만 맞춘다(바로 앞에 사람이 같은 버전을 배포해도 가져가지 않는다).
        실행 id를 모르면(GitHub가 id를 못 찾음, Jenkins는 대기열 id `queue-*`라 빌드 번호와 다름)
        같은 버전이 기록 시각 뒤에 처음 성공한 실행을 그 기록으로 본다.
        한 기록은 실행 하나만 맞춘다.
        """
        for note in self.redeploys:
            if note.service != service:
                continue
            if note.claimed is not None:
                if note.claimed == run.run_id:
                    return note
                continue
            if note.run_id is not None and not note.run_id.startswith("queue-"):
                matched = note.run_id == run.run_id
            else:
                matched = run.version == note.version and run.at >= note.at - REDEPLOY_SKEW
            if matched:
                note.claimed = run.run_id
                return note
        return None

    def replace_service(
        self, service: str, runs: list[ToolRun], *, tool: str = "deploy_tool"
    ) -> None:
        """그 서비스의 이력(시연 이력 포함)을 배포 도구의 성공한 실행으로 바꾼다.

        `deploy_id`가 `<서비스>:<실행 id>`라 "되돌림" 표시(`rolled_back`)는 다시 받아도 이어진다.
        우리가 롤백으로 띄운 실행은 디스패치한 버전과 롤백 표시(`is_rollback`)를 붙인다.
        """
        for d in self.deployments:
            self._learn_components(d)
        components = tuple(sorted(self.components_by_service.get(service, ())))
        fresh: list[Deployment] = []
        for run in sorted(runs, key=lambda r: r.at):
            if not run.ok:
                continue  # 실패·진행 중인 실행은 배포되지 않았다
            note = self._claim(service, run)
            who = f" · {run.actor}" if run.actor else ""
            fresh.append(
                Deployment(
                    deploy_id=f"{service}:{run.run_id}",
                    service=service,
                    version=note.version if note else run.version,
                    deployed_at=run.at,
                    components=components,
                    change_summary=f"{tool} 실행 {run.run_id}{who}"
                    + (" (롤백 다시 배포)" if note else ""),
                    source=tool,
                    is_rollback=note is not None,
                    version_from_sha=run.version_from_sha and note is None,
                )
            )
        self.deployments = [d for d in self.deployments if d.service != service] + fresh

    def next_version(self, service: str) -> str:
        """그 서비스의 마지막 배포 버전에서 끝자리를 하나 올린다 (v2.4.1 → v2.4.2)."""
        rows = [d for d in self.deployments if d.service == service]
        if not rows:
            return "v1.0.0"
        latest = max(rows, key=lambda d: d.deployed_at).version
        head, _, last = latest.rpartition(".")
        if not head:  # 점이 없는 버전(결제 설정 v2 등) → v2.1
            version = f"{latest}.1"
        else:
            version = f"{head}.{int(last) + 1}" if last.isdigit() else f"{head}.{last}.1"
        while self.find(service, version):
            head, _, last = version.rpartition(".")
            version = f"{head}.{int(last) + 1}"
        return version

    def record(
        self,
        *,
        service: str,
        version: str,
        components: tuple[str, ...],
        change_summary: str,
        at: datetime,
        applies_to: str | None = None,
    ) -> Deployment:
        """배포 하나를 기록한다 (시연 준비 `POST /api/infra/demo-deploy`). 재시작하면 사라진다."""
        deployment = Deployment(
            deploy_id=f"dep_demo_{len(self.deployments) + 1:04d}",
            service=service,
            version=version,
            deployed_at=at,
            components=components,
            change_summary=change_summary,
            applies_to=applies_to,
        )
        self.deployments.append(deployment)
        self._learn_components(deployment)
        return deployment
