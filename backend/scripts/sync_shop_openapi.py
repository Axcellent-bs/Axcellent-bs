"""판매 서버(캣북스) OpenAPI 고정본을 원본 레포에서 다시 받거나, 어긋났는지 확인한다.

판매 서버는 `Catbooks-demo/catbooks-demo`로 나갔다(레포 분리). 원본 계약은 그 레포의
`backend/contracts/openapi/shop-api.json`이고, AXcellent는 평가용 가짜 판매 서버 옆에 사본을 둔다.
사본마다 어느 커밋에서 받았는지(`OPENAPI_SOURCE`)를 옆 파이썬 파일에 적어 둔다.

    python scripts/sync_shop_openapi.py              # 원본 dev 최신으로 사본을 다시 받는다
    python scripts/sync_shop_openapi.py --ref <sha>  # 정한 커밋·브랜치로 받는다
    python scripts/sync_shop_openapi.py --check      # 사본이 원본 dev와 같은지만 본다(다르면 1)

공개 레포라 토큰 없이 읽는다. `GITHUB_TOKEN`이 있으면 커밋 조회에 붙인다(요청 한도).
표준 라이브러리만 쓴다.
새 사본(예: 데이터분석 가짜 판매 서버)이 생기면 `TARGETS`에 더한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = "Catbooks-demo/catbooks-demo"
SPEC_PATH = "backend/contracts/openapi/shop-api.json"
DEFAULT_REF = "dev"
BACKEND = Path(__file__).resolve().parents[1]
TIMEOUT_S = 20
_SOURCE = re.compile(r"(Catbooks-demo/catbooks-demo@)([0-9a-f]{40})")
_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


@dataclass(frozen=True)
class Target:
    pin: Path  # 고정본 사본
    source: Path  # `OPENAPI_SOURCE = "Catbooks-demo/catbooks-demo@<sha> ..."`가 적힌 파일


TARGETS = [
    Target(
        pin=BACKEND / "domains/finance/eval/shop_api_openapi.json",
        source=BACKEND / "domains/finance/eval/fake_shop.py",
    ),
]


def dump(spec: dict[str, Any]) -> str:
    """원본(`ax_shop_api.export_openapi`)과 같은 모양으로 쓴다."""
    return json.dumps(spec, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _get(url: str, accept: str) -> bytes:
    headers = {"Accept": accept, "User-Agent": "axcellent-sync-shop-openapi"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 (고정된 https 주소)
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        return response.read()


def resolve_ref(ref: str) -> str:
    """브랜치·태그·짧은 해시를 40자 커밋으로 바꾼다."""
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref
    body = _get(f"https://api.github.com/repos/{REPO}/commits/{ref}", "application/vnd.github.sha")
    sha = body.decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(f"{REPO}@{ref}의 커밋을 못 찾음")
    return sha


def fetch_spec(sha: str) -> dict[str, Any]:
    raw = _get(f"https://raw.githubusercontent.com/{REPO}/{sha}/{SPEC_PATH}", "application/json")
    return json.loads(raw)


def pinned_sha(target: Target) -> str | None:
    found = _SOURCE.search(target.source.read_text(encoding="utf-8"))
    return found.group(2) if found else None


def _operations(spec: dict[str, Any]) -> set[str]:
    return {
        f"{method.upper()} {path}"
        for path, item in (spec.get("paths") or {}).items()
        for method in item
        if method in _METHODS
    }


def spec_diff(local: dict[str, Any], upstream: dict[str, Any]) -> list[str]:
    """사본 → 원본으로 가면 무엇이 바뀌는지(경로·메서드 단위). 모양만 바뀌었으면 한 줄로."""
    mine, theirs = _operations(local), _operations(upstream)
    lines = [f"추가: {op}" for op in sorted(theirs - mine)]
    lines += [f"삭제: {op}" for op in sorted(mine - theirs)]
    if not lines and local != upstream:
        lines.append("경로는 같고 요청·응답 모양이 바뀜")
    return lines


def check(targets: list[Target], ref: str = DEFAULT_REF) -> list[str]:
    """어긋난 사본마다 한 줄씩. 비어 있으면 모두 원본과 같다."""
    sha = resolve_ref(ref)
    upstream = fetch_spec(sha)
    problems = []
    for target in targets:
        local = json.loads(target.pin.read_text(encoding="utf-8"))
        changes = spec_diff(local, upstream)
        if changes:
            name = (
                target.pin.relative_to(BACKEND)
                if target.pin.is_relative_to(BACKEND)
                else target.pin
            )
            problems.append(
                f"{name} (받은 커밋 {(pinned_sha(target) or '?')[:8]}, 원본 {ref} {sha[:8]}): "
                + "; ".join(changes)
            )
    return problems


def sync(targets: list[Target], ref: str = DEFAULT_REF) -> str:
    """사본을 원본 `ref`로 다시 쓰고 출처 줄의 커밋을 바꾼다. 받은 커밋을 돌려준다."""
    sha = resolve_ref(ref)
    text = dump(fetch_spec(sha))
    for target in targets:
        target.pin.write_text(text, encoding="utf-8", newline="\n")
        source = target.source.read_text(encoding="utf-8")
        updated, count = _SOURCE.subn(rf"\g<1>{sha}", source)
        if count != 1:
            raise RuntimeError(f"{target.source}: OPENAPI_SOURCE 커밋이 {count}개(하나여야 함)")
        target.source.write_text(updated, encoding="utf-8", newline="\n")
    return sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="판매 서버 OpenAPI 고정본 동기화")
    parser.add_argument("--ref", default=DEFAULT_REF, help="원본 레포 브랜치·커밋 (기본 dev)")
    parser.add_argument("--check", action="store_true", help="어긋났는지만 본다(다르면 1)")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(TARGETS, args.ref)
        for line in problems:
            print(f"어긋남: {line}")
        if problems:
            print("고치기: cd backend && python scripts/sync_shop_openapi.py")
            return 1
        print(f"판매 서버 OpenAPI 사본 {len(TARGETS)}개가 원본 {args.ref}와 같음")
        return 0
    sha = sync(TARGETS, args.ref)
    print(f"판매 서버 OpenAPI 사본 {len(TARGETS)}개를 {REPO}@{sha[:8]}로 맞춤")
    return 0


if __name__ == "__main__":
    sys.exit(main())
