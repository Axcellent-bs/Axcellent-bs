#!/usr/bin/env bash
# 고객사 쇼핑몰(캣북스)에 결제 장애를 넣고 뺀다. 실제 모드(run_all.sh)가 떠 있을 때 운영자가 쓴다.
# AI 대화방에는 장애 버튼이 없다. 장애는 사람이 요청하면 운영자가 이 명령으로 넣는다.
#   bash backend/scripts/shop_fault.sh failure    # 결제가 서버 오류(5xx)로 실패 → 약 25초 뒤 사건이 열린다
#   bash backend/scripts/shop_fault.sh latency    # 결제 응답 2초 지연
#   bash backend/scripts/shop_fault.sh lost       # 결제사는 승인했는데 응답이 끊김(시간 초과)
#   bash backend/scripts/shop_fault.sh recheck    # 시간 초과 결제를 지금 다시 확인
#   bash backend/scripts/shop_fault.sh clear      # 해제
#   bash backend/scripts/shop_fault.sh            # 지금 상태
# 시험용 "결제사 쪽 지연" (AI가 고치지 못하는 장애 시연용, docs/ops/shop-fault-test.md):
#   bash backend/scripts/shop_fault.sh pg-slow [ms]  # 결제마다 실제로 ms(기본 1500)만큼 기다린다.
#                                                     # 장애 스위치가 아니라 AI 해제(clear)로 안 꺼진다
#   bash backend/scripts/shop_fault.sh pg-ok         # 결제사 쪽 지연 끄기 (사람만)
# 관리자 토큰은 run_all.sh 가 만든 backend/.data/web.env 에서 읽는다(출력하지 않는다).
set -eu
cd "$(dirname "$0")/.."
URL="${SHOP_API_URL:-http://127.0.0.1:8010}/api/shop/admin/faults"
TOKEN=$(sed -n 's/^VITE_SHOP_ADMIN_TOKEN=//p' .data/web.env 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "backend/.data/web.env 가 없습니다. 먼저 bash scripts/run_all.sh 로 실제 모드 백엔드를 띄우세요." >&2
  exit 1
fi
AUTH="Authorization: Bearer $TOKEN"
case "${1:-status}" in
  failure) curl -s -X POST "$URL" -H "$AUTH" -H "Content-Type: application/json" -d '{"kind":"failure"}' ;;
  latency) curl -s -X POST "$URL" -H "$AUTH" -H "Content-Type: application/json" -d '{"kind":"latency","delay_ms":2000}' ;;
  lost) curl -s -X POST "$URL" -H "$AUTH" -H "Content-Type: application/json" -d '{"kind":"lost_response"}' ;;
  recheck) curl -s -X POST "${URL%/faults}/payments/recheck" -H "$AUTH" ;;
  clear) curl -s -X DELETE "$URL" -H "$AUTH" ;;
  pg-slow)
    MS="${2:-1500}"
    case "$MS" in '' | *[!0-9]*) echo "pg-slow 지연은 숫자(ms)로: shop_fault.sh pg-slow 1500" >&2; exit 2 ;; esac
    curl -s -X POST "$URL" -H "$AUTH" -H "Content-Type: application/json" -d "{\"kind\":\"pg_slow\",\"delay_ms\":$MS}" ;;
  pg-ok) curl -s -X DELETE "$URL/pg" -H "$AUTH" ;;
  status)
    STATE=$(curl -s "$URL" -H "$AUTH")
    echo "$STATE"
    # 결제사 쪽 지연은 장애 스위치(active)와 따로 보인다
    PG=$(printf '%s' "$STATE" | grep -o '"pg_delay_ms":[0-9]*' | cut -d: -f2 || true)
    if [ -n "$PG" ] && [ "$PG" != "0" ]; then
      echo "결제사 쪽 지연: 켜짐 (${PG}ms, 장애 스위치 아님 — AI가 끌 수 없음, 끄기: shop_fault.sh pg-ok)"
    else
      echo "결제사 쪽 지연: 꺼짐"
    fi
    ;;
  *) echo "사용법: shop_fault.sh [failure|latency|lost|recheck|clear|status|pg-slow [ms]|pg-ok]" >&2; exit 2 ;;
esac
echo
