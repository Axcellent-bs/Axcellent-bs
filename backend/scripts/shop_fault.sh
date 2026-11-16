#!/usr/bin/env bash
# 고객사 쇼핑몰(캣북스)에 결제 장애를 넣고 뺀다. 실제 모드(run_all.sh)가 떠 있을 때 운영자가 쓴다.
# AI 대화방에는 장애 버튼이 없다. 장애는 사람이 요청하면 운영자가 이 명령으로 넣는다.
# 판매 서버와 장애 스크립트는 캣북스 레포(Catbooks-demo/catbooks-demo)로 옮겼다. 이 파일은 옆 클론의
# backend/scripts/shop_fault.sh를 부르는 얇은 래퍼다(CATBOOKS_DEMO_DIR, 기본 이 레포 옆 catbooks-demo).
#   bash backend/scripts/shop_fault.sh failure    # 결제가 서버 오류(5xx)로 실패 → 약 25초 뒤 사건이 열린다
#   bash backend/scripts/shop_fault.sh latency    # 결제 응답 2초 지연
#   bash backend/scripts/shop_fault.sh lost       # 결제사는 승인했는데 응답이 끊김(시간 초과)
#   bash backend/scripts/shop_fault.sh recheck    # 시간 초과 결제를 지금 다시 확인
#   bash backend/scripts/shop_fault.sh clear      # 해제
#   bash backend/scripts/shop_fault.sh            # 지금 상태
#   bash backend/scripts/shop_fault.sh pg-slow [ms] | pg-ok   # 결제사 쪽 지연 (docs/ops/shop-fault-test.md)
# 관리자 토큰은 run_all.sh 가 만든 backend/.data/web.env 에서 읽어 SHOP_ADMIN_TOKEN으로 넘긴다(출력하지 않는다).
set -eu
cd "$(dirname "$0")/.."
CATBOOKS_DEMO_DIR="${CATBOOKS_DEMO_DIR:-$PWD/../../catbooks-demo}"
SCRIPT="$CATBOOKS_DEMO_DIR/backend/scripts/shop_fault.sh"
if [ ! -f "$SCRIPT" ]; then
  echo "캣북스 레포가 없습니다: $CATBOOKS_DEMO_DIR (git clone https://github.com/Catbooks-demo/catbooks-demo 또는 CATBOOKS_DEMO_DIR)" >&2
  exit 1
fi
TOKEN=$(sed -n 's/^VITE_SHOP_ADMIN_TOKEN=//p' .data/web.env 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "backend/.data/web.env 가 없습니다. 먼저 bash scripts/run_all.sh 로 실제 모드 백엔드를 띄우세요." >&2
  exit 1
fi
SHOP_ADMIN_TOKEN="$TOKEN" SHOP_API_URL="${SHOP_API_URL:-http://127.0.0.1:8010}" exec bash "$SCRIPT" "$@"
