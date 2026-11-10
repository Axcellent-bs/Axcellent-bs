#!/usr/bin/env bash
# 대사 예외 시연용 PG 정산 파일을 만들어 재무에 올린다 (운영자 명령). 설명은 pg_demo_file.py 맨 위.
#   bash backend/scripts/pg_demo_file.sh          # 만들고 올리기
#   bash backend/scripts/pg_demo_file.sh --dry    # 만들기만
#   bash backend/scripts/pg_demo_file.sh clear    # 올린 PG 자료 빼기 (시연 끝)
#   bash backend/scripts/pg_demo_file.sh status   # 올린 기록 보기
set -eu
cd "$(dirname "$0")/.."
exec uv run python scripts/pg_demo_file.py "$@"
