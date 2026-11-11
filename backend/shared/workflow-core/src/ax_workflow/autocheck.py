"""기획서 확정·PR 머지 자동 처리 판정 (코드로만 계산, LLM 없음).

`execute(confirm_spec)`·`execute(merge_pr)`에 보내는 params를 safety #171 규칙 키 그대로 만든다
(`safety/src/ax_safety/rules/thresholds.toml`, 근거 #166). 값은 모두 도메인 상태에서 계산하고,
요청 쪽(화면·A2A)이 보낸 숫자는 쓰지 않는다.

- 기획서: 질문 0·경고 0·근거 1 이상·되돌릴 수 없음 0이면 자동 확정. 질문이 남으면 "답 대기",
  그 밖에 걸리면 "교차검증 대기".
- PR: 마지막 검수 통과 + 아래 규칙에 안 걸리면 자동 머지. 걸리면 "교차검증 대기",
  되돌릴 수 없는 영역에 닿으면 "사람 확인".

규칙 기준값은 #171 `thresholds.toml`의 제안값을 옮겨 둔 것이다. 최종 판정은 safety가 다시 하고,
safety가 보류·거절하면 서비스가 "교차검증 대기"로 둔다 (두 곳이 어긋나도 막히는 쪽으로).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ax_workflow.models import PR, Para, Spec, State
from ax_workflow.trace import changed_paths_for

# ---- 되돌릴 수 없는 영역 (경로 접두사 → 이유). 제안 목록, 확정은 safety·사람 (#166 노트)
# 기획서 확정(confirm_spec)과 PR 머지(merge_pr) 둘 다 irreversible_count로 센다.
IRREVERSIBLE_PREFIXES: dict[str, str] = {
    "shop-api/payment/migrations/": "결제 데이터 삭제·변경 마이그레이션",
    "shop-api/accounts/pii/": "개인정보 필드",
    "shop-api/settlement/payout/": "실제 송금·정산 연동",
    # 캣북스 새 레포(Catbooks-demo/catbooks-demo) 구조: backend/shop-api/
    "backend/shop-api/payment/migrations/": "결제 데이터 삭제·변경 마이그레이션",
    "backend/shop-api/accounts/pii/": "개인정보 필드",
    "backend/shop-api/settlement/payout/": "실제 송금·정산 연동",
}

# ---- 머지 위험 경로 (#200). shop-web/이 허용 경로에 들어가면서(safety #201) 결제·개인정보
# 화면은 자동 머지하지 않는다. merge_pr의 irreversible_count에만 더해서 safety
# merge_irreversible로 항상 C가 된다. 기획서 확정은 코드를 바꾸지 않으므로 세지 않는다.
# 아래쪽은 safety 보호 경로(#203 `merge_protected_paths`)와 같은 값이다. safety가 어차피 C로
# 막으므로 planning도 자동 머지를 시도하지 않고 바로 사람 확인으로 둔다.
MERGE_RISK_PREFIXES: dict[str, str] = {
    "shop-web/checkout/": "결제 화면 (결제 요청·금액·실패 안내)",
    "shop-web/payment/": "결제 수단 입력 화면",
    "shop-web/account/": "회원·개인정보 화면",
    "shop-web/src/pages/Checkout.tsx": "결제 화면 (safety 보호 경로)",
    "shop-web/src/lib/auth.tsx": "로그인·인증 (safety 보호 경로)",
    "shop-web/src/lib/api.ts": "API 클라이언트 (safety 보호 경로)",
    "shop-web/src/lib/env.ts": "환경값 (safety 보호 경로)",
    "shop-api/src/ax_shop_api/db.py": "DB 스키마 (safety 보호 경로)",
    "shop-api/src/ax_shop_api/service.py": "주문·결제 로직 (safety 보호 경로)",
    "shop-api/src/ax_shop_api/promotions.py": "쿠폰 지급 로직 (safety 보호 경로)",
    "shop-api/src/ax_shop_api/seed.py": "시드 데이터 (safety 보호 경로)",
    ".github/": "CI (safety 보호 경로)",
    "safety/": "관문 규칙 (safety 보호 경로)",
    "contracts/": "부서 간 계약 (safety 보호 경로)",
    # 캣북스 새 레포(Catbooks-demo/catbooks-demo) 구조: frontend/shop-web/, backend/shop-api/.
    # 예전 구조와 같은 파일이라 같은 이유로 막는다. 허용 경로(PR_ALLOWED_PATHS)는 safety
    # thresholds.toml과 같은 값이라 safety 변경과 함께 바꾼다
    "frontend/shop-web/checkout/": "결제 화면 (결제 요청·금액·실패 안내)",
    "frontend/shop-web/payment/": "결제 수단 입력 화면",
    "frontend/shop-web/account/": "회원·개인정보 화면",
    "frontend/shop-web/src/pages/Checkout.tsx": "결제 화면 (safety 보호 경로)",
    "frontend/shop-web/src/lib/auth.tsx": "로그인·인증 (safety 보호 경로)",
    "frontend/shop-web/src/lib/api.ts": "API 클라이언트 (safety 보호 경로)",
    "frontend/shop-web/src/lib/env.ts": "환경값 (safety 보호 경로)",
    "backend/shop-api/src/ax_shop_api/db.py": "DB 스키마 (safety 보호 경로)",
    "backend/shop-api/src/ax_shop_api/service.py": "주문·결제 로직 (safety 보호 경로)",
    "backend/shop-api/src/ax_shop_api/promotions.py": "쿠폰 지급 로직 (safety 보호 경로)",
    "backend/shop-api/src/ax_shop_api/seed.py": "시드 데이터 (safety 보호 경로)",
    "backend/contracts/": "판매 서버 API 계약 고정본 (새 레포)",
}

# ---- #171 thresholds.toml merge_pr 규칙 (제안값, generate_pr와 같은 크기·경로 기준)
PR_MAX_LINES = 300  # pr_changed_lines: changed_lines > 300
PR_MAX_FILES = 20  # pr_changed_files: changed_files > 20
# pr_allowed_paths·merge_allowed_paths (safety #201과 같은 값)
PR_ALLOWED_PATHS: tuple[str, ...] = ("shop-api/", "shop-web/", "docs/")
REVIEW_MISMATCH_LIMIT = 2  # pr_review_mismatch: review_mismatch_count >= 2

WAIT_ANSWER = "답 대기"
WAIT_CROSS_CHECK = "교차검증 대기"
WAIT_HUMAN = "사람 확인"


@dataclass
class Check:
    """판정 결과. `params`는 execute에 그대로 보내고, `rules`는 걸린 #171 규칙 키."""

    params: dict[str, Any]
    rules: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rules


