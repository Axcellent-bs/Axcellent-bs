#!/usr/bin/env bash
# 로컬에서 백엔드 전체(공통 4개 + 서비스 11개 + 총괄)를 띄운다. 기본은 실제 모드다.
#   bash backend/scripts/run_all.sh          # 실제 모드: 실제 LLM·실제 토큰·Redis·파일 DB, 캣북스 고객 주문 트래픽
#   bash backend/scripts/run_all.sh mock     # 목 모드(예전 방식): 시드 데이터·가짜 LLM, 네트워크 없이 화면 확인용
#   bash backend/scripts/run_all.sh stop     # 이 스크립트가 띄운 것 끄기
# 먼저 backend/ 에서 `uv sync --all-packages` 한 번. 로그는 backend/.logs/<이름>.log
#
# 실제 모드에 필요한 것
# - backend/.env 의 LLM_MODEL·LLM_BASE_URL·LLM_API_KEY (EMBED_MODEL은 회사 기억 검색용, .env.example 참고)
# - Docker (Redis 이벤트 버스를 컨테이너 ax-redis 로 띄운다. REDIS_URL을 주면 그걸 쓴다)
# 에이전트 토큰·사람 토큰은 켤 때마다 새로 만들어 프로세스에만 넘긴다(파일에 남기지 않는다).
# 화면에 넘길 값(사람 토큰 등)은 backend/.data/web.env 에 쓰고, scripts/run_web.sh 가 읽는다.
set -u
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY=".venv/bin/python"
LOGS=".logs"
DATA=".data"
mkdir -p "$LOGS" "$DATA"

# 이름 포트 모듈 (총괄은 부서 카드를 모으므로 맨 뒤에 띄운다)
APPS="
safety 8001 ax_safety.main:app
memory 8002 ax_memory.main:app
shop-api 8010 ax_shop_api.main:app
dashboard-api 8020 ax_dashboard_api.main:app
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
orchestrator 8000 ax_orchestrator.main:app
"
PORTS=$(echo "$APPS" | awk 'NF {print $2}' | tr '\n' ',' | sed 's/,$//')

