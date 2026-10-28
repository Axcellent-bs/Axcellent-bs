#!/usr/bin/env bash
# 로컬에서 백엔드 전체(공통 4개 + 서비스 12개)를 목 모드로 띄운다. Docker 없이 개발 PC에서 화면 확인용.
#   bash backend/scripts/run_all.sh          # 띄우기 (로그: backend/.logs/<이름>.log)
#   bash backend/scripts/run_all.sh stop     # 이 스크립트가 띄운 것 끄기
# 먼저 backend/ 에서 `uv sync --all-packages` 한 번.
set -u
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY=".venv/bin/python"
LOGS=".logs"
mkdir -p "$LOGS"

# 이름 포트 모듈
APPS="
safety 8001 ax_safety.main:app
memory 8002 ax_memory.main:app
shop-api 8010 ax_shop_api.main:app
dashboard-api 8020 ax_dashboard_api.main:app
orchestrator 8000 ax_orchestrator.main:app
planning 8101 svc_planning.main:app
dev 8111 svc_dev.main:app
inquiry 8102 svc_inquiry.main:app
claims 8112 svc_claims.main:app
reviews 8122 svc_reviews.main:app
coupon 8103 svc_coupon.main:app
handover 8104 svc_handover.main:app
decisions 8114 svc_decisions.main:app
finance 8105 svc_finance.main:app
data 8106 svc_data.main:app
incident 8107 svc_incident.main:app
"

if [ "${1:-}" = "stop" ]; then
  for f in "$LOGS"/*.pid; do
    [ -f "$f" ] || continue
    pid=$(cat "$f")
    taskkill //PID "$pid" //F //T >/dev/null 2>&1 || kill "$pid" 2>/dev/null
    rm -f "$f"
  done
  echo "stopped"
  exit 0
fi

# 컨테이너가 아니라서 서비스 이름 대신 localhost 주소를 알려 준다 (AX_<이름>_URL, agent_url)
while read -r name port _; do
  [ -n "$name" ] || continue
  export "AX_$(echo "$name" | tr 'a-z-' 'A-Z_')_URL=http://127.0.0.1:$port"
done <<< "$APPS"
# 부서 이름으로 부르는 코드가 있어도 skill로 서비스를 고르지만(route_target), 고를 수 없는 경우를 위해 대표 서비스로 둔다
export AX_SUPPORT_URL=http://127.0.0.1:8102 AX_HR_URL=http://127.0.0.1:8104
export AX_MARKETING_URL=http://127.0.0.1:8103 AX_INFRA_URL=http://127.0.0.1:8107
# 이름이 다른 주소 변수 (shop-api의 총괄 중계, hr의 회사 기억)
export SHOP_ORCHESTRATOR_URL=http://127.0.0.1:8000 HR_MEMORY_URL=http://127.0.0.1:8002

export AGENT_TEMPLATE_MOCK=1
# 총괄은 자기 목 스위치가 따로 있다. 부서는 가짜 대신 위에서 띄운 서비스들을 실제로 부른다(카드는 서비스별로 모아 부서 단위로 합침)
export ORCHESTRATOR_MOCK=1 ORCHESTRATOR_REAL_DEPTS=infra,data,planning,marketing,support,finance,hr
# 화면(AXcellent 3090, 고객사 쇼핑몰 shop-web 3000)에서 직접 부르는 서버의 CORS
WEB="http://localhost:3090,http://127.0.0.1:3090,http://localhost:3000,http://127.0.0.1:3000,http://localhost:3010,http://127.0.0.1:3010,http://localhost:3001"
export DASHBOARD_CORS_ORIGINS="$WEB" FINANCE_CORS_ORIGINS="$WEB" SHOP_CORS_ORIGINS="$WEB"
export SAFETY_CORS_ORIGINS="$WEB" PLANNING_CORS_ORIGINS="$WEB"

while read -r name port module; do
  [ -n "$name" ] || continue
  "$PY" -m uvicorn "$module" --host 127.0.0.1 --port "$port" > "$LOGS/$name.log" 2>&1 &
  echo $! > "$LOGS/$name.pid"
done <<< "$APPS"

sleep 8
while read -r name port _; do
  [ -n "$name" ] || continue
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port/health" || true)
  printf "%-14s %s  %s\n" "$name" "$port" "$code"
done <<< "$APPS"
wait
