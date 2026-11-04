#!/usr/bin/env bash
# 로컬에서 백엔드 전체(공통 4개 + 서비스 11개 + 총괄)를 실제 모드로 띄운다. 목 모드는 없다(*_MOCK은 테스트 전용).
#   bash backend/scripts/run_all.sh          # 실제 LLM·실제 토큰·Redis·파일 DB, 캣북스 고객 주문 트래픽
#   bash backend/scripts/run_all.sh stop     # 이 스크립트가 띄운 것 끄기
#   bash backend/scripts/run_all.sh reset    # 끄고 테스트로 쌓인 기록(.data·이벤트 버스)을 지운다
#   bash backend/scripts/run_all.sh --only data,finance,shop-api   # 고른 서버만 (이름은 아래 APPS)
#   bash backend/scripts/run_all.sh --only data-demo              # 묶음: core, data-demo (아래 GROUP_*)
#     안 띄운 서비스는 AX_ENABLED_SERVICES에서도 빠진다(이미 줬으면 교집합). stop은 늘 전체 포트를 끈다
# 먼저 backend/ 에서 `uv sync --all-packages` 한 번. 로그는 backend/.logs/<이름>.log
#
# 실제 모드에 필요한 것
# - backend/.env 의 LLM_MODEL·LLM_BASE_URL·LLM_API_KEY (EMBED_MODEL은 회사 기억 검색·상품 연결용.
#   EMBED_BASE_URL·EMBED_API_KEY는 임베딩만 다른 주소로 보낼 때, 없으면 LLM 주소·키. .env.example 참고)
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
# 인수인계(handover, 8104)는 화면에서 접어 띄우지 않는다. 코드·계약·서비스 정의는 그대로 있고, 켠 서비스에서도 뺀다(FOLDED)
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
decisions 8114 svc_decisions.main:app
org 8124 svc_org.main:app
finance 8105 svc_finance.main:app
data 8106 svc_data.main:app
incident 8107 svc_incident.main:app
orchestrator 8000 ax_orchestrator.main:app
"
# 접어 둔 서비스(띄우지 않고 켠 서비스에서도 뺀다). 되살리려면 위 APPS에 줄을 되돌리고 여기서 뺀다
FOLDED="handover"
PORTS=$(echo "$APPS" | awk 'NF {print $2}' | tr '\n' ',' | sed 's/,$//')

# --only 묶음 이름 (data-demo = core + data 업무 리포트가 부르는 서비스)
GROUP_core="safety,memory,shop-api,dashboard-api,orchestrator"
GROUP_data_demo="$GROUP_core,data,finance,inquiry,coupon,dev"

# 인자: stop | reset | mock 과 --only <목록> (--only=<목록>도 된다)
CMD=""
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only)
      ONLY="${2:-}"
      [ -n "$ONLY" ] || { echo "--only 뒤에 서버 이름(쉼표 목록)이나 묶음 이름이 필요합니다" >&2; exit 2; }
      shift 2 ;;
    --only=*) ONLY="${1#--only=}"; shift ;;
    stop|reset|mock) CMD="$1"; shift ;;
    *) echo "모르는 인자: $1 (쓸 수 있는 것: stop, reset, --only <목록>)" >&2; exit 2 ;;
  esac
done

# 띄울 서버 목록 (APPS 순서 유지). --only가 없으면 전부
RUN_APPS="$APPS"
SKIPPED=""
if [ -n "$ONLY" ]; then
  wanted=","
  for item in $(echo "$ONLY" | tr ',' ' '); do
    var="GROUP_$(echo "$item" | tr '-' '_')"
    if [ -n "${!var:-}" ]; then
      wanted="$wanted${!var},"
    elif echo "$APPS" | awk -v n="$item" '$1 == n {found=1} END {exit !found}'; then
      wanted="$wanted$item,"
    else
      echo "모르는 서버·묶음 이름: $item" >&2
      echo "  서버: $(echo "$APPS" | awk 'NF {print $1}' | tr '\n' ' ')" >&2
      echo "  묶음: core data-demo" >&2
      exit 2
    fi
  done
  RUN_APPS=$(echo "$APPS" | awk -v w="$wanted" 'NF && index(w, "," $1 ",")')
  SKIPPED=$(echo "$APPS" | awk -v w="$wanted" 'NF && !index(w, "," $1 ",") {print $1}' | tr '\n' ' ' | sed 's/ $//')
