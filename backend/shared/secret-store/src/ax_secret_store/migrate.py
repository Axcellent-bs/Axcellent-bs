"""`.env` → DB 이전 도구. `python -m ax_secret_store <명령>` (값은 절대 출력하지 않는다).

명령
- `migrate [--dry-run] [--tenant T] [--names 패턴...] [--env-file 파일] [--overwrite]`
  가져올 이름은 `--names`(fnmatch 패턴) → `AX_SECRET_MIGRATE_NAMES`(쉼표) → 기본
  `MARKETING_*_API_KEY`, `AX_AGENT_TOKENS` 순서로 정한다. 커스텀 공급자 키는
  `--names MARKETING_CUSTOM_*`처럼 따로 적는다. `--env-file`이 없으면 이 프로세스의 환경변수를 본다.
- `verify [같은 인자]` 이전 전후 대조. 지문(HMAC)이 같은지만 `match`/`mismatch`로 보인다.
- `rewrap` 마스터 키를 바꾼 뒤 모든 값을 현재 키 버전으로 다시 암호화한다.
- `keygen` 새 마스터 키를 한 번 출력한다(`.env`에 넣고 따로 백업한다).
`.env` 값은 지우지 않는다. 스위치를 `env`로 되돌리면 롤백이다.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path

from ax_secret_store import crypto, errors
from ax_secret_store.errors import SecretStoreError
from ax_secret_store.resolver import default_tenant
from ax_secret_store.service import SecretService, check_name, check_value

NAMES_ENV = "AX_SECRET_MIGRATE_NAMES"
DEFAULT_PATTERNS = ["MARKETING_*_API_KEY", "AX_AGENT_TOKENS"]
NOT_SECRET = {"MARKETING_CUSTOM_VIDEO_ALLOWED_HOSTS"}  # 주소 목록이라 비밀값이 아니다
WHO = "migrate-cli"


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, raw = text.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = raw.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def patterns_from(args_names: list[str] | None) -> list[str]:
    if args_names:
        return args_names
    env = [p.strip() for p in os.getenv(NAMES_ENV, "").split(",") if p.strip()]
    return env or DEFAULT_PATTERNS


def select_names(source: dict[str, str], patterns: list[str]) -> list[str]:
    return sorted(
        k
        for k in source
        if k not in NOT_SECRET and any(fnmatch.fnmatchcase(k, p) for p in patterns)
    )


def _source(env_file: str | None) -> dict[str, str]:
    return parse_env_file(Path(env_file)) if env_file else dict(os.environ)


def run_migrate(
    svc: SecretService, source: dict[str, str], patterns: list[str], tenant: str,
    *, dry_run: bool, overwrite: bool,
) -> list[tuple[str, str]]:  # fmt: skip
    results: list[tuple[str, str]] = []
    for name in select_names(source, patterns):
        value = source[name].strip()
        if not value:
            results.append((name, "empty_in_env"))
            continue
        try:
            check_name(name)
            check_value(value)
        except SecretStoreError as error:
            results.append((name, error.code))
            continue
        if dry_run:
            exists = svc.repo.get(tenant, name) is not None
            skip = exists and not overwrite
            results.append((name, "would_skip_exists" if skip else "would_import"))
        else:
            results.append((name, svc.import_value(tenant, name, value, WHO, overwrite=overwrite)))
    return results


def run_verify(
    svc: SecretService, source: dict[str, str], patterns: list[str], tenant: str
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for name in select_names(source, patterns):
        env_value = source[name].strip()
        stored = svc.fingerprint_of_stored(tenant, name)
        if stored is None:
            results.append((name, "missing_in_db"))
        elif not env_value:
            results.append((name, "missing_in_env"))
        else:
            same = stored == svc.keyring.fingerprint(tenant, name, env_value)
            results.append((name, "match" if same else "mismatch"))
    return results


def _print(rows: list[tuple[str, str]]) -> None:
    for name, status in rows:
        print(f"{name}\t{status}")
    print(f"total\t{len(rows)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ax_secret_store")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("migrate", "verify"):
        p = sub.add_parser(cmd)
        p.add_argument("--tenant", default=None)
        p.add_argument("--names", nargs="+", default=None)
        p.add_argument("--env-file", default=None)
        if cmd == "migrate":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--overwrite", action="store_true")
    sub.add_parser("rewrap")
    sub.add_parser("keygen")
    args = parser.parse_args(argv)
    if args.cmd == "keygen":
        print(crypto.generate_master_key())
        return 0
    try:
        svc = SecretService.from_env()
        if args.cmd == "rewrap":
            print(f"rewrapped\t{svc.rewrap_all(WHO)}")
            return 0
        tenant = args.tenant or default_tenant()
        source = _source(args.env_file)
        patterns = patterns_from(args.names)
        if args.cmd == "migrate":
            rows = run_migrate(
                svc, source, patterns, tenant, dry_run=args.dry_run, overwrite=args.overwrite
            )
        else:
            rows = run_verify(svc, source, patterns, tenant)
    except SecretStoreError as error:
        print(f"error\t{error.code}", file=sys.stderr)
        return 2
    except OSError:
        print(f"error\t{errors.DB_UNAVAILABLE}", file=sys.stderr)
        return 2
    _print(rows)
    return 1 if any(s == "mismatch" for _, s in rows) else 0
