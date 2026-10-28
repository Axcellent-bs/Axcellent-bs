#!/usr/bin/env bash
# 고객사 쇼핑몰(캣북스)에 결제 장애를 넣고 뺀다. 실제 모드(run_all.sh)가 떠 있을 때 운영자가 쓴다.
# AI 대화방에는 장애 버튼이 없다. 장애는 사람이 요청하면 운영자가 이 명령으로 넣는다.
#   bash backend/scripts/shop_fault.sh failure    # 결제가 서버 오류(5xx)로 실패 → 약 25초 뒤 사건이 열린다
#   bash backend/scripts/shop_fault.sh latency    # 결제 응답 2초 지연
#   bash backend/scripts/shop_fault.sh clear      # 해제
#   bash backend/scripts/shop_fault.sh refund-abuse [구매자 건수]  # 재무 AI 장애: 한 고객이 주문을 연달아 사고 전부 환불(환불 남용)
#   bash backend/scripts/shop_fault.sh            # 지금 상태
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
  clear) curl -s -X DELETE "$URL" -H "$AUTH" ;;
  status) curl -s "$URL" -H "$AUTH" ;;
  refund-abuse) shift; exec "${PY:-.venv/Scripts/python.exe}" scripts/refund_abuse.py "$@" ;;
  *) echo "사용법: shop_fault.sh [failure|latency|clear|status|refund-abuse]" >&2; exit 2 ;;
esac
echo
