#!/usr/bin/env bash
# 시연 준비: "방금 shop-api를 배포했다"는 기록을 인프라 AI와 개발 AI에 같이 남긴다.
# 장애를 넣기(shop_fault.sh failure) 직전에 한 번 부른다. 실제 모드(run_all.sh)가 떠 있을 때 운영자가 쓴다.
#   bash backend/scripts/demo_deploy.sh
#
# 왜 필요한가: 인프라 AI 진단은 장애 직전 60분 안의 배포만 원인 후보로 보고, 개발 AI 롤백 교차검증은
# 그 배포 직전 30분 안에 머지된 PR이 있어야 승인한다. 시연 데이터의 마지막 배포(10/21)로는 둘 다
# 안 걸려서 사람에게 넘어간다. 이 기록을 남기면 원인 배포 → 교차검증 승인 → 자동 롤백 → 복구 확인이 이어진다.
# 두 기록 모두 서버 메모리·상태에만 남는다(인프라 기록은 재시작하면 사라진다).
# 운영자(admin) 토큰은 run_all.sh 가 만든 backend/.data/web.env 에서 읽는다(출력하지 않는다).
set -eu
cd "$(dirname "$0")/.."
INFRA="${INFRA_URL:-http://127.0.0.1:8107}/api/infra/demo-deploy"
DEV="${DEV_URL:-http://127.0.0.1:8111}/api/planning/demo/incident-merge"
TOKEN=$(sed -n 's/^VITE_SHOP_ADMIN_TOKEN=//p' .data/web.env 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  echo "backend/.data/web.env 가 없습니다. 먼저 bash scripts/run_all.sh 로 실제 모드 백엔드를 띄우세요." >&2
  exit 1
fi
AUTH="Authorization: Bearer $TOKEN"

# 1) 인프라 AI: 지금 시각 배포 기록 (버전은 마지막 배포에서 하나 올린다)
infra=$(curl -s -X POST "$INFRA" -H "$AUTH" -H "Content-Type: application/json" -d '{}')
version=$(echo "$infra" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')
service=$(echo "$infra" | sed -n 's/.*"service":"\([^"]*\)".*/\1/p')
if [ -z "$version" ] || [ -z "$service" ]; then
  echo "인프라 AI 배포 기록 실패: $infra" >&2
  exit 1
fi
echo "인프라 AI: $service $version 배포 기록"

# 2) 개발 AI: 그 버전으로 나간 머지 PR 기록 (롤백 교차검증이 찾는다)
dev=$(curl -s -X POST "$DEV" -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"deployed_version\":\"$service $version\"}")
pr=$(echo "$dev" | sed -n 's/.*"id":"\(PR #[0-9]*\)".*/\1/p')
if [ -z "$pr" ]; then
  echo "개발 AI 머지 기록 실패: $dev" >&2
  exit 1
fi
echo "개발 AI: $pr 머지 → $service $version 기록"
echo "이제 장애를 넣으세요: bash backend/scripts/shop_fault.sh failure"
