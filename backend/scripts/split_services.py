"""서비스 폴더(`services/<id>/`)를 대응표(`ax_agent_template/services.py`)에서 만든다.

카드는 원래 부서 카드(`domains/<부서>/agent-card.json`)에서 그 서비스 skill만 골라 만든다.
skill 설명·스키마·허용 요청자는 지어내지 않고 부서 카드 그대로 옮긴다.
대응표를 고친 뒤 `python backend/scripts/split_services.py`로 다시 만든다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 부서 이름 → (domains 폴더, 파이썬 패키지, 카드 경로 환경변수, 배포 이름)
DEPARTMENTS = {
    "support": ("support", "support_agent", "SUPPORT_AGENT_CARD", "ax-agent-support"),
    "marketing": ("marketing", "marketing_agent", "MARKETING_AGENT_CARD", "ax-agent-marketing"),
    "finance": ("finance", "finance_agent", "FINANCE_AGENT_CARD", "ax-agent-finance"),
    "data": ("data", "data_agent", "DATA_AGENT_CARD", "ax-agent-data"),
    "hr": ("hr", "hr_agent", "HR_AGENT_CARD", "ax-agent-hr"),
    "planning": ("planning", "planning_agent", "PLANNING_AGENT_CARD", "ax-agent-planning"),
    "infra": ("infra-dejafix", "infra_agent", "INFRA_AGENT_CARD", "ax-agent-infra"),
}
# 부서 이름 → 서비스 역할 환경변수. 같은 부서 앱을 서비스마다 따로 띄울 때 맡은 몫을 알린다
# (기획개발: planning = 기획서까지, dev = PR·머지, `planning_agent/dev_handoff.py`)
ROLE_ENVS = {"planning": "PLANNING_ROLE"}
# 직접 만든 서비스 (부서 패키지를 쪼개지 않음)
STANDALONE = {"orchestrator", "hosts"}


def load_services():
    path = ROOT / "shared/agent-template/src/ax_agent_template/services.py"
    spec = importlib.util.spec_from_file_location("services", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["services"] = mod  # dataclass가 모듈을 찾는다
    spec.loader.exec_module(mod)
    return mod.SERVICES


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build(svc) -> None:
    folder, package, card_env, dist = DEPARTMENTS[svc.department]
    dept_card = json.loads((ROOT / "domains" / folder / "agent-card.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in dept_card["skills"]}
    missing = [k for k in svc.skills if k not in by_id]
    if missing:
        raise SystemExit(f"{svc.id}: 부서 카드에 없는 skill {missing}")
    card = {
        **dept_card,
        "name": svc.id,
        "description": f"{svc.name} — {svc.department} 업무 중 이 서비스 skill만 맡는다. "
        + dept_card["description"],
        "url": f"http://{svc.id}:{svc.port}/a2a",
        "skills": [by_id[k] for k in svc.skills],
    }
    out = ROOT / "services" / svc.id
    write(out / "agent-card.json", json.dumps(card, ensure_ascii=False, indent=2) + "\n")
    mod = f"svc_{svc.id}"
    role_env = ROLE_ENVS.get(svc.department)
    role_line = f'os.environ.setdefault("{role_env}", "{svc.id}")\n' if role_env else ""
    write(out / "src" / mod / "__init__.py", "")
    write(
        out / "src" / mod / "main.py",
        f'''"""{svc.name} 서비스 에이전트 ({svc.id}, {svc.port}).

업무 로직은 `domains/{folder}`({package})를 그대로 쓰고, 이 폴더의 `agent-card.json`에 올린 skill만 받는다.
카드에 없는 skill은 템플릿 ACL이 막는다. 서비스 대응표: `ax_agent_template/services.py`.
"""

import os
from pathlib import Path

os.environ.setdefault("{card_env}", str(Path(__file__).resolve().parents[2] / "agent-card.json"))
{role_line}
from {package}.main import app  # noqa: E402

__all__ = ["app"]
''',
    )
    write(
        out / "pyproject.toml",
        f'''[project]
name = "ax-svc-{svc.id}"
version = "0.1.0"
description = "services/{svc.id} — {svc.name} (domains/{folder})"
requires-python = ">=3.12"
dependencies = ["{dist}"]

[tool.uv.sources]
{dist} = {{ workspace = true }}

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{mod}"]
''',
    )
    write(
        out / "compose.yml",
        f'''services:
  {svc.id}:
    build:
      context: ../..
      dockerfile: services/{svc.id}/Dockerfile
    ports:
      - "{svc.port}:{svc.port}"
    env_file:
      - path: ../../.env
        required: false
      - path: ./.env
        required: false
''',
    )
    write(
        out / "Dockerfile",
        f'''# 빌드 컨텍스트는 backend/: docker build -f services/{svc.id}/Dockerfile .
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \\
    UV_LINK_MODE=copy \\
    UV_PYTHON_DOWNLOADS=never \\
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package ax-svc-{svc.id}

EXPOSE {svc.port}
CMD ["uvicorn", "{mod}.main:app", "--host", "0.0.0.0", "--port", "{svc.port}"]
''',
    )
    rows = "\n".join(f"| `{k}` | {by_id[k]['grade']} |" for k in svc.skills)
    write(
        out / "README.md",
        f'''# services/{svc.id} — {svc.name}

포트 {svc.port} · 업무 로직 `domains/{folder}` · 화면 서비스 id `{svc.id}`

| skill | 등급 |
| --- | --- |
{rows}

- 실행: `uv run --package ax-svc-{svc.id} uvicorn {mod}.main:app --port {svc.port}` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `{svc.department}`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
''',
    )


def main() -> None:
    services = load_services()
    for svc in services:
        if svc.id not in STANDALONE:
            build(svc)
            print(f"services/{svc.id}  {svc.port}  skill {len(svc.skills)}")


if __name__ == "__main__":
    main()
