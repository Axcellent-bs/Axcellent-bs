#!/usr/bin/env bash
# 실제 모드 백엔드(run_all.sh)에 붙는 AXcellent 화면(frontend, 3090)을 켠다.
#   bash backend/scripts/run_web.sh
# 토큰: run_all.sh 가 켤 때마다 만든 사람·관리자·데이터 토큰(backend/.data/web.env)을 VITE_ 접두사가 없는
# AX_WEB_*_TOKEN 으로만 개발 서버(node)에 넘긴다. 화면 번들(VITE_*_TOKEN)에는 자리표시자만 들어가고,
# 개발 서버 프록시(frontend/token-proxy.ts)가 자리표시자를 진짜 토큰으로 바꿔 붙인다(서버 nginx와 같은 구조).
# 화면이 로그인 세션 토큰을 보내면 프록시는 그대로 넘긴다. 토큰을 붙이는 주소는 모두 개발 서버 프록시를 탄다.
set -eu
cd "$(dirname "$0")/.."
WEB_ENV=.data/web.env
if [ ! -f "$WEB_ENV" ]; then
  echo "backend/.data/web.env 가 없습니다. 먼저 bash scripts/run_all.sh 로 실제 모드 백엔드를 띄우세요." >&2
  exit 1
fi
# web.env 는 셸로 읽어 들이지 않는다(VITE_ 이름이 그대로 환경에 실리지 않게). 이름 하나씩 값만 꺼낸다.
# 예전 run_all.sh 가 만든 파일(VITE_* 이름)도 받는다
val() { sed -n "s/^$1=//p" "$WEB_ENV" | head -n 1; }
pick() { v=$(val "$1"); [ -n "$v" ] || v=$(val "$2"); printf '%s' "$v"; }
AX_WEB_HUMAN_TOKEN=$(pick AX_WEB_HUMAN_TOKEN VITE_SAFETY_HUMAN_TOKEN)
AX_WEB_ADMIN_TOKEN=$(pick AX_WEB_ADMIN_TOKEN VITE_SHOP_ADMIN_TOKEN)
AX_WEB_DATA_A2A_TOKEN=$(pick AX_WEB_DATA_A2A_TOKEN VITE_DATA_A2A_TOKEN)
export AX_WEB_HUMAN_TOKEN AX_WEB_ADMIN_TOKEN AX_WEB_DATA_A2A_TOKEN
# 화면에는 자리표시자만 (frontend/token-proxy.ts 의 *_PLACEHOLDER 와 같은 값). 진짜 토큰이 없으면 비워서 버튼을 끈다
placeholder() { [ -n "$1" ] && printf '%s' "$2" || true; }
VITE_SAFETY_HUMAN_TOKEN=$(placeholder "$AX_WEB_HUMAN_TOKEN" human-via-nginx)
VITE_SHOP_ADMIN_TOKEN=$(placeholder "$AX_WEB_ADMIN_TOKEN" admin-via-nginx)
VITE_DATA_A2A_TOKEN=$(placeholder "$AX_WEB_DATA_A2A_TOKEN" data-via-nginx)
export VITE_SAFETY_HUMAN_TOKEN VITE_SHOP_ADMIN_TOKEN VITE_DATA_A2A_TOKEN
# 토큰을 붙여 부르는 서비스는 모두 개발 서버 프록시 주소로 (frontend/vite.config.ts)
export VITE_DASHBOARD_API_URL=/dashboard-api VITE_SAFETY_API_URL=/safety-api VITE_FINANCE_AGENT_URL=/finance-api
export VITE_PLANNING_API_URL=/planning-api VITE_DEV_API_URL=/dev-api VITE_ORCHESTRATOR_URL=/orch
export VITE_DATA_A2A_URL=/data-a2a VITE_HR_API_URL=/hr-api VITE_ARCHIVE_API_URL=/archive-api
cd ../frontend
exec npm run dev
