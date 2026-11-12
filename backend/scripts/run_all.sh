#!/usr/bin/env bash
# 로컬에서 백엔드 전체(공통 5개 + 서비스 11개 + 총괄)를 실제 모드로 띄운다. 목 모드는 없다(*_MOCK은 테스트 전용).
#   bash backend/scripts/run_all.sh          # 실제 LLM·실제 토큰·Redis·파일 DB (캣북스 가상 주문은 SHOP_TRAFFIC=on일 때만, 배송 표시는 늘)
#                                            # 이미 떠 있으면 켜지 않는다 ("이미 실행 중 (pid …), 먼저 stop")
#   bash backend/scripts/run_all.sh stop     # 이 스크립트가 띄운 것 끄기 (자식까지, 끝에 "남은 프로세스 0개" 확인)
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
dejafix-proxy 8030 ax_dejafix_proxy.main:app
planning 8101 svc_planning.main:app
dev 8111 svc_dev.main:app
inquiry 8102 svc_inquiry.main:app
claims 8112 svc_claims.main:app
settlement 8126 svc_settlement.main:app
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
# 이 스크립트가 띄우는 프로세스를 명령줄로 알아보는 표식 (scripts/procs.py: 이미 떠 있는지·끈 뒤 남았는지)
MARKERS=()
while read -r _ _ module; do
  [ -n "${module:-}" ] && MARKERS+=("uvicorn $module")
done <<< "$APPS"
MARKERS+=("data_agent.trending_collect" "data_agent.youtube_collect" "shop_traffic.py")
COLLECTOR_LOCKS=("$DATA/youtube_collect.lock" "$DATA/trending_collect.lock")

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

# 상태 확인 주소 (dejafix-proxy만 /healthz, 나머지는 /health)
health_url() {
  if [ "$1" = "dejafix-proxy" ]; then echo "http://127.0.0.1:$2/healthz"; else echo "http://127.0.0.1:$2/health"; fi
}

# bash의 $!는 Windows 프로세스 번호가 아니다. .pid에는 Windows 번호(/proc/<pid>/winpid)를 적는다
winpid_of() { cat "/proc/$1/winpid" 2>/dev/null || echo "$1"; }

# >>> 로그 보관
# 켤 때 지난 로그를 덮어쓰지 않는다: .logs/<이름>.log를 .logs/prev/<이름>-<그 로그를 켠 시각>.log로 옮기고,
# 이름마다 최근 KEEP_LOGS(기본 5)개만 남긴다(오래된 것부터 지움). 갑자기 꺼진 서버의 마지막 줄을 다음에 볼 수 있게
KEEP_LOGS="${KEEP_LOGS:-5}"
keep_log() {
  local name="$1" file="$LOGS/$1.log" prev="$LOGS/prev" stamp target n=1
  [ -s "$file" ] || return 0
  mkdir -p "$prev"
  # 첫 줄 "[start] … · 시작 2026-11-12 15:43:22 +0900"의 시각. 없으면 파일 수정 시각
  stamp=$(sed -n '1s/.*시작 \([0-9]\{4\}\)-\([0-9]\{2\}\)-\([0-9]\{2\}\) \([0-9]\{2\}\):\([0-9]\{2\}\):\([0-9]\{2\}\).*/\1\2\3-\4\5\6/p' "$file")
  [ -n "$stamp" ] || stamp=$(date -r "$file" '+%Y%m%d-%H%M%S' 2>/dev/null || date '+%Y%m%d-%H%M%S')
  target="$prev/$name-$stamp.log"
  while [ -e "$target" ]; do n=$((n + 1)); target="$prev/$name-$stamp-$n.log"; done
  mv "$file" "$target"
  # 이름 뒤가 숫자(시각)인 것만 센다 (shop-api와 shop-traffic처럼 앞이 같은 이름이 섞이지 않게)
  # 새것부터 늘어놓고(수정 시각, mv는 시각을 바꾸지 않는다) KEEP_LOGS개 뒤부터 지운다
  ls -1t "$prev/$name-"[0-9]*.log 2>/dev/null | tail -n "+$((KEEP_LOGS + 1))" | while read -r old; do rm -f "$old"; done
}
# <<< 로그 보관