fi

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

if [ "$CMD" = "stop" ]; then
  stop_all
  echo "stopped"
  exit 0
fi

# 테스트로 쌓인 기록을 지우고 처음부터: 캣북스 주문·AI 대화 기록·인사 기록·LLM 사용량·이벤트 버스
# (캣북스 쇼핑몰은 비어 있으면 시드를 다시 넣는다)
if [ "$CMD" = "reset" ]; then
  stop_all
  rm -f "$DATA"/*.db "$DATA"/*.db-wal "$DATA"/*.db-shm "$DATA"/web.env "$DATA"/tokens.env
  docker exec ax-redis redis-cli FLUSHALL >/dev/null 2>&1 || true
  echo "reset: backend/.data 와 이벤트 버스를 비웠습니다. 다시 켜려면 bash scripts/run_all.sh"
  exit 0
fi

if [ "$CMD" = "mock" ]; then
  echo "목 모드는 지웠습니다. 로컬도 실제 모드 하나로 돕니다 (*_MOCK 스위치는 CI 단위 테스트 전용)." >&2
  exit 2
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
# 화면(AXcellent 3090, 캣북스 쇼핑몰 3000)에서 직접 부르는 서버의 CORS
WEB="http://localhost:3090,http://127.0.0.1:3090,http://localhost:3001,http://localhost:3000,http://localhost:3010,http://127.0.0.1:3010"
export DASHBOARD_CORS_ORIGINS="$WEB" FINANCE_CORS_ORIGINS="$WEB" SHOP_CORS_ORIGINS="$WEB"
export SAFETY_CORS_ORIGINS="$WEB" PLANNING_CORS_ORIGINS="$WEB"
# 화면에서 넣은 형상관리 연결(토큰 포함)을 다시 켜도 남게 저장한다. .data/는 git에 안 올라간다
export PLANNING_VCS_STORE="$PWD/$DATA/vcs_connections.json"
# 화면에서 넣은 고객사 API 키(BYOK)도 같은 곳에 저장한다 (커밋하지 않는다)
export DEV_LLM_KEY_STORE="$PWD/$DATA/llm_key.json"
# 기획·개발 상태를 서비스 역할마다 파일에 이어 쓴다 (다시 켜도 요청·작업·PR·대화가 남음)
export PLANNING_STATE_DIR="$PWD/$DATA"
# data: 오늘 네이버 판정("지금 급등 중" 배지)을 파일에 남겨 다시 켜도 그날 배지가 유지되게
export DATA_TODAY_BOARD_DB="$PWD/$DATA/today_board.db"
# marketing: 기획에서 받은 기획안을 파일에 남겨 다시 켜도 목록이 유지되게
export MARKETING_BRIEFS_DB="$PWD/$DATA/marketing_briefs.db"
# 기획·개발은 실제 시각으로 (기본은 10-22에서 1분씩 가는 시연 시계라 총괄이 사건 구간으로 묻는
# 관련 PR 찾기·교차검증 최근 7일 장애가 실제 머지를 못 잡는다)
export PLANNING_CLOCK=real
# A2A 주고받은 기록(AI 대화방이 읽는다)
export AX_COMMON_LOG_DB="$PWD/$DATA/common_log.db"
# 데이터분석 업무 리포트 일간 저장 (data 자체 파일. 하루 한 번 미리 만들어 화면이 바로 읽는다)
export DATA_REPORT_DB="$PWD/$DATA/data_reports.db"

# 업무 요청 라우터 (README "업무 요청 라우터"): 켠 서비스면 에이전트로, 안 켠 서비스면 사람 할 일함으로
# 켠 서비스는 backend/.env 의 AX_ENABLED_SERVICES (쉼표 목록). 없거나 비어 있으면 전부 켠다(예전 동작)
if [ -z "${AX_ENABLED_SERVICES:-}" ] && [ -f .env ]; then
  AX_ENABLED_SERVICES=$(tr -d '\r' < .env | sed -n 's/^AX_ENABLED_SERVICES=//p' | tail -n 1 | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//; s/^"(.*)"$/\1/')
fi
export AX_ENABLED_SERVICES="${AX_ENABLED_SERVICES:-}"
# 켠 서비스 파일 ({"enabled": [...], "updated_at", "updated_by"}). 있으면 위 환경변수보다 먼저 쓰고,
# 바꾸면 재시작 없이 반영된다. 없거나 깨졌으면 환경변수를 쓴다 (ax_agent_template.routing)
export AX_ENABLED_SERVICES_FILE="${AX_ENABLED_SERVICES_FILE:-$PWD/$DATA/enabled_services.json}"
# 사람 할 일함 저장 파일 (dashboard-api)
export AX_HANDOFF_DB="$PWD/$DATA/handoffs.db"
# 총괄 이벤트 저장소 (다시 켜도 이벤트 기록과 처리한 event_id가 남는다)
export ORCHESTRATOR_DB="$PWD/$DATA/orchestrator.db"
# 재무 기록(판매자 심사·정산 보류·장부·사람 결정). 다시 켜도 이어진다
export FINANCE_STATE_FILE="$PWD/$DATA/finance_state.json"
export FINANCE_EXPORT_DIR="${FINANCE_EXPORT_DIR:-$PWD/$DATA/finance_exports}"
export FINANCE_BANK_INBOX="${FINANCE_BANK_INBOX:-$PWD/$DATA/finance_bank_inbox}"
mkdir -p "$FINANCE_EXPORT_DIR" "$FINANCE_BANK_INBOX"
# 외부 연결 쓰기 보류(긴급 취약). 다시 켜도 잊지 않게 파일에 둔다
export SAFETY_CONNECTION_HOLDS_FILE="$PWD/$DATA/connection_holds.json"

# .env (LLM 설정 등). 값은 출력하지 않는다
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1090
  . <(tr -d '\r' < .env)
  set +a
fi
# backend/.env 에 없으면 레포 맨 위 .env 에서 LLM·임베딩 설정 6개만 읽는다 (DejaFix 형식 .env 를 같이 쓰는 경우).
# 다른 값(메일·Zabbix 등)은 서버에 넘기지 않고, 줄 끝 주석(공백 뒤 #)은 뗀다
if [ -f ../.env ]; then
  for v in LLM_MODEL LLM_BASE_URL LLM_API_KEY EMBED_MODEL EMBED_BASE_URL EMBED_API_KEY; do
    [ -n "${!v:-}" ] && continue
    val=$(tr -d '\r' < ../.env | sed -n "s/^$v=//p" | tail -n 1 | sed -E 's/^\xEF\xBB\xBF//; s/[[:space:]]+#.*$//; s/[[:space:]]+$//; s/^"(.*)"$/\1/')
    [ -n "$val" ] && export "$v=$val"
  done
fi
# 회사 설정 (config/tenants/<AX_TENANT>/tenant.toml, 기본 catbooks): 조직도 AX_ORG_CONFIG·data 카탈로그
# DATA_TENANT_CATALOG를 정한다. 이미 준 환경변수(.env 포함)와 config/org.yaml(각자 덮어쓰기)이 먼저다
if ! tenant_exports=$("$PY" scripts/tenant_env.py); then
  echo "회사 설정을 읽지 못해 켜지 않습니다 (AX_TENANT=${AX_TENANT:-catbooks}, backend/README.md \"새 회사를 붙일 때\")" >&2
  exit 2
fi
eval "$tenant_exports"
if [ -z "${AX_ORG_CONFIG:-}" ] || [ ! -f "$AX_ORG_CONFIG" ]; then
  echo "==================================================================" >&2
  echo " 조직도 없음: 꺼진 서비스로 가는 일은 no_assignee로 실패합니다" >&2
  echo " config/tenants/${AX_TENANT:-catbooks}/org.yaml 을 두거나 AX_ORG_CONFIG를 주세요" >&2
  echo "==================================================================" >&2
fi
# 목 스위치(*_MOCK, 테스트 전용)는 남기지 않는다 (.env에 남아 있어도 끈다)
for v in $(env | sed -n 's/^\([A-Z_]*_MOCK\)=.*/\1/p'); do unset "$v"; done
unset ORCHESTRATOR_REAL_DEPTS
for v in LLM_MODEL LLM_BASE_URL LLM_API_KEY; do
  [ -n "${!v:-}" ] || echo "경고: backend/.env 와 레포 맨 위 .env 에 $v 가 없습니다. LLM을 쓰는 skill은 규칙 기반 대체 답을 냅니다." >&2