def irreversible_paths(paths: list[str]) -> list[str]:
    """되돌릴 수 없는 영역에 닿는 경로."""
    return [p for p in paths if any(p.startswith(prefix) for prefix in IRREVERSIBLE_PREFIXES)]


# ---- 에이전트 설정·실행 훅 (고객사·레포와 상관없이, 깊이와 상관없이)
# AI가 만든 PR이 이 파일을 바꾸면 머지 뒤 이 레포를 여는 개발자의 Claude Code·Codex·에디터나
# CI가 그 지시·훅·스크립트를 자동으로 읽고 실행한다 (AGENTS.md 공격, 2026-11 Postman 훅 사건과
# 같은 구조). 그래서 자동 머지하지 않고 항상 사람이 본다.
AGENT_CONFIG_DIRS: dict[str, str] = {
    ".claude": "Claude Code 설정·훅",
    ".codex": "Codex 설정",
    ".cursor": "Cursor 설정",
    ".gemini": "Gemini CLI 설정",
    ".vscode": "에디터 작업·실행 설정",
    ".devcontainer": "개발 컨테이너 설정",
    ".husky": "git 훅",
    ".githooks": "git 훅",
    ".github": "CI·Actions (copilot-instructions.md 포함)",
    # 다른 코딩 에이전트·에디터 (Postman 설치 프로그램이 노린 목록과 SafeDep 정리 참고)
    ".agents": "에이전트 설정",
    ".agent": "에이전트 설정",
    ".kiro": "Kiro 지시 파일",
    ".windsurf": "Windsurf 지시 파일",
    ".roo": "Roo 지시 파일",
    ".continue": "Continue 설정",
    ".amazonq": "Amazon Q 지시 파일",
    ".opencode": "opencode 설정",
    ".factory": "Factory 설정",
    ".idea": "JetBrains 실행 구성",
    ".run": "JetBrains 실행 구성",
    # safety #517이 더한 것 (다른 CI)
    ".gitlab": "GitLab CI",
    ".circleci": "CircleCI",
}
AGENT_CONFIG_FILES: dict[str, str] = {
    "claude.md": "에이전트 지시 파일",
    "agents.md": "에이전트 지시 파일",
    "gemini.md": "에이전트 지시 파일",
    ".mcp.json": "MCP 서버 설정",
    ".cursorrules": "에이전트 지시 파일",
    ".pre-commit-config.yaml": "git 훅",
    ".npmrc": "패키지 설치 설정",
    "package.json": "설치·실행 스크립트 (postinstall 등)",
    "pyproject.toml": "설치·빌드 설정",
    "setup.py": "설치 스크립트",
    "makefile": "빌드 스크립트",
    ".clinerules": "에이전트 지시 파일",
    ".windsurfrules": "에이전트 지시 파일",
    "copilot-instructions.md": "에이전트 지시 파일",
    "conftest.py": "pytest가 자동으로 실행하는 파일",
    "setup.cfg": "설치·빌드 설정",
    "tox.ini": "테스트 실행 설정",
    "noxfile.py": "테스트 실행 스크립트",
    "requirements.txt": "의존성 (새 패키지 = 공급망)",
    "composer.json": "설치·실행 스크립트 (post-install-cmd)",
    "gemfile": "Bundler가 읽을 때 실행",
    "taskfile.yml": "작업 실행 스크립트",
    ".envrc": "direnv가 폴더에 들어갈 때 실행",
    ".gitmodules": "하위 저장소 주소",
    "dockerfile": "이미지 빌드 스크립트",
    # ---- safety #517이 더한 것 (safety `paths_named`와 같은 목록으로 맞춘다)
    # 잠금 파일: package.json은 그대로 두고 받을 주소만 바꿔치기할 수 있다
    "package-lock.json": "잠금 파일 (받을 주소)",
    "npm-shrinkwrap.json": "잠금 파일 (받을 주소)",
    "pnpm-lock.yaml": "잠금 파일 (받을 주소)",
    "yarn.lock": "잠금 파일 (받을 주소)",
    "bun.lock": "잠금 파일 (받을 주소)",
    "bun.lockb": "잠금 파일 (받을 주소)",
    "uv.lock": "잠금 파일 (받을 주소)",
    "poetry.lock": "잠금 파일 (받을 주소)",
    "pipfile.lock": "잠금 파일 (받을 주소)",
    "composer.lock": "잠금 파일 (받을 주소)",
    "gemfile.lock": "잠금 파일 (받을 주소)",
    # 패키지 관리자 설정: 받는 곳을 바꾸거나 설치 때 코드를 실행한다
    ".yarnrc": "패키지 설치 설정",
    ".yarnrc.yml": "패키지 설치 설정",
    ".pnpmfile.cjs": "설치 때 실행되는 코드",
    "pnpm-workspace.yaml": "패키지 설치 설정",
    "bunfig.toml": "패키지 설치 설정",
    "uv.toml": "패키지 설치 설정",
    "pip.conf": "패키지 설치 설정",
    "pipfile": "의존성",
    # 다른 CI: 고객사 레포가 GitHub만 쓴다는 보장이 없다
    ".gitlab-ci.yml": "GitLab CI",
    "jenkinsfile": "Jenkins CI",
    "azure-pipelines.yml": "Azure Pipelines",
    "bitbucket-pipelines.yml": "Bitbucket Pipelines",
    # 나머지: 도구가 자동으로 읽는 파일이거나 있는 이름의 다른 표기
    ".env": "환경값 (도구가 자동으로 읽음)",
    ".devcontainer.json": "개발 컨테이너 설정",
    ".aider.conf.yml": "Aider 설정",
    "gnumakefile": "빌드 스크립트",
    "taskfile.yaml": "작업 실행 스크립트",
}


