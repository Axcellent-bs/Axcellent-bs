#!/usr/bin/env bash
# 시연: 잘못된 배포. 실제 모드(run_all.sh)가 떠 있을 때 운영자가 쓴다 (docs/ops/shop-fault-test.md 시나리오 C).
#   bash backend/scripts/demo_deploy.sh --bad     # 쇼핑몰 결제 설정을 v2(잘못된 배포)로 바꾸고 배포·PR 기록을 남긴다
#   bash backend/scripts/demo_deploy.sh --reset   # 사람이 v1로 되돌리는 수동 복구 (AI가 되돌렸으면 기록만 맞춘다)
#   bash backend/scripts/demo_deploy.sh           # (예전) 배포 기록만 남긴다. 장애는 shop_fault.sh로 따로 넣는다
#
# --bad 가 남기는 것:
#   1) shop-api 결제 설정 v1 → v2. v2는 결제사 승인이 500대로 실패한다(토스 테스트 키면
#      TossPayments-Test-Code, 가짜 결제사면 같은 코드). 장애 스위치(active)는 꺼진 그대로다.
#   2) 인프라 AI 배포 기록: 이전 v1(쇼핑몰이 v1이 된 시각) → 새 v2(지금). 원인 진단이 이 배포를 찾는다.
#   3) 개발 AI 머지 PR 기록: v2로 나간 PR. 롤백 교차검증(배포 직전 30분 안 머지)이 이 PR을 찾는다.
# 장애 감지(약 25초) → 진단 → 되돌리기(B면 자동, 위험하면 C로 사람 승인) → v1 → 지표로 복구.
# 라이브 결제 키가 있으면 shop-api가 v2를 거절한다. 인프라·개발 기록은 서버 메모리에만 남는다.
# 운영자(admin) 토큰은 run_all.sh 가 만든 backend/.data/web.env 에서 읽는다(출력하지 않는다).
set -eu
cd "$(dirname "$0")/.."
SHOP="${SHOP_API_URL:-http://127.0.0.1:8010}/api/shop/admin/payment-version"
INFRA="${INFRA_URL:-http://127.0.0.1:8107}/api/infra/demo-deploy"
DEV="${DEV_URL:-http://127.0.0.1:8111}/api/planning/demo/incident-merge"
SERVICE="shop-api"
TOKEN=$(sed -n 's/^VITE_SHOP_ADMIN_TOKEN=//p' .data/web.env 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "backend/.data/web.env 가 없습니다. 먼저 bash scripts/run_all.sh 로 실제 모드 백엔드를 띄우세요." >&2
  exit 1
fi
AUTH="Authorization: Bearer $TOKEN"
JSON="Content-Type: application/json"

field() {  # field <json> <이름>: 맨 처음 나오는 "이름":"값"의 값
  printf '%s' "$1" | grep -o "\"$2\":\"[^\"]*\"" | head -n 1 | sed 's/^"[^"]*":"\(.*\)"$/\1/' || true
}

shop_version() {
  curl -s "$SHOP" -H "$AUTH"
}

dev_merge() {  # dev_merge <서비스 버전> <제목>
  curl -s -X POST "$DEV" -H "$AUTH" -H "$JSON" -d "{\"deployed_version\":\"$1\",\"title\":\"$2\"}"
}

case "${1:-}" in
  --bad)
    now=$(shop_version)
    current=$(field "$now" version)
    since=$(field "$now" since)
    if [ -z "$current" ] || [ -z "$since" ]; then
      echo "쇼핑몰 결제 설정 버전을 읽지 못했습니다: $now" >&2
      exit 1
    fi
    if [ "$current" = "v2" ]; then
      echo "이미 v2(잘못된 배포)입니다. 먼저 --reset 으로 되돌리세요." >&2
      exit 1
    fi
    deployed=$(curl -s -X PUT "$SHOP" -H "$AUTH" -H "$JSON" -d '{"version":"v2"}')
    if [ "$(field "$deployed" version)" != "v2" ]; then
      echo "쇼핑몰 결제 설정을 v2로 바꾸지 못했습니다: $deployed" >&2
      exit 1
    fi
    echo "쇼핑몰: 결제 설정 $current → v2 (잘못된 배포)"
    body="{\"service\":\"$SERVICE\",\"version\":\"v2\",\"previous_version\":\"$current\",\"previous_at\":\"$since\",\"applies_to\":\"shop_payment_config\",\"change_summary\":\"결제 설정 v2 배포 (잘못된 배포 시연)\"}"
    infra=$(curl -s -X POST "$INFRA" -H "$AUTH" -H "$JSON" -d "$body")
    if [ "$(field "$infra" version)" != "v2" ]; then
      echo "인프라 AI 배포 기록 실패: $infra (쇼핑몰은 v2입니다. --reset 으로 되돌리세요)" >&2
      exit 1
    fi
    echo "인프라 AI: $SERVICE $current → v2 배포 기록"
    dev=$(dev_merge "$SERVICE v2" "결제 승인 요청 설정 변경 (잘못된 배포 시연)")
    pr=$(printf '%s' "$dev" | sed -n 's/.*"id":"\(PR #[0-9]*\)".*/\1/p' | head -n 1)
    if [ -z "$pr" ]; then
      echo "개발 AI 머지 기록 실패: $dev (쇼핑몰은 v2입니다. --reset 으로 되돌리세요)" >&2
      exit 1
    fi
    echo "개발 AI: $pr 머지 → $SERVICE v2 기록"
    echo "약 25초 뒤 사건이 열립니다. 끝나면: bash backend/scripts/demo_deploy.sh --reset"
    ;;
  --reset)
    reset=$(curl -s -X PUT "$SHOP" -H "$AUTH" -H "$JSON" -d '{"version":"v1"}')
    if [ "$(field "$reset" version)" != "v1" ]; then
      echo "쇼핑몰 결제 설정을 v1로 되돌리지 못했습니다: $reset" >&2
      exit 1
    fi
    echo "쇼핑몰: 결제 설정 $(field "$reset" before) → v1"
    # 인프라 기록에서 지금 돌지 않는 배포(v2)를 되돌림으로 표시한다(다음 진단 후보에서 빠지게)
    marked=$(curl -s -X POST "$INFRA/reset" -H "$AUTH" -H "$JSON" \
      -d "{\"service\":\"$SERVICE\",\"running_version\":\"v1\"}")
    echo "인프라 AI 기록 정리: $marked"
    ;;
  "")
    # (예전) 지금 시각 배포 기록만. 버전은 마지막 배포에서 하나 올린다
    infra=$(curl -s -X POST "$INFRA" -H "$AUTH" -H "$JSON" -d '{}')
    version=$(field "$infra" version)
    service=$(field "$infra" service)
    if [ -z "$version" ] || [ -z "$service" ]; then
      echo "인프라 AI 배포 기록 실패: $infra" >&2
      exit 1
    fi
    echo "인프라 AI: $service $version 배포 기록"
    dev=$(dev_merge "$service $version" "결제 타임아웃 재조정 (시연 배포)")
    pr=$(printf '%s' "$dev" | sed -n 's/.*"id":"\(PR #[0-9]*\)".*/\1/p' | head -n 1)
    if [ -z "$pr" ]; then
      echo "개발 AI 머지 기록 실패: $dev" >&2
      exit 1
    fi
    echo "개발 AI: $pr 머지 → $service $version 기록"
    echo "이제 장애를 넣으세요: bash backend/scripts/shop_fault.sh failure"
    ;;
  *) echo "사용법: demo_deploy.sh [--bad|--reset]" >&2; exit 2 ;;
esac