done
[ -n "${EMBED_MODEL:-}" ] || echo "경고: EMBED_MODEL 이 없습니다. 회사 기억(memory) 검색이 실패합니다 (예: gemini-embedding-001)." >&2

# 안 띄운 서비스는 켠 서비스에서도 뺀다 (라우터가 그쪽으로 A2A를 보내지 않고 사람 할 일함으로 넘긴다).
# --only로 뺀 서비스와 접어 둔 서비스(FOLDED)가 대상이다. 이미 AX_ENABLED_SERVICES를 줬으면 둘의 교집합이다
svc_ids=" $("$PY" -c "from ax_agent_template.services import SERVICES; print(' '.join(s.id for s in SERVICES))") "
if [ -n "$ONLY" ] || [ -n "$FOLDED" ]; then
  given=",$(echo "${AX_ENABLED_SERVICES:-}" | tr -d ' '),"
  enabled=""
  if [ -n "$ONLY" ]; then base_names=$(echo "$RUN_APPS" | awk 'NF {print $1}'); else base_names=$svc_ids; fi
  for n in $base_names; do
    case "$svc_ids" in *" $n "*) ;; *) continue ;; esac
    case " $FOLDED " in *" $n "*) continue ;; esac
    case "$given" in ",,"|*",$n,"*) enabled="$enabled${enabled:+,}$n" ;; esac
  done
  # 비어 있으면 "전부 켬"이 되므로, 켤 서비스가 하나도 없으면 어느 서비스와도 맞지 않는 값을 둔다
  export AX_ENABLED_SERVICES="${enabled:-none}"
  # 켠 서비스 파일이 있으면 라우터는 파일을 먼저 본다. 안 띄운 서비스가 파일에서 켜져 있으면 A2A가 실패한다
  if [ -f "$AX_ENABLED_SERVICES_FILE" ]; then
    echo "경고: 켠 서비스 파일($AX_ENABLED_SERVICES_FILE)이 있어 run_all.sh가 정한 켠 서비스($AX_ENABLED_SERVICES)보다 먼저 쓰입니다. 안 띄운 서비스가 파일에 있으면 그 파일을 고치거나 지우세요." >&2
  fi