# 끄기: 포트를 쥔 프로세스 + 기록한 pid와 그 자식(venv 런처 밑 실제 python) + 수집기 잠금에 적힌 pid.
# 끈 뒤 표식으로 다시 찾아 "남은 프로세스 0개"를 확인한다. 남으면 pid·명령줄을 보여 주고 실패(1)
stop_all() {
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "foreach(\$p in @($PORTS)){Get-NetTCPConnection -LocalPort \$p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue }}" >/dev/null 2>&1
  fi
  local pids=""
  for f in "$LOGS"/*.pid; do
    [ -f "$f" ] || continue
    kill "$(cat "$f")" 2>/dev/null  # 예전 .pid(bash 번호)도 끈다
    pids="$pids $(cat "$f")"
    rm -f "$f"
  done
  "$PY" scripts/procs.py stop --pids "$pids" --locks "${COLLECTOR_LOCKS[@]}" -- "${MARKERS[@]}"
}

if [ "$CMD" = "stop" ]; then
  if ! stop_all; then
    echo "stop 실패: 위 프로세스가 남아 있습니다" >&2
    exit 1
  fi
  echo "stopped"
  exit 0
fi

# 테스트로 쌓인 기록을 지우고 처음부터: 캣북스 주문·AI 대화 기록·인사 기록·LLM 사용량·이벤트 버스
# (캣북스 쇼핑몰은 비어 있으면 시드를 다시 넣는다)
if [ "$CMD" = "reset" ]; then
  if ! stop_all; then
    echo "reset 실패: 위 프로세스가 남아 있어 기록을 지우지 않았습니다" >&2
    exit 1
  fi
  rm -f "$DATA"/*.db "$DATA"/*.db-wal "$DATA"/*.db-shm "$DATA"/web.env "$DATA"/tokens.env
  docker exec ax-redis redis-cli FLUSHALL >/dev/null 2>&1 || true
  echo "reset: backend/.data 와 이벤트 버스를 비웠습니다. 다시 켜려면 bash scripts/run_all.sh"
  exit 0
fi

if [ "$CMD" = "mock" ]; then
  echo "목 모드는 지웠습니다. 로컬도 실제 모드 하나로 돕니다 (*_MOCK 스위치는 CI 단위 테스트 전용)." >&2
  exit 2
fi

# 켜기: 같은 서버·수집기가 이미 떠 있으면 켜지 않는다 (옛 코드가 남아 새 코드를 막지 않게)
if ! "$PY" scripts/procs.py running "${MARKERS[@]}"; then
  exit 1
fi
rm -f "$LOGS"/*.pid  # 떠 있는 것이 없으니 남은 .pid는 끝난 프로세스 것
# 로그 첫 줄: 어느 코드로 언제 켰는지 (수집기 [quota] 줄에도 짧은 커밋이 붙는다)
AX_GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "알 수 없음")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "알 수 없음")
export AX_GIT_COMMIT
START_LINE="[start] 코드 $AX_GIT_COMMIT ($GIT_BRANCH) · 시작 $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "$START_LINE"

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
# marketing: 이미지·영상 생성 비용 장부(월 누적). 다시 켜도 남아야 월 상한이 유지된다
export MARKETING_MEDIA_LEDGER_DB="$PWD/$DATA/marketing_media_ledger.db"
# marketing: publish_sns 게시 대기 건(승인 대기·게시 중)을 파일에 남겨 다시 켜도 이어지게
export MARKETING_SNS_PENDING_DB="$PWD/$DATA/marketing_sns_pending.db"
# marketing: SNS 게시 초안도 파일에 남겨 다시 켜도 post_draft_id를 찾게 한다
export MARKETING_SNS_DRAFT_DB="$PWD/$DATA/marketing_sns_drafts.db"
# marketing: 마케팅 AI와 사람의 직접 대화를 파일에 남겨 다시 켜도 대화가 유지되게
export MARKETING_CHAT_DB="$PWD/$DATA/marketing_chat.db"
# 인프라 AI 직접 대화 저장 (incident)
export INFRA_CHAT_DB="$PWD/$DATA/infra_chat.db"
# 기획·개발은 실제 시각으로 (기본은 10-22에서 1분씩 가는 시연 시계라 총괄이 사건 구간으로 묻는
# 관련 PR 찾기·교차검증 최근 7일 장애가 실제 머지를 못 잡는다)
export PLANNING_CLOCK=real
# A2A 주고받은 기록(AI 대화방이 읽는다)
export AX_COMMON_LOG_DB="$PWD/$DATA/common_log.db"
# 데이터분석 업무 리포트 일간 저장 (data 자체 파일. 하루 한 번 미리 만들어 화면이 바로 읽는다)
export DATA_REPORT_DB="$PWD/$DATA/data_reports.db"
# data: 트렌드 감지 탭 계산 결과 (수집기가 새 스냅샷 직후 미리 계산해 넣고, 화면은 읽기만. 다시 켜도 남음)
export DATA_TREND_RESPONSES_DB="$PWD/$DATA/trend_responses.db"
# data: 설명 문장 캐시·네이버 하루 캐시(응답·호출 수)도 파일에 (다시 켜도 LLM·네이버를 다시 안 부름)
export DATA_EXPLAIN_DB="$PWD/$DATA/explain_cache.db"
# data: YouTube 할당량 장부 하나 (서버·수집기가 같은 파일, 하루 상한 DATA_YOUTUBE_DAILY_UNITS 기본 8000)
export DATA_YOUTUBE_QUOTA_DB="$PWD/$DATA/youtube_quota.db"
export DATA_NAVER_CACHE_DB="$PWD/$DATA/naver_cache.db"
# data: LLM 하루 상한 기록 (서버·수집기가 같이 셈. 상한은 .env의 DATA_LLM_DAILY_CALLS·DATA_LLM_DAILY_TOKENS)
export DATA_LLM_BUDGET_DB="$PWD/$DATA/data_llm_budget.db"
# data: 브리프·기회 카드 한 줄 재사용 (같은 id + 같은 근거 숫자면 LLM에 다시 묻지 않음)
export DATA_BRIEF_LINES_DB="$PWD/$DATA/brief_lines.db"

# 업무 요청 라우터 (README "업무 요청 라우터"): 켠 서비스면 에이전트로, 안 켠 서비스면 사람 할 일함으로
# 켠 서비스는 backend/.env 의 AX_ENABLED_SERVICES (쉼표 목록). 없거나 비어 있으면 전부 켠다(예전 동작)
if [ -z "${AX_ENABLED_SERVICES:-}" ] && [ -f .env ]; then
  AX_ENABLED_SERVICES=$(tr -d '\r' < .env | sed -n 's/^AX_ENABLED_SERVICES=//p' | tail -n 1 | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//; s/^"(.*)"$/\1/')
fi
export AX_ENABLED_SERVICES="${AX_ENABLED_SERVICES:-}"
# 켠 서비스 파일 ({"enabled": [...], "updated_at", "updated_by"}). 있으면 위 환경변수보다 먼저 쓰고,
# 바꾸면 재시작 없이 반영된다. 없거나 깨졌으면 환경변수를 쓴다 (ax_agent_template.routing)
export AX_ENABLED_SERVICES_FILE="${AX_ENABLED_SERVICES_FILE:-$PWD/$DATA/enabled_services.json}"
# 회사 연결(시스템 관리 > 회사 연결에서 정한 고객사 판매 서버 주소). 있으면 AX_SHOP_API_URL보다 먼저, 재시작 없이 반영
export AX_COMPANY_CONNECTION_FILE="${AX_COMPANY_CONNECTION_FILE:-$PWD/$DATA/company_connection.json}"
# 사람 할 일함 저장 파일 (dashboard-api)
export AX_HANDOFF_DB="$PWD/$DATA/handoffs.db"
# 오피스 단톡방 대화 목록 (dashboard-api). 다시 켜도 단톡방 대화가 이어진다
export AX_OFFICE_ROOM_DB="$PWD/$DATA/office_room.db"
# 총괄 이벤트 저장소 (다시 켜도 이벤트 기록과 처리한 event_id가 남는다)
export ORCHESTRATOR_DB="$PWD/$DATA/orchestrator.db"
# 재무 기록(판매자 심사·정산 보류·장부·사람 결정). 다시 켜도 이어진다
export FINANCE_STATE_FILE="$PWD/$DATA/finance_state.json"
# 판매자 정산 묶음 기록(고객지원, settlement). 다시 켜도 이어진다. 처음 한 번 재무 기록의 묶음을 가져온다
export SUPPORT_SETTLEMENT_FILE="$PWD/$DATA/support_settlement.json"
export SUPPORT_SETTLEMENT_IMPORT_FILE="$FINANCE_STATE_FILE"
# 고객지원 AI가 지급일이 된 주의 정산 지급 승인을 스스로 요청한다(10분마다 확인). 승인은 고객지원 담당
export SUPPORT_SETTLEMENT_AUTO_REQUEST="${SUPPORT_SETTLEMENT_AUTO_REQUEST:-on}"
export FINANCE_EXPORT_DIR="${FINANCE_EXPORT_DIR:-$PWD/$DATA/finance_exports}"
export FINANCE_BANK_INBOX="${FINANCE_BANK_INBOX:-$PWD/$DATA/finance_bank_inbox}"
# PG 정산 파일(CSV) 폴더. 넣으면 재무가 읽어 그 판매일 기간을 파일과 대사한다
export FINANCE_PG_INBOX="${FINANCE_PG_INBOX:-$PWD/$DATA/finance_pg_inbox}"
mkdir -p "$FINANCE_EXPORT_DIR" "$FINANCE_BANK_INBOX" "$FINANCE_PG_INBOX"
# 외부 연결 쓰기 보류(긴급 취약). 다시 켜도 잊지 않게 파일에 둔다
export SAFETY_CONNECTION_HOLDS_FILE="$PWD/$DATA/connection_holds.json"
# 안전 관문 상태(실행 기록·멱등성 키·보류 큐·감사 로그). 다시 켜도 보류 건과 실행 기록이 남는다
export SAFETY_STATE_DB="$PWD/$DATA/safety_state.db"

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

# >>> dejafix-proxy 계정 확인 (tests/test_run_all_procs.py가 이 부분만 실행한다)
# DejaFix 계정(.env의 DEJAFIX_USERNAME)이 없으면 띄우지 않는다. 띄우면 DejaFix가 없는 PC에서
# 로그인 실패만 쌓인다. 계정·비밀번호 값은 출력하지 않는다
if [ -z "${DEJAFIX_USERNAME:-}" ] && echo "$RUN_APPS" | awk '$1 == "dejafix-proxy" {found=1} END {exit !found}'; then
  RUN_APPS=$(echo "$RUN_APPS" | awk 'NF && $1 != "dejafix-proxy"')
  echo "dejafix-proxy 안 띄움(DEJAFIX_USERNAME 없음)"
fi
# <<< dejafix-proxy 계정 확인

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
        [ "$(curl -s -o /dev/null -w "%{http_code}" "$(health_url "$n" "$p")" || true)" = "200" ] && up=$((up + 1))
      done <<< "$RUN_APPS"
      [ "$up" -ge "$started" ] && break
      sleep 1
    done
  fi
  keep_log "$name"
  echo "$START_LINE" > "$LOGS/$name.log"
  # shop-api만 바인드 주소를 바꿀 수 있다(AX_SHOP_API_HOST, 기본 127.0.0.1). 나머지는 127.0.0.1
  host=127.0.0.1
  [ "$name" = "shop-api" ] && host="${AX_SHOP_API_HOST:-127.0.0.1}"
  "$PY" -m uvicorn "$module" --host "$host" --port "$port" >> "$LOGS/$name.log" 2>&1 &
  winpid_of $! > "$LOGS/$name.pid"
  started=$((started + 1))
done <<< "$RUN_APPS"

# >>> 주문 트래픽 스위치 (tests/test_run_all_procs.py가 이 부분만 실행한다)
# 캣북스 가상 주문(scripts/shop_traffic.py)은 .env의 SHOP_TRAFFIC=on일 때만 넣는다(기본 꺼짐).
# 꺼도 같은 스크립트가 발송·도착 표시와 재고 보충은 계속 한다(SHOP_TRAFFIC_ORDERS=0). 발송·도착이
# 안 적히면 사람이 사이트에서 한 주문도 재무 판매자 정산에서 보류된다. 결제 장애 감지는 결제
# 표본(60초에 최소 5건)이 있어야 해서 장애 시연 때는 on으로 켠다
SHOP_TRAFFIC_ORDERS=0
case "${SHOP_TRAFFIC:-off}" in
  on | 1 | true) SHOP_TRAFFIC_ORDERS=1 ;;
esac
if [ "$SHOP_TRAFFIC_ORDERS" != 1 ] && echo "$RUN_APPS" | awk '$1 == "shop-api" {found=1} END {exit !found}'; then
  echo "캣북스 가상 주문 꺼짐(배송 표시·재고 보충만, SHOP_TRAFFIC=on이면 주문도 넣음)"
fi
# <<< 주문 트래픽 스위치

# 캣북스 주문 트래픽·배송 표시 (shop-api를 띄웠을 때만. 가상 주문은 위 스위치가 정한다)
if echo "$RUN_APPS" | awk '$1 == "shop-api" {found=1} END {exit !found}'; then
  # 재고가 0이 되면 주문이 조용히 멈춰 장애가 안 잡히는 것처럼 보인다. 자동 보충을 기본으로 켠다
  # (끄려면 SHOP_TRAFFIC_REFILL=0). 보충에는 위에서 만든 admin 토큰을 쓴다
  keep_log shop-traffic
  echo "$START_LINE" > "$LOGS/shop-traffic.log"
  SHOP_API_URL=http://127.0.0.1:8010 SHOP_TRAFFIC_REFILL="${SHOP_TRAFFIC_REFILL:-1}" SHOP_TRAFFIC_ORDERS="$SHOP_TRAFFIC_ORDERS" \
    "$PY" scripts/shop_traffic.py >> "$LOGS/shop-traffic.log" 2>&1 &
  winpid_of $! > "$LOGS/shop-traffic.pid"
fi

# data: Google 인기 검색어 스냅샷을 1시간마다 모은다 (data를 띄웠을 때만, 끄기는 stop이 .pid로 같이 끈다)
if echo "$RUN_APPS" | awk '$1 == "data" {found=1} END {exit !found}'; then
  keep_log trending-collect
  echo "$START_LINE" > "$LOGS/trending-collect.log"
  "$PY" -m data_agent.trending_collect --every 3600 >> "$LOGS/trending-collect.log" 2>&1 &
  winpid_of $! > "$LOGS/trending-collect.pid"
  # YouTube 스냅샷도 1시간마다 (수집 직후 감지 탭 결과를 미리 계산). 이미 다른 수집기가 돌면
  # 잠금(backend/.data/youtube_collect.lock)을 못 잡아 바로 끝난다 → 두 개가 같이 돌지 않는다
  keep_log youtube-collect
  echo "$START_LINE" > "$LOGS/youtube-collect.log"
  "$PY" -m data_agent.youtube_collect --every 3600 >> "$LOGS/youtube-collect.log" 2>&1 &
  winpid_of $! > "$LOGS/youtube-collect.pid"
  # 수집기 두 개를 켰다는 줄 (잠금을 못 잡아 끝났는지는 각 로그 첫 줄, 상태는 data_sources 출처 현황)
  echo "수집기: trending_collect·youtube_collect 1시간마다 (로그 $LOGS/trending-collect.log, $LOGS/youtube-collect.log)"
fi

sleep 8
echo "모드: 실제"
if [ -f "$AX_ENABLED_SERVICES_FILE" ]; then
  echo "켠 서비스: 파일 $AX_ENABLED_SERVICES_FILE (바꾸면 재시작 없이 반영)  조직도: ${AX_ORG_CONFIG:-없음 (꺼진 서비스로 가는 일은 no_assignee로 실패)}"
else
  echo "켠 서비스: ${AX_ENABLED_SERVICES:-전부}  조직도: ${AX_ORG_CONFIG:-없음 (꺼진 서비스로 가는 일은 no_assignee로 실패)}"
fi
if [ -f "$AX_COMPANY_CONNECTION_FILE" ]; then
  echo "회사 연결: 파일 $AX_COMPANY_CONNECTION_FILE 의 판매 서버 주소를 씁니다 (시스템 관리 > 회사 연결에서 해제)"
fi
while read -r name port _; do
  [ -n "$name" ] || continue
  code=$(curl -s -o /dev/null -w "%{http_code}" "$(health_url "$name" "$port")" || true)
  printf "%-14s %s  %s\n" "$name" "$port" "$code"
done <<< "$RUN_APPS"
[ -z "$SKIPPED" ] || echo "안 띄움: $SKIPPED"
echo "화면: bash scripts/run_web.sh (backend/.data/web.env 값으로 프론트 개발 서버를 켠다)"
wait