def path_segments(path: str) -> list[str]:
    """이름 비교용 경로 조각 (safety `ax_safety.rules.path_segments`와 같은 규칙).

    대소문자, `\\` 구분자, `./`·`../`·끝의 `/`, Windows가 무시하는 끝의 점·공백(`CLAUDE.md.`),
    대체 스트림(`CLAUDE.md::$DATA`)을 같은 파일로 본다."""
    segments = path.replace("\\", "/").split("/")
    cleaned = (segment.split(":", 1)[0].rstrip(" .").casefold() for segment in segments)
    return [segment for segment in cleaned if segment]


def agent_config_reason(path: str) -> str | None:
    """에이전트 설정·실행 훅 파일이면 그 이유 (아니면 None).

    조각 하나라도 이름이 같으면 걸린다 (폴더·파일 구분 없이, 끝 조각도). `.claude`라는 심볼릭
    링크나 `.claude/`처럼 끝 조각이 폴더 이름인 경로도 잡는다."""
    for part in path_segments(path):
        reason = AGENT_CONFIG_DIRS.get(part) or AGENT_CONFIG_FILES.get(part)
        if reason:
            return reason
    return None


def merge_risk_paths(paths: list[str]) -> list[str]:
    """머지하면 사람이 봐야 하는 경로: 되돌릴 수 없는 영역 + 머지 위험 경로(#200)
    + 에이전트 설정·실행 훅 파일."""
    prefixes = (*IRREVERSIBLE_PREFIXES, *MERGE_RISK_PREFIXES)
    return [
        p
        for p in paths
        if any(p.startswith(prefix) for prefix in prefixes) or agent_config_reason(p)
    ]