fi

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

# 에이전트 토큰 명부: 서비스 이름과 원래 부서 이름, 사람 관리자(admin) 모두 넣는다.
# 토큰은 $DATA/tokens.env 에 두고 다시 켤 때 그대로 쓴다. 켤 때마다 새로 만들면 이미 떠 있는
# 화면(run_web.sh)이 예전 사람 토큰을 들고 있어 "사람 토큰을 검증할 수 없다"로 막힌다.
# 없는 이름만 새로 만든다. 새 토큰이 필요하면 bash scripts/run_all.sh reset
# 사람별 토큰: AX_PEOPLE="예진,준하"처럼 이름을 주면 사람마다 사람 토큰(승인·머지·재무 결정)과
# 작업 대기열 토큰(내 Claude MCP)을 따로 만든다. 누가 결정했는지 관문·화면 기록에 이름이 남는다.
# 내 작업 토큰 보기: sed -n 's/^WORK_TOKEN\.예진=//p' backend/.data/tokens.env (내 Claude 연결에 넣는다)
NAMES="$(echo "$APPS" | awk 'NF {print $1}' | tr '\n' ' ') support marketing hr infra admin"
read -r AX_AGENT_TOKENS HUMAN_TOKEN WORK_TOKEN PEOPLE_HUMAN PEOPLE_WORK < <("$PY" -c "
import os, secrets, sys
names, path = sys.argv[1].split(), sys.argv[2]
people = [p.strip() for p in sys.argv[3].split(',') if p.strip() and ':' not in p and '=' not in p and ' ' not in p]
saved = {}
if os.path.exists(path):
    for line in open(path, encoding='utf-8'):
        key, _, value = line.strip().partition('=')
        saved[key] = value
agents = dict(p.split('=', 1) for p in saved.get('AX_AGENT_TOKENS', '').split(',') if '=' in p)
agents = {n: agents.get(n) or secrets.token_urlsafe(24) for n in names}
human = saved.get('HUMAN_TOKEN') or secrets.token_urlsafe(24)
work = saved.get('WORK_TOKEN') or secrets.token_urlsafe(24)
ph = {p: saved.get(f'HUMAN_TOKEN.{p}') or secrets.token_urlsafe(24) for p in people}
pw = {p: saved.get(f'WORK_TOKEN.{p}') or secrets.token_urlsafe(24) for p in people}
line = ','.join(f'{n}={t}' for n, t in agents.items())
with open(path, 'w', encoding='utf-8') as f:
    f.write(f'AX_AGENT_TOKENS={line}\nHUMAN_TOKEN={human}\nWORK_TOKEN={work}\n')
    for p in people:
        f.write(f'HUMAN_TOKEN.{p}={ph[p]}\nWORK_TOKEN.{p}={pw[p]}\n')
os.chmod(path, 0o600)
# 빈 목록도 자리를 지키게 '-'를 넣는다 (read가 칸을 당기지 않게)
print(line, human, work, ','.join(f'{p}:{t}' for p, t in ph.items()) or '-',
      ','.join(f'{p}:{t}' for p, t in pw.items()) or '-')
" "$NAMES" "$DATA/tokens.env" "${AX_PEOPLE:-}")
PEOPLE_WORK="$(printf '%s' "$PEOPLE_WORK" | tr -d '\015')"  # Windows 파이썬이 줄 끝에 붙이는 CR을 지운다
[ "$PEOPLE_HUMAN" = "-" ] && PEOPLE_HUMAN=""
[ "$PEOPLE_WORK" = "-" ] && PEOPLE_WORK=""
export AX_AGENT_TOKENS
# 사람 토큰: 안전 관문 보류 결정·재무·dashboard-api 쓰기. 이름 "공용"은 모두가 같이 쓰는 토큰이다
# (화면 데모 로그인의 "운영자" 역할과는 다른 것. 사람마다 나누려면 AX_PEOPLE)
# 사람별 토큰(AX_PEOPLE)이 있으면 같이 넣는다. 공용 토큰은 로컬 화면 빌드(web.env)가 쓴다
HUMANS="공용:$HUMAN_TOKEN${PEOPLE_HUMAN:+,$PEOPLE_HUMAN}"
export SAFETY_HUMAN_TOKENS="$HUMANS" FINANCE_HUMAN_TOKENS="$HUMANS"
# 작업 대기열(MCP) 전용 토큰: 개발자 PC의 Claude Code 설정에 남는 값이라 사람 토큰과 나눈다.
# 승인·머지 권한이 없다 (dev_agent/work_auth.py). 이름이 같아 화면의 "내 Claude"와 이어진다
export DEV_WORK_TOKENS="${DEV_WORK_TOKENS:-공용:$WORK_TOKEN${PEOPLE_WORK:+,$PEOPLE_WORK}}"
# MCP·작업 대기열은 작업 토큰만 받는다 (사람 토큰이 개발자 PC 설정에 남지 않게). 예전 연결은
# 화면 "내 Claude 연결" 명령으로 다시 붙인다. 한동안 예전처럼 쓰려면 DEV_WORK_ALLOW_HUMAN=1
export DEV_WORK_ALLOW_HUMAN="${DEV_WORK_ALLOW_HUMAN:-0}"
token_of() { echo "$AX_AGENT_TOKENS" | tr ',' '\n' | sed -n "s/^$1=//p"; }
# 화면(개발 서버)에 넘길 값. 이 파일은 커밋하지 않는다(.data/)
{
  echo "VITE_SAFETY_HUMAN_TOKEN=$HUMAN_TOKEN"
  echo "VITE_SHOP_ADMIN_TOKEN=$(token_of admin)"
  echo "VITE_DATA_A2A_TOKEN=$(token_of dashboard-api)"
  # 읽기 전용 열람 토큰(.env의 SHOP_VIEWER_TOKEN)이 있으면 화면에도 넘긴다. 쓰기는 못 하는 값이다
  [ -z "${SHOP_VIEWER_TOKEN:-}" ] || echo "VITE_SHOP_VIEWER_TOKEN=$SHOP_VIEWER_TOKEN"
} > "$DATA/web.env"

stop_all

RUN_COUNT=$(echo "$RUN_APPS" | awk 'NF' | wc -l | tr -d ' ')
echo "띄운 서버 ${RUN_COUNT}개: $(echo "$RUN_APPS" | awk 'NF {print $1}' | tr '\n' ' ' | sed 's/ $//'), 안 띄움: ${SKIPPED:-없음}"

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
      done <<< "$RUN_APPS"
      [ "$up" -ge "$started" ] && break
      sleep 1
    done
  fi
  "$PY" -m uvicorn "$module" --host 127.0.0.1 --port "$port" > "$LOGS/$name.log" 2>&1 &
  echo $! > "$LOGS/$name.pid"
  started=$((started + 1))
done <<< "$RUN_APPS"

# 캣북스 고객 주문 트래픽 (shop-api를 띄웠을 때만)
if echo "$RUN_APPS" | awk '$1 == "shop-api" {found=1} END {exit !found}'; then
  # 재고가 0이 되면 주문이 조용히 멈춰 장애가 안 잡히는 것처럼 보인다. 자동 보충을 기본으로 켠다
  # (끄려면 SHOP_TRAFFIC_REFILL=0). 보충에는 위에서 만든 admin 토큰을 쓴다
  SHOP_API_URL=http://127.0.0.1:8010 SHOP_TRAFFIC_REFILL="${SHOP_TRAFFIC_REFILL:-1}" \
    "$PY" scripts/shop_traffic.py > "$LOGS/shop-traffic.log" 2>&1 &
  echo $! > "$LOGS/shop-traffic.pid"
fi

# data: Google 인기 검색어 스냅샷을 1시간마다 모은다 (data를 띄웠을 때만, 끄기는 stop이 .pid로 같이 끈다)
if echo "$RUN_APPS" | awk '$1 == "data" {found=1} END {exit !found}'; then
  "$PY" -m data_agent.trending_collect --every 3600 > "$LOGS/trending-collect.log" 2>&1 &
  echo $! > "$LOGS/trending-collect.pid"
fi

sleep 8
echo "모드: 실제"
if [ -f "$AX_ENABLED_SERVICES_FILE" ]; then
  echo "켠 서비스: 파일 $AX_ENABLED_SERVICES_FILE (바꾸면 재시작 없이 반영)  조직도: ${AX_ORG_CONFIG:-없음 (꺼진 서비스로 가는 일은 no_assignee로 실패)}"
else
  echo "켠 서비스: ${AX_ENABLED_SERVICES:-전부}  조직도: ${AX_ORG_CONFIG:-없음 (꺼진 서비스로 가는 일은 no_assignee로 실패)}"
fi
while read -r name port _; do
  [ -n "$name" ] || continue
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port/health" || true)
  printf "%-14s %s  %s\n" "$name" "$port" "$code"
done <<< "$RUN_APPS"
[ -z "$SKIPPED" ] || echo "안 띄움: $SKIPPED"
echo "화면: bash scripts/run_web.sh (backend/.data/web.env 값으로 프론트 개발 서버를 켠다)"
wait
