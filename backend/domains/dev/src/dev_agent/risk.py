"""코딩 결과의 위험도 (LLM 없이, 바뀐 파일로만 정한다). 리뷰·머지 확인을 위험도에 맞춘다.

바뀐 줄 수로는 나누지 않는다: 결제 한 줄도 위험하다. 바뀐 파일 중 가장 위험한 파일이 등급이다.

- high(높음): 안전 규칙 머지 위험 경로(결제·인증·API 클라이언트·환경값·DB·쿠폰·CI·계약 등),
  에이전트 설정 파일, 빌드·배포·의존성 파일, 서버 코드, 장애 이력 경로
  → 리뷰 1번 + 사람 확인 뒤 머지
- low(낮음): 스타일·문서·이미지·문구 파일만 → LLM 리뷰 없이 CI 통과면 자동 머지
- medium(보통): 그 밖(화면 코드 등) → 리뷰 1번, 고친 코드는 CI로 확인
"""

from __future__ import annotations

from typing import Literal

from ax_workflow.autocheck import agent_config_reason, incident_paths, merge_risk_paths
from ax_workflow.models import State

Risk = Literal["low", "medium", "high"]

LOW_EXT = (
    ".css", ".scss", ".sass", ".less", ".md", ".mdx", ".txt", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".svg", ".ico",
)  # fmt: skip
TEXT_DIRS = ("locales", "locale", "i18n", "lang", "messages")  # 번역·문구 파일 폴더 (.json)
BUILD_NAMES = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml",
    "uv.lock", "requirements.txt", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "makefile", "nginx.conf", ".npmrc", ".nvmrc",
}  # fmt: skip
BUILD_PREFIXES = ("vite.config", "tsconfig", "next.config", "webpack.config", "babel.config")
BUILD_DIRS = ("deploy", "infra", ".github", "scripts")
SERVER_DIRS = ("backend", "server", "api")


def _parts(path: str) -> list[str]:
    return [x.lower() for x in path.replace("\\", "/").split("/") if x]


def _high_reason(path: str, incidents: set[str]) -> str | None:
    if merge_risk_paths([path]):
        return "머지 보호 경로"
    if agent_config_reason(path):
        return "에이전트 설정 파일"
    parts = _parts(path)
    name = parts[-1] if parts else ""
    if (
        name in BUILD_NAMES
        or name.startswith(BUILD_PREFIXES)
        or any(d in BUILD_DIRS for d in parts[:-1])
    ):
        return "빌드·배포·의존성 파일"
    if any(d in SERVER_DIRS or d.endswith("-api") for d in parts[:-1]):
        return "서버 코드"
    if path in incidents:
        return "장애 이력 경로"
    return None


def _low(path: str) -> bool:
    parts = _parts(path)
    name = parts[-1] if parts else ""
    if name.endswith(LOW_EXT):
        return True
    return name.endswith(".json") and any(d in TEXT_DIRS for d in parts[:-1])


def risk_of(state: State, paths: list[str]) -> tuple[Risk, str]:
    """(등급, 이유 한 줄). 이유는 화면·PR에 그대로 보인다."""
    incidents = incident_paths(state)
    for p in paths:
        why = _high_reason(p, incidents)
        if why:
            return "high", f"{why} {p}"
    if paths and all(_low(p) for p in paths):
        return "low", "스타일·문서·문구 파일만"
    return "medium", "화면·일반 코드"


LABEL = {"low": "낮음", "medium": "보통", "high": "높음"}