def planned_area(index: int) -> str:
    """기획서의 n번째 요구사항이 바꿀 영역 (목 규칙: 첫 요구사항은 백엔드, 나머지는 화면)."""
    return "shop-api/cart/" if index == 0 else "shop-web/checkout/"


def planned_paths(spec: Spec, para: Para, index: int) -> list[str]:
    """요구사항 문단이 바꿀 파일 경로. 문단에 경로가 적혀 있으면 그것, 없으면 역추적 규칙."""
    if para.paths:
        return list(dict.fromkeys(para.paths))
    return changed_paths_for(spec.id, para.id, planned_area(index))


# ---------------------------------------------------------------- 기획서 (confirm_spec)


def spec_params(spec: Spec) -> dict[str, Any]:
    """`confirm_spec` params: open_questions·warnings·basis_count·irreversible_count."""
    reqs = [p for p in spec.paras if p.kind == "req"]
    return {
        "open_questions": 1 if spec.question else 0,
        "warnings": sum(1 for p in spec.paras if p.warn),
        "basis_count": len(spec.basis),
        "irreversible_count": sum(
            1 for i, p in enumerate(reqs) if irreversible_paths(planned_paths(spec, p, i))
        ),
    }


def spec_check(spec: Spec) -> Check:
    params = spec_params(spec)
    rules: list[str] = []
    if params["open_questions"] > 0:
        rules.append("spec_open_question")
    if params["warnings"] > 0:
        rules.append("spec_rejected_warning")
    if params["basis_count"] < 1:
        rules.append("spec_no_basis")
    if params["irreversible_count"] > 0:
        rules.append("irreversible")
    return Check(params=params, rules=rules)


def spec_wait_state(check: Check) -> str:
    return WAIT_ANSWER if "spec_open_question" in check.rules else WAIT_CROSS_CHECK


# ---------------------------------------------------------------- PR (merge_pr)


def _para_key(state: State, pr: PR) -> str | None:
    task = next((t for t in state.tasks if t.id == pr.task), None)
    return f"{task.spec}:{task.para}" if task else None


def _incident_paras(state: State) -> dict[str, str]:
    """장애 표시가 있는 문단 → 사건 번호."""
    return {f"{s.id}:{p.id}": p.incident for s in state.specs for p in s.paras if p.incident}


def linked_incident_paras(state: State, pr: PR) -> dict[str, str]:
    """이 PR의 문단 + 같은 파일을 바꾼 다른 PR의 문단 중 장애 표시(link_incident)가 있는 것."""
    marked = _incident_paras(state)
    linked: dict[str, str] = {}
    own = _para_key(state, pr)
    if own in marked:
        linked[own] = marked[own]
    paths = set(pr.changed_paths)
    for other in state.prs:
        if other.id == pr.id or not paths & set(other.changed_paths):
            continue
        key = _para_key(state, other)
        if key in marked:
            linked[key] = marked[key]
    return linked


def linked_incident_count(state: State, pr: PR) -> int:
    """장애 표시 문단 연결 수."""
    return len(linked_incident_paras(state, pr))


def pr_params(state: State, pr: PR) -> dict[str, Any]:
    """`merge_pr` params: generate_pr와 같은 크기·경로 + 검수 불일치·장애 연결·되돌릴 수 없음."""
    paths = sorted(set(pr.changed_paths))
    return {
        "changed_lines": pr.gate.lines,
        "changed_files": pr.gate.files,
        "changed_paths": paths,
        "review_mismatch_count": sum(1 for r in pr.reviews if r == "불일치"),
        "linked_incident_count": linked_incident_count(state, pr),
        "irreversible_count": len(merge_risk_paths(paths)),
    }


def pr_check(state: State, pr: PR) -> Check:
    params = pr_params(state, pr)
    rules: list[str] = []
    if params["changed_lines"] > PR_MAX_LINES:
        rules.append("pr_changed_lines")
    if params["changed_files"] > PR_MAX_FILES:
        rules.append("pr_changed_files")
    if not all(any(p.startswith(a) for a in PR_ALLOWED_PATHS) for p in params["changed_paths"]):
        rules.append("pr_allowed_paths")
    if params["review_mismatch_count"] >= REVIEW_MISMATCH_LIMIT:
        rules.append("pr_review_mismatch")
    if params["linked_incident_count"] > 0:
        rules.append("pr_linked_incident")
    if params["irreversible_count"] > 0:
        rules.append("irreversible")
    return Check(params=params, rules=rules)


def pr_wait_state(check: Check) -> str:
    """되돌릴 수 없는 영역이나 교차검증 근거(`merge_cross_*`)에 걸리면 사람 확인."""
    if "irreversible" in check.rules or any(r.startswith("merge_cross_") for r in check.rules):
        return WAIT_HUMAN
    return WAIT_CROSS_CHECK
