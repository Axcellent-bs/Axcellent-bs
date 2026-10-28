#!/usr/bin/env bash
# 실제 모드 백엔드(run_all.sh)에 붙는 AXcellent 화면(frontend, 3090)을 켠다.
# run_all.sh 가 켤 때마다 만든 사람 토큰·관리자 토큰을 backend/.data/web.env 에서 읽어 개발 서버에만 넘긴다.
#   bash backend/scripts/run_web.sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .data/web.env ]; then
  echo "backend/.data/web.env 가 없습니다. 먼저 bash scripts/run_all.sh 로 실제 모드 백엔드를 띄우세요." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
. .data/web.env
set +a
cd ../frontend
exec npm run dev