stop_all() {
  # bash의 $!는 Windows 프로세스 번호가 아니라서, 포트를 쥔 프로세스를 찾아 끈다
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "foreach(\$p in @($PORTS)){Get-NetTCPConnection -LocalPort \$p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue }}" >/dev/null 2>&1
  fi
  for f in "$LOGS"/*.pid; do
    [ -f "$f" ] || continue
    kill "$(cat "$f")" 2>/dev/null
    rm -f "$f"
  done
}

if [ "${1:-}" = "stop" ]; then
  stop_all
  echo "stopped"
  exit 0
fi

MODE=real
[ "${1:-}" = "mock" ] && MODE=mock

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
# 화면(AXcellent 프론트 3090)에서 직접 부르는 서버의 CORS
WEB="http://localhost:3090,http://127.0.0.1:3090,http://localhost:3001"
export DASHBOARD_CORS_ORIGINS="$WEB" FINANCE_CORS_ORIGINS="$WEB" SHOP_CORS_ORIGINS="$WEB"
export SAFETY_CORS_ORIGINS="$WEB" PLANNING_CORS_ORIGINS="$WEB"
# A2A 주고받은 기록(AI 대화방이 읽는다)
export AX_COMMON_LOG_DB="$PWD/$DATA/common_log.db"

if [ "$MODE" = "mock" ]; then
  export AGENT_TEMPLATE_MOCK=1
  # 총괄은 자기 목 스위치가 따로 있다. 부서는 가짜 대신 위에서 띄운 서비스들을 실제로 부른다
  export ORCHESTRATOR_MOCK=1 ORCHESTRATOR_REAL_DEPTS=infra,data,planning,marketing,support,finance,hr
else
  # .env (LLM 설정 등). 값은 출력하지 않는다
  if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1090
    . <(tr -d '\r' < .env)
    set +a
  fi
  # backend/.env 에 없으면 레포 맨 위 .env 에서 LLM 설정 4개만 읽는다 (DejaFix 형식 .env 를 같이 쓰는 경우).
  # 다른 값(메일·Zabbix 등)은 서버에 넘기지 않고, 줄 끝 주석(공백 뒤 #)은 뗀다
  if [ -f ../.env ]; then
    for v in LLM_MODEL LLM_BASE_URL LLM_API_KEY EMBED_MODEL; do
      [ -n "${!v:-}" ] && continue
      val=$(tr -d '\r' < ../.env | sed -n "s/^$v=//p" | tail -n 1 | sed -E 's/^\xEF\xBB\xBF//; s/[[:space:]]+#.*$//; s/[[:space:]]+$//; s/^"(.*)"$/\1/')
      [ -n "$val" ] && export "$v=$val"
    done
  fi
  # 실제 모드에서는 어떤 목 스위치도 남기지 않는다 (.env에 남아 있어도 끈다)
  for v in $(env | sed -n 's/^\([A-Z_]*_MOCK\)=.*/\1/p'); do unset "$v"; done
  unset ORCHESTRATOR_REAL_DEPTS
  for v in LLM_MODEL LLM_BASE_URL LLM_API_KEY; do
    [ -n "${!v:-}" ] || echo "경고: backend/.env 와 레포 맨 위 .env 에 $v 가 없습니다. LLM을 쓰는 skill은 규칙 기반 대체 답을 냅니다." >&2
  done
  [ -n "${EMBED_MODEL:-}" ] || echo "경고: EMBED_MODEL 이 없습니다. 회사 기억(memory) 검색이 실패합니다 (예: gemini-embedding-001)." >&2

  # 이벤트 버스 (Redis Streams)
  if [ -z "${REDIS_URL:-}" ]; then
    if docker inspect ax-redis >/dev/null 2>&1; then
      docker start ax-redis >/dev/null
    else
      docker run -d --name ax-redis -p 6379:6379 redis:7-alpine >/dev/null
    fi
    export REDIS_URL=redis://127.0.0.1:6379/0
  fi

  # 고객사 데이터는 파일에 둔다 (다시 켜도 남는다). 쇼핑몰은 비어 있을 때만 시드를 넣는다
  export SHOP_DB_PATH="$PWD/$DATA/shop.db" HR_DB_PATH="$PWD/$DATA/hr.db"
  export AX_LLM_USAGE_DB="$PWD/$DATA/llm_usage.db"
  # 결제 장애 감지를 시연 시간에 맞춘다 (기본 1분 지속 → 20초, 5초마다 판정). .env 값이 있으면 그걸 쓴다
  export SHOP_DEGRADED_SUSTAIN_S="${SHOP_DEGRADED_SUSTAIN_S:-20}" SHOP_MONITOR_INTERVAL_S="${SHOP_MONITOR_INTERVAL_S:-5}"

  # 에이전트 토큰 명부: 이름마다 새 토큰. 서비스 이름과 원래 부서 이름, 사람 관리자(admin) 모두 넣는다
  NAMES="$(echo "$APPS" | awk 'NF {print $1}' | tr '\n' ' ') support marketing hr infra admin"
  read -r AX_AGENT_TOKENS HUMAN_TOKEN < <("$PY" -c "
import secrets, sys
names = sys.argv[1].split()
print(','.join(f'{n}={secrets.token_urlsafe(24)}' for n in names), secrets.token_urlsafe(24))
" "$NAMES")
  export AX_AGENT_TOKENS
  # 사람(운영자) 토큰: 안전 관문 보류 결정·재무·dashboard-api 쓰기
  export SAFETY_HUMAN_TOKENS="운영자:$HUMAN_TOKEN" FINANCE_HUMAN_TOKENS="운영자:$HUMAN_TOKEN"
  token_of() { echo "$AX_AGENT_TOKENS" | tr ',' '\n' | sed -n "s/^$1=//p"; }
  # 화면(개발 서버)에 넘길 값. 이 파일은 커밋하지 않는다(.data/)
  {
    echo "VITE_MOCK=off"
    echo "VITE_SAFETY_HUMAN_TOKEN=$HUMAN_TOKEN"
    echo "VITE_SHOP_ADMIN_TOKEN=$(token_of admin)"
    echo "VITE_DATA_A2A_TOKEN=$(token_of dashboard-api)"
  } > "$DATA/web.env"
fi

stop_all

started=0
while read -r name port module; do
  [ -n "$name" ] || continue
  if [ "$name" = "orchestrator" ]; then
    # 총괄은 시작할 때 부서 카드를 모은다. 다른 서버가 다 뜬 뒤에 띄운다
    for _ in $(seq 1 30); do
      up=0
      while read -r n p _; do
        [ -n "$n" ] && [ "$n" != "orchestrator" ] || continue
        [ "$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$p/health" || true)" = "200" ] && up=$((up + 1))
      done <<< "$APPS"
      [ "$up" -ge "$started" ] && break
      sleep 1
    done
  fi
  "$PY" -m uvicorn "$module" --host 127.0.0.1 --port "$port" > "$LOGS/$name.log" 2>&1 &
  echo $! > "$LOGS/$name.pid"
  started=$((started + 1))
done <<< "$APPS"

if [ "$MODE" = "real" ]; then
  # 캣북스 고객 주문 트래픽
  SHOP_API_URL=http://127.0.0.1:8010 "$PY" scripts/shop_traffic.py > "$LOGS/shop-traffic.log" 2>&1 &
  echo $! > "$LOGS/shop-traffic.pid"
fi

sleep 8
echo "모드: $MODE"
while read -r name port _; do
  [ -n "$name" ] || continue
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port/health" || true)
  printf "%-14s %s  %s\n" "$name" "$port" "$code"
done <<< "$APPS"
[ "$MODE" = "real" ] && echo "화면: bash scripts/run_web.sh (backend/.data/web.env 값으로 프론트 개발 서버를 켠다)"
wait
