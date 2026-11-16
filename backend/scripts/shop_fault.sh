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
# 따로 배포한 캣북스: SHOP_API_URL(판매 서버 주소)과 SHOP_ADMIN_TOKEN(캣북스 관리자 토큰)을 주고 부른다.
#   SHOP_API_URL=https://… SHOP_ADMIN_TOKEN=… bash backend/scripts/shop_fault.sh status
# 하나만 주면 멈춘다(로컬 토큰을 다른 주소로 보내거나, 토큰만 주고 127.0.0.1로 보내지 않게).
# 둘 다 없을 때만 로컬 판매 서버(CATBOOKS_LOCAL=on으로 run_all.sh가 띄운 것)로 본다: 주소 http://127.0.0.1:8010,
# 토큰은 run_all.sh 가 만든 backend/.data/web.env 의 VITE_SHOP_ADMIN_TOKEN(그 판매 서버만 받는 값). 출력하지 않는다.
set -eu
cd "$(dirname "$0")/.."
CATBOOKS_DEMO_DIR="${CATBOOKS_DEMO_DIR:-$PWD/../../catbooks-demo}"
SCRIPT="$CATBOOKS_DEMO_DIR/backend/scripts/shop_fault.sh"
if [ ! -f "$SCRIPT" ]; then
  echo "캣북스 레포가 없습니다: $CATBOOKS_DEMO_DIR (git clone https://github.com/Catbooks-demo/catbooks-demo 또는 CATBOOKS_DEMO_DIR)" >&2
  exit 1
fi
URL="${SHOP_API_URL:-}"
TOKEN="${SHOP_ADMIN_TOKEN:-}"
if [ -n "$URL" ] && [ -n "$TOKEN" ]; then
  :  # 따로 배포한 캣북스 (또는 직접 고른 판매 서버)
elif [ -z "$URL" ] && [ -z "$TOKEN" ]; then
  # 로컬 판매 서버(CATBOOKS_LOCAL=on으로 run_all.sh가 띄운 것)만
  URL="http://127.0.0.1:8010"
  TOKEN=$(sed -n 's/^VITE_SHOP_ADMIN_TOKEN=//p' .data/web.env 2>/dev/null || true)
  if [ -z "$TOKEN" ]; then
    echo "backend/.data/web.env 가 없습니다. 로컬이면 먼저 CATBOOKS_LOCAL=on bash scripts/run_all.sh 로 띄우세요." >&2
    echo "따로 배포한 캣북스면 SHOP_API_URL과 SHOP_ADMIN_TOKEN(캣북스 관리자 토큰)을 둘 다 주세요." >&2
    exit 1
  fi
else
  echo "SHOP_API_URL과 SHOP_ADMIN_TOKEN(캣북스 관리자 토큰)은 둘 다 주거나 둘 다 비워야 합니다(둘 다 비우면 로컬 판매 서버)." >&2
  exit 1
fi
SHOP_ADMIN_TOKEN="$TOKEN" SHOP_API_URL="$URL" exec bash "$SCRIPT" "$@"
