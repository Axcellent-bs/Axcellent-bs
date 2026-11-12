#!/usr/bin/env bash
# ax-gate — 서버 위임 게이트 (초안, 일반 구현. 구체 서버 값은 비공개 정책 파일에서 읽는다)
#
# 설치 위치: /usr/local/sbin/ax-gate (root:root 755)
#
# 신원은 OS 계정이다. 사람마다 자기 계정으로 접속하고, 정책·감사 로그는 계정 단위다.
#
# 두 가지로 불린다. 같은 파일이다.
#   1) 앞단: sshd 가 authorized_keys 의 command="/usr/local/sbin/ax-gate <계정명>" 으로 부른다.
#      접속한 사람의 계정 권한으로 돈다. 신원은 실제 로그인 계정(id -un)이고, command= 에 적힌
#      계정명은 그것과 같은지 확인만 한다. SSH_ORIGINAL_COMMAND 를 허용 목록으로만 해석하고
#      계정별 허용표·잠금을 확인한 뒤, 요청을 표준 입력으로 뒷단에 넘긴다.
#   2) 뒷단: 앞단이 `sudo -n /usr/local/sbin/ax-gate` (인자 없음)로 부른다. root 로 돈다.
#      신원은 sudo 가 넣어 준 SUDO_USER 만 믿는다. 요청에는 이름이 없고, 있어도 읽지 않는다.
#      표준 입력의 요청을 처음부터 다시 검증하고(앞단을 믿지 않는다) 실제 동작을 한다.
#
# 규칙: eval 없음, 변수는 항상 따옴표, 모든 인자는 화이트리스트 정규식, 비밀값은 출력·기록 안 함.
# 서버마다 다른 것(경로, 서비스 이름, health 주소, 배포 환경변수, location 블록 모양)은 전부
# 정책 파일에 있다. 이 스크립트에는 특정 서버의 값이 없다.

set -Eeuo pipefail
IFS=$' \t\n'
export LC_ALL=C

readonly GATE_VERSION="0.2.0-draft"
readonly ALL_ACTIONS=(status lock unlock nginx-restore nginx-add-location nginx-approve-location deploy-bundle restart-backend rollback-bundle record-pr)
# 실행 전에 팀 채널 알림을 올렸다고 확인(--announced)해야 하는 동작
readonly NEED_ANNOUNCE=(nginx-restore nginx-approve-location deploy-bundle restart-backend rollback-bundle)

# 끝 코드
readonly RC_FAIL=1 RC_USAGE=2 RC_PERM=3 RC_LOCKED=4 RC_CONFIG=5

# ---------------------------------------------------------------------------
# 시험 모드: 서버 없이 로컬 테스트할 때만. SSH 접속 중이거나 root 면 절대 켜지지 않는다.
# (sshd 는 클라이언트 환경변수를 넘기지 않고, sudo 는 환경을 지운다.)
# ---------------------------------------------------------------------------
TEST_ROOT=""
if [[ -n "${OPS_GATE_TEST_ROOT:-}" && -z "${SSH_CONNECTION:-}" && "${EUID}" -ne 0 ]]; then
  TEST_ROOT="${OPS_GATE_TEST_ROOT}"
fi

if [[ -n "${TEST_ROOT}" ]]; then
  SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  readonly SELF="${SELF_DIR}/ops-gate.sh"
  readonly CHECKER="${SELF_DIR}/nginx_location_check.py"
  readonly POLICY_FILE="${TEST_ROOT}/etc/ax-gate/policy.toml"
  readonly LOCK_FILE="${TEST_ROOT}/etc/ax-gate/deploy.lock"
  readonly RUN_LOCK="${TEST_ROOT}/run/lock/ax-gate.run"
  readonly AUDIT_LOG="${TEST_ROOT}/var/log/ax-gate/audit.log"
  readonly PENDING_DIR="${TEST_ROOT}/var/lib/ax-gate/pending"
  readonly APPROVED_DIR="${TEST_ROOT}/var/lib/ax-gate/approved"
  readonly RE_PATH='^([A-Za-z]:)?/[A-Za-z0-9_./-]+$'
  SUDO_CMD=(sudo -n -- bash "${SELF}")
else
  readonly SELF="/usr/local/sbin/ax-gate"
  readonly CHECKER="/usr/local/sbin/ax-gate-location-check"
  readonly POLICY_FILE="/etc/ax-gate/policy.toml"        # root:root 644
  readonly LOCK_FILE="/etc/ax-gate/deploy.lock"          # 배포 금지 잠금 (root:root 644)
  readonly RUN_LOCK="/run/lock/ax-gate.run"              # 실행 중 중복 방지 (금지 잠금과 별개)
  readonly AUDIT_LOG="/var/log/ax-gate/audit.log"
  readonly PENDING_DIR="/var/lib/ax-gate/pending"        # 두 번째 승인을 기다리는 location 제안 (root 755)
  readonly APPROVED_DIR="/var/lib/ax-gate/approved"      # 승인 기록 보관
  readonly RE_PATH='^/[A-Za-z0-9_./-]+$'
  SUDO_CMD=(sudo -n -- "${SELF}")
  PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  export PATH
fi

# 인자 화이트리스트
readonly RE_KEY='^[a-z][a-z0-9-]{1,31}$'
readonly RE_ACTION='^[a-z][a-z-]{1,31}$'
readonly RE_SHA='^[0-9a-f]{7,40}$'
readonly RE_SHA_FULL='^[0-9a-f]{40}$'
readonly RE_REASON='^[A-Za-z0-9][A-Za-z0-9_.,-]{0,79}$'
readonly RE_UNTIL='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$'
readonly RE_FOLDER='^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$'
readonly RE_LOCNAME='^[a-z0-9][a-z0-9-]{0,39}$'
readonly RE_METHOD='^(POST|PUT|PATCH|DELETE)$'
# 1차 거름망일 뿐이다. 진짜 검사는 nginx_location_check.py 가 한다.
readonly RE_LOCREGEX='^\^/[]A-Za-z0-9_/().|\\[^+-]{1,198}\$$'
readonly RE_NONPRINT='[^ -~]'
readonly RE_UINT='^[0-9]{1,9}$'
readonly RE_EPOCH='^[0-9]{1,12}$'
readonly RE_PR='^[0-9]{1,6}$'
readonly RE_DIGEST='^[0-9a-f]{12}$'
# 정책 값 화이트리스트
readonly RE_BRANCH='^(origin/)?[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$'
readonly RE_USER='^[a-z_][a-z0-9_-]{0,31}$'
readonly RE_UNIT='^[A-Za-z0-9_.@-]{1,64}$'
readonly RE_ENVNAME='^[A-Z_][A-Z0-9_]{0,63}$'
readonly RE_ENVPAIR='^[A-Z_][A-Z0-9_]{0,63}=[A-Za-z0-9_.:/@%+,-]{0,200}$'
readonly RE_LOCALURL='^http://(127\.0\.0\.1|localhost):[0-9]{1,5}(/[A-Za-z0-9_./-]*)?$'
readonly RE_MARKERSTR='^[A-Za-z0-9_.-]{3,64}$'
readonly RE_LABEL='^[A-Za-z0-9_-]{1,24}$'
# sqlite 파일 백업: sqlite3 명령이 없는 서버도 있어 파이썬 sqlite3 모듈의 backup API 를 쓴다.
readonly PY_SQLITE_BACKUP='import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()'

LAYER="front"
KEY="-"
ACTION="-"
ARGS=()
DRY=0
ANNOUNCED=0
PR="-"
NOTE="-"
A_ARGS="-"

# ---------------------------------------------------------------------------
# 감사 로그 (추가 전용). 검증을 통과한 값만 적는다. 원문 명령은 적지 않는다.
# ---------------------------------------------------------------------------
audit() {
  local result="$1" ts line mode="real" ann="no"
  (( DRY )) && mode="dry"
  ts="$(date '+%Y-%m-%dT%H:%M:%S%z')"
  (( ANNOUNCED )) && ann="yes"
  printf -v line '%s\tuser=%s\taction=%s\targs=%s\tmode=%s\tannounced=%s\tpr=%s\tnote=%s\tlayer=%s\tresult=%s' \
    "${ts}" "${KEY}" "${ACTION}" "${A_ARGS}" "${mode}" "${ann}" "${PR}" "${NOTE}" "${LAYER}" "${result}"
  if command -v logger >/dev/null 2>&1; then
    logger -t ax-gate -- "${line}" || true
  fi
  printf '%s\n' "${line}" >> "${AUDIT_LOG}"
}

# 거부: 감사 로그에 남기고 끝낸다.
deny() {
  local code="$1" rc="$2"
  shift 2
  printf '거부(%s): %s\n' "${code}" "$*" >&2
  audit "denied:${code}" || printf '경고: 감사 로그에 쓰지 못했다\n' >&2
  exit "${rc}"
}

# 실행 중 실패
step_fail() {
  local code="$1" rc="$2"
  shift 2
  printf '실패(%s): %s\n' "${code}" "$*" >&2
  audit "fail:${code}" || printf '경고: 감사 로그에 쓰지 못했다\n' >&2
  exit "${rc}"
}

say() { printf '%s\n' "$*"; }

# 바꾸는 명령은 전부 run 으로 부른다. DRY_RUN 이면 찍기만 한다.
run() {
  if (( DRY )); then
    printf 'DRY_RUN:'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

# ---------------------------------------------------------------------------
# 정책 파일: TOML 의 좁은 부분집합만 읽는다. 모르는 줄이 하나라도 있으면 전체 거부.
#   [구역] / 키 = "글" / 키 = 123 / 키 = true|false / 키 = ["a", "b"] (한 줄)
# ---------------------------------------------------------------------------
declare -A POLICY=()

check_root_owned() {
  # 운영에서만: root 소유이고 그룹·기타 쓰기 금지여야 한다.
  local f="$1" st uid mode
  [[ -n "${TEST_ROOT}" ]] && return 0
  st="$(stat -c '%u %a' -- "${f}")" || return 1
  uid="${st%% *}"
  mode="${st##* }"
  [[ "${uid}" == "0" ]] || return 1
  (( (8#${mode} & 8#022) == 0 )) || return 1
}

load_policy() {
  local line sec="" k v rest items n=0
  local re_blank='^[[:space:]]*(#.*)?$'
  local re_sec='^\[([a-z_]+)\][[:space:]]*(#.*)?$'
  local re_kv='^([A-Za-z_][A-Za-z0-9_-]*)[[:space:]]*=[[:space:]]*(.*)$'
  local re_str='^"([^"\\]*)"[[:space:]]*(#.*)?$'
  local re_int='^([0-9]{1,9})[[:space:]]*(#.*)?$'
  local re_bool='^(true|false)[[:space:]]*(#.*)?$'
  local re_arr='^\[[[:space:]]*(("[^"\\]*"[[:space:]]*,?[[:space:]]*)*)\][[:space:]]*(#.*)?$'
  local re_item='^"([^"\\]*)"[[:space:]]*,?[[:space:]]*(.*)$'

  if [[ ! -f "${POLICY_FILE}" || ! -r "${POLICY_FILE}" ]]; then
    deny "policy-missing" "${RC_CONFIG}" "정책 파일을 읽을 수 없다"
  fi
  if ! check_root_owned "${POLICY_FILE}"; then
    deny "policy-perms" "${RC_CONFIG}" "정책 파일이 root 소유가 아니거나 남이 쓸 수 있다"
  fi
  while IFS= read -r line || [[ -n "${line}" ]]; do
    n=$((n + 1))
    line="${line%$'\r'}"
    [[ "${line}" =~ ${re_blank} ]] && continue
    if [[ "${line}" =~ ${re_sec} ]]; then
      sec="${BASH_REMATCH[1]}"
      continue
    fi
    if [[ -n "${sec}" && "${line}" =~ ${re_kv} ]]; then
      k="${sec}.${BASH_REMATCH[1]}"
      v="${BASH_REMATCH[2]}"
      if [[ -n "${POLICY[${k}]+x}" ]]; then
        deny "policy-syntax" "${RC_CONFIG}" "정책 파일 ${n}번째 줄: 키가 두 번 나온다"
      fi
      if [[ "${v}" =~ ${re_str} || "${v}" =~ ${re_int} || "${v}" =~ ${re_bool} ]]; then
        POLICY["${k}"]="${BASH_REMATCH[1]}"
        continue
      fi
      if [[ "${v}" =~ ${re_arr} ]]; then
        rest="${BASH_REMATCH[1]}"
        items=""
        while [[ "${rest}" =~ ${re_item} ]]; do
          items+="${BASH_REMATCH[1]}"$'\n'
          rest="${BASH_REMATCH[2]}"
        done
        POLICY["${k}"]="${items}"
        continue
      fi
    fi
    deny "policy-syntax" "${RC_CONFIG}" "정책 파일 ${n}번째 줄을 해석할 수 없다(전체 거부)"
  done < "${POLICY_FILE}"
  validate_self_approve
}

# [self_approve] accounts: 제안자 본인이 자기 제안을 승인해도 되는 계정 목록(기본은 비어 있음 = 금지).
# 키 오타·문자열이 아닌 값·[permissions] 에 없는 계정·형식이 틀린 계정은 정책 로드 실패(전체 거부)다.
validate_self_approve() {
  local k item
  for k in "${!POLICY[@]}"; do
    [[ "${k}" == self_approve.* ]] || continue
    [[ "${k}" == "self_approve.accounts" ]] ||
      deny "policy-value" "${RC_CONFIG}" "정책 [self_approve] 에 알 수 없는 키가 있다(전체 거부)"
  done
  [[ -n "${POLICY[self_approve.accounts]-}" ]] || return 0
  # 한 줄 목록은 항목마다 줄바꿈으로 끝난다. 문자열·숫자·true 값은 그렇지 않다.
  [[ "${POLICY[self_approve.accounts]}" == *$'\n' ]] ||
    deny "policy-value" "${RC_CONFIG}" "정책 self_approve.accounts 는 문자열 목록이어야 한다"
  while IFS= read -r item; do
    [[ -n "${item}" ]] || continue
    [[ "${item}" =~ ${RE_KEY} && -n "${POLICY[permissions.${item}]+x}" ]] ||
      deny "policy-value" "${RC_CONFIG}" "정책 self_approve.accounts 의 계정이 형식에 안 맞거나 [permissions] 에 없다"
  done <<< "${POLICY[self_approve.accounts]}"
}

# 목록 값에 항목이 있는가
policy_list_has() {
  local k="$1" want="$2" item
  [[ -n "${POLICY[${k}]+x}" ]] || return 1
  while IFS= read -r item; do
    [[ -n "${item}" && "${item}" == "${want}" ]] && return 0
  done <<< "${POLICY[${k}]}"
  return 1
}

# 목록 값을 ITEMS 배열로. 두 번째 인자(정규식)가 있으면 항목마다 검사한다.
ITEMS=()
policy_items() {
  local k="$1" re="${2:-}" item
  ITEMS=()
  [[ -n "${POLICY[${k}]+x}" ]] || return 0
  while IFS= read -r item; do
    [[ -n "${item}" ]] || continue
    if [[ -n "${re}" && ! "${item}" =~ ${re} ]]; then
      step_fail "config" "${RC_CONFIG}" "정책 ${k} 에 형식이 맞지 않는 항목이 있다"
    fi
    ITEMS+=("${item}")
  done <<< "${POLICY[${k}]}"
}

policy_uint() { # 키 기본값 → REPLY
  local k="$1" def="$2"
  REPLY="${POLICY[${k}]-${def}}"
  [[ "${REPLY}" =~ ${RE_UINT} ]] || deny "policy-value" "${RC_CONFIG}" "정책 값 ${k} 는 숫자여야 한다"
}

policy_str() { # 키 정규식 → REPLY. 없거나 형식이 틀리면 실패
  local k="$1" re="$2"
  REPLY="${POLICY[${k}]-}"
  [[ "${REPLY}" =~ ${re} ]] || step_fail "config" "${RC_CONFIG}" "정책 ${k} 가 없거나 형식이 틀리다"
}

check_permission() {
  local pk="permissions.${KEY}"
  [[ -n "${POLICY[${pk}]+x}" ]] || pk="permissions._default"
  if ! policy_list_has "${pk}" "${ACTION}"; then
    deny "not-permitted" "${RC_PERM}" "계정 ${KEY} 에게 ${ACTION} 권한이 없다"
  fi
}

# ---------------------------------------------------------------------------
# 인자 검증 (앞단·뒷단 공통)
# ---------------------------------------------------------------------------
usage() {
  cat >&2 <<'EOF'
ax-gate 사용법 (ssh <내 계정>@<서버> <동작> ...)
  status
  lock <사유> [해제시각 YYYY-MM-DDTHH:MM]     사유는 영어·숫자·_.,- 만(띄어쓰기 대신 -)
  unlock
  nginx-restore <백업폴더명>
  nginx-add-location <이름> <POST|PUT|PATCH|DELETE> <정규식>   제안만 한다(바로 적용되지 않는다)
  nginx-approve-location <이름> <확인값 12자>                  제안자와 다른 권한자가 승인하면 적용된다
  deploy-bundle <SHA>
  restart-backend <SHA> --ack-data-loss
  rollback-bundle <SHA>
  record-pr <참조: 백업폴더명 또는 SHA> <PR번호>               감사 로그에 PR 번호를 남긴다
맨 끝에 붙일 수 있는 것(순서 무관):
  --dry-run     실제로 바꾸지 않고 할 일만 보여 준다
  --announced   팀 채널에 알림 한 줄(되돌리는 방법 포함)을 올렸다는 확인. 바꾸는 동작에는 필수
  pr=<번호>     관련 PR 번호를 감사 로그에 남긴다
EOF
}

validate_args() {
  local n="${#ARGS[@]}" ok=0 a
  for a in "${ALL_ACTIONS[@]}"; do
    [[ "${a}" == "${ACTION}" ]] && ok=1
  done
  if (( ! ok )); then
    [[ "${ACTION}" =~ ${RE_ACTION} ]] || ACTION="invalid"
    usage
    deny "unknown-action" "${RC_USAGE}" "허용 목록에 없는 동작이다"
  fi
  case "${ACTION}" in
    status | unlock)
      (( n == 0 )) || deny "bad-args" "${RC_USAGE}" "${ACTION} 은 인자를 받지 않는다"
      ;;
    lock)
      (( n == 1 || n == 2 )) || deny "bad-args" "${RC_USAGE}" "lock <사유> [해제시각]"
      [[ "${ARGS[0]}" =~ ${RE_REASON} ]] || deny "bad-reason" "${RC_USAGE}" "사유는 영어·숫자·_.,- 만, 80자 이하"
      if (( n == 2 )); then
        [[ "${ARGS[1]}" =~ ${RE_UNTIL} ]] || deny "bad-until" "${RC_USAGE}" "해제시각은 YYYY-MM-DDTHH:MM"
      fi
      ;;
    nginx-restore)
      (( n == 1 )) || deny "bad-args" "${RC_USAGE}" "nginx-restore <백업폴더명>"
      [[ "${ARGS[0]}" =~ ${RE_FOLDER} && "${ARGS[0]}" != *..* ]] ||
        deny "bad-folder" "${RC_USAGE}" "백업 폴더 이름이 잘못됐다"
      ;;
    nginx-add-location)
      (( n == 3 )) || deny "bad-args" "${RC_USAGE}" "nginx-add-location <이름> <메서드> <정규식>"
      [[ "${ARGS[0]}" =~ ${RE_LOCNAME} ]] || deny "bad-name" "${RC_USAGE}" "이름은 영어 소문자·숫자·- 만"
      [[ "${ARGS[1]}" =~ ${RE_METHOD} ]] || deny "bad-method" "${RC_USAGE}" "메서드는 POST|PUT|PATCH|DELETE"
      [[ "${ARGS[2]}" =~ ${RE_LOCREGEX} ]] || deny "bad-regex" "${RC_USAGE}" "정규식에 쓸 수 없는 글자가 있거나 ^/...$ 꼴이 아니다"
      ;;
    nginx-approve-location)
      (( n == 2 )) || deny "bad-args" "${RC_USAGE}" "nginx-approve-location <이름> <확인값 12자>"
      [[ "${ARGS[0]}" =~ ${RE_LOCNAME} ]] || deny "bad-name" "${RC_USAGE}" "이름은 영어 소문자·숫자·- 만"
      [[ "${ARGS[1]}" =~ ${RE_DIGEST} ]] || deny "bad-digest" "${RC_USAGE}" "확인값은 status 에 나온 16진수 12자"
      ;;
    record-pr)
      (( n == 2 )) || deny "bad-args" "${RC_USAGE}" "record-pr <참조> <PR번호>"
      [[ "${ARGS[0]}" =~ ${RE_FOLDER} && "${ARGS[0]}" != *..* ]] || deny "bad-ref" "${RC_USAGE}" "참조는 백업 폴더명이나 SHA"
      [[ "${ARGS[1]}" =~ ${RE_PR} ]] || deny "bad-pr" "${RC_USAGE}" "PR 번호는 숫자"
      ;;
    deploy-bundle | rollback-bundle)
      (( n == 1 )) || deny "bad-args" "${RC_USAGE}" "${ACTION} <SHA>"
      [[ "${ARGS[0]}" =~ ${RE_SHA} ]] || deny "bad-sha" "${RC_USAGE}" "SHA 는 16진수 소문자 7~40자"
      ;;
    restart-backend)
      (( n == 2 )) || deny "bad-args" "${RC_USAGE}" "restart-backend <SHA> --ack-data-loss"
      [[ "${ARGS[0]}" =~ ${RE_SHA} ]] || deny "bad-sha" "${RC_USAGE}" "SHA 는 16진수 소문자 7~40자"
      [[ "${ARGS[1]}" == "--ack-data-loss" ]] ||
        deny "no-ack" "${RC_USAGE}" "재시작하면 메모리에만 있는 상태가 지워진다. 알고 한다면 --ack-data-loss 를 붙인다"
      ;;
  esac
  A_ARGS="${ARGS[*]:-}"
  [[ -n "${A_ARGS}" ]] || A_ARGS="-"
  if [[ "${ACTION}" == "record-pr" ]]; then
    PR="${ARGS[1]}"
    NOTE="ref:${ARGS[0]}"
  fi
}

# 팀 채널에 올릴 알림 문안. 게이트는 메시지를 보내지 않는다. 문안을 만들고, 올렸는지 확인만 받는다.
notice_text() {
  local undo="-" label="${POLICY[gate.label]-server}"
  [[ "${label}" =~ ${RE_LABEL} ]] || label="server"
  case "${ACTION}" in
    deploy-bundle) undo="rollback-bundle ${ARGS[0]}" ;;
    rollback-bundle) undo="서버의 www-*.bak-before-rollback-<실행 시각>.tgz 백업(서버 책임자에게 요청)" ;;
    restart-backend) undo="되돌릴 수 없음(메모리에만 있는 상태 소실). 파일 백업은 *bak-before-${ARGS[0]:0:8}" ;;
    nginx-restore) undo="nginx-restore pre-gate-restore-<실행 시각>(실행하면 폴더명이 출력됨)" ;;
    nginx-approve-location) undo="nginx-restore pre-gate-add-${ARGS[0]}-<실행 시각>(실행하면 폴더명이 출력됨)" ;;
  esac
  printf '[%s] %s: %s %s 를 곧 실행합니다. 되돌리는 방법: %s\n' "${label}" "${KEY}" "${ACTION}" "${A_ARGS}" "${undo}"
}

require_announced() {
  local a need=0
  for a in "${NEED_ANNOUNCE[@]}"; do
    [[ "${a}" == "${ACTION}" ]] && need=1
  done
  (( need )) || return 0
  (( ANNOUNCED )) && return 0
  if [[ "${LAYER}" == "front" ]]; then
    {
      printf '바꾸기 전에 팀 채널에 아래 한 줄을 올린다. 올린 뒤 같은 명령 끝에 --announced 를 붙인다.\n  '
      notice_text
    } >&2
  fi
  (( DRY )) && return 0
  deny "not-announced" "${RC_USAGE}" "팀 채널 알림 확인(--announced)이 없다"
}

# ---------------------------------------------------------------------------
# 배포 금지 잠금
# ---------------------------------------------------------------------------
L_KEY="" L_REASON="" L_UNTIL="" L_EPOCH=""

lock_read() { # 0 읽음, 1 파일 없음, 2 깨짐
  local line
  L_KEY="" L_REASON="" L_UNTIL="" L_EPOCH=""
  [[ -e "${LOCK_FILE}" ]] || return 1
  while IFS= read -r line || [[ -n "${line}" ]]; do
    case "${line}" in
      user=*) L_KEY="${line#user=}" ;;
      reason=*) L_REASON="${line#reason=}" ;;
      until=*) L_UNTIL="${line#until=}" ;;
      until_epoch=*) L_EPOCH="${line#until_epoch=}" ;;
    esac
  done < "${LOCK_FILE}" || return 2
  [[ "${L_KEY}" =~ ${RE_KEY} && "${L_REASON}" =~ ${RE_REASON} && "${L_EPOCH}" =~ ${RE_EPOCH} && "${L_UNTIL}" =~ ${RE_UNTIL} ]] || return 2
  return 0
}

lock_active() { # 0 잠김, 1 안 잠김. 깨진 잠금 파일은 잠긴 것으로 본다.
  local rc=0 now
  lock_read || rc=$?
  (( rc == 1 )) && return 1
  (( rc == 2 )) && return 0
  if [[ "${POLICY[lock.auto_expire]-false}" == "true" ]]; then
    now="$(date +%s)"
    (( now >= L_EPOCH )) && return 1
  fi
  return 0
}

lock_describe() {
  local rc=0
  lock_read || rc=$?
  case "${rc}" in
    1) say "잠금: 없음" ;;
    2) say "잠금: 있음(파일을 해석할 수 없어 잠긴 것으로 본다)" ;;
    *)
      if lock_active; then
        say "잠금: 있음 — 건 사람 ${L_KEY}, 사유 ${L_REASON}, 해제 예정 ${L_UNTIL}"
      else
        say "잠금: 만료됨(해제 예정 ${L_UNTIL} 지남, 건 사람 ${L_KEY}) — 막지 않는다"
      fi
      ;;
  esac
}

deny_if_locked() {
  if lock_active; then
    lock_describe >&2
    deny "locked" "${RC_LOCKED}" "배포 금지 잠금 중이라 읽기(status)와 unlock 만 된다"
  fi
}

# ---------------------------------------------------------------------------
# 앞단
# ---------------------------------------------------------------------------
parse_command() {
  local cmd="$1" toks=() last
  if [[ -z "${cmd}" ]]; then
    usage
    deny "no-command" "${RC_USAGE}" "대화형 접속은 안 된다. 동작을 하나 적는다"
  fi
  if (( ${#cmd} > 512 )) || [[ "${cmd}" =~ ${RE_NONPRINT} ]]; then
    deny "bad-chars" "${RC_USAGE}" "명령이 너무 길거나 쓸 수 없는 글자(제어 문자·한글 등)가 있다"
  fi
  read -r -a toks <<< "${cmd}"
  if (( ${#toks[@]} == 0 || ${#toks[@]} > 9 )); then
    deny "bad-args" "${RC_USAGE}" "인자 개수가 맞지 않는다"
  fi
  while (( ${#toks[@]} > 1 )); do
    last="${toks[${#toks[@]}-1]}"
    case "${last}" in
      --dry-run) DRY=1 ;;
      --announced) ANNOUNCED=1 ;;
      pr=*)
        PR="${last#pr=}"
        if [[ ! "${PR}" =~ ${RE_PR} ]]; then
          PR="-"
          deny "bad-pr" "${RC_USAGE}" "pr=<번호> 는 숫자여야 한다"
        fi
        ;;
      *) break ;;
    esac
    unset 'toks[${#toks[@]}-1]'
  done
  (( ${#toks[@]} > 0 )) || deny "bad-args" "${RC_USAGE}" "동작이 없다"
  ACTION="${toks[0]}"
  ARGS=("${toks[@]:1}")
}

front_main() {
  local me
  LAYER="front"
  if (( $# != 1 )) || [[ ! "$1" =~ ${RE_KEY} ]]; then
    printf '설정 오류: authorized_keys 의 command= 에 계정명이 없거나 잘못됐다\n' >&2
    exit "${RC_CONFIG}"
  fi
  # 신원은 실제 로그인 계정이다. command= 에 적힌 이름은 그것과 같은지 확인만 한다.
  me="$(id -un)" || me=""
  if [[ ! "${me}" =~ ${RE_KEY} || "${me}" == "root" ]]; then
    printf '설정 오류: 로그인 계정을 알 수 없거나 게이트를 쓸 수 없는 계정이다\n' >&2
    exit "${RC_CONFIG}"
  fi
  KEY="${me}"
  if [[ "$1" != "${me}" ]]; then
    printf '설정 오류: authorized_keys 의 계정명이 로그인 계정과 다르다. 실행하지 않는다\n' >&2
    audit "denied:account-mismatch" || true
    exit "${RC_CONFIG}"
  fi
  [[ "${DRY_RUN:-0}" == "1" ]] && DRY=1
  load_policy
  parse_command "${SSH_ORIGINAL_COMMAND:-}"
  validate_args
  check_permission
  case "${ACTION}" in
    status | unlock | record-pr) ;;
    *) deny_if_locked ;;
  esac
  require_announced
  # 요청을 한 줄에 하나씩 뒷단 표준 입력으로 넘긴다(sudoers 는 인자 없는 호출만 허용).
  {
    printf '%s\n' "${DRY}" "${ANNOUNCED}" "${PR}" "${ACTION}"
    if (( ${#ARGS[@]} > 0 )); then
      printf '%s\n' "${ARGS[@]}"
    fi
  } | "${SUDO_CMD[@]}"
}

# ---------------------------------------------------------------------------
# 뒷단 공통 도구
# ---------------------------------------------------------------------------
path_ok() { # 값이 쓸 수 있는 경로 모양인가
  [[ "$1" =~ ${RE_PATH} && "$1" != *..* ]]
}

path_set() { # 정책 [paths] 에 쓸 수 있는 경로가 들어 있는가(없으면 그 기능을 건너뛴다)
  path_ok "${POLICY[paths.$1]-}"
}

require_paths() { # 정책 [paths] 의 값을 P_<이름> 변수로. 없거나 이상하면 실패.
  local name v
  for name in "$@"; do
    v="${POLICY[paths.${name}]-}"
    if ! path_ok "${v}"; then
      step_fail "config-path" "${RC_CONFIG}" "정책 [paths] ${name} 이 없거나 경로가 아니다(설치 때 채워야 한다)"
    fi
    printf -v "P_${name}" '%s' "${v}"
  done
}

# 배포 스크립트와 git 은 root 가 아니라 정책의 배포 계정으로 돌린다(runuser).
DEPLOY_USER=""
load_deploy_user() {
  DEPLOY_USER="${POLICY[deploy.user]-}"
  [[ "${DEPLOY_USER}" =~ ${RE_USER} && "${DEPLOY_USER}" != "root" ]] ||
    step_fail "config" "${RC_CONFIG}" "정책 [deploy] user 가 없다"
}

as_deploy() { runuser -u "${DEPLOY_USER}" -- "$@"; }

http_code() { # URL → REPLY (3자리)
  REPLY="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null)" || REPLY="000"
  [[ "${REPLY}" =~ ^[0-9]{3}$ ]] || REPLY="000"
}

# health 주소 목록은 정책 [health] urls 에서 읽는다.
H_OK=0
H_TOTAL=0
H_BAD=""
health_count() {
  local u
  H_OK=0
  H_BAD=""
  policy_items health.urls "${RE_LOCALURL}"
  H_TOTAL="${#ITEMS[@]}"
  (( H_TOTAL > 0 )) || step_fail "config" "${RC_CONFIG}" "정책 [health] urls 가 비어 있다"
  for u in "${ITEMS[@]}"; do
    http_code "${u}"
    if [[ "${REPLY}" == "200" ]]; then
      H_OK=$((H_OK + 1))
    else
      H_BAD+=" ${u#http://}(${REPLY})"
    fi
  done
}

stamp_now() { REPLY="$(date '+%Y%m%d-%H%M%S')"; }

service_unit() { policy_str restart.unit "${RE_UNIT}"; }

journal_count() { # 서비스 최근 5분 로그의 오류 줄 수(내용은 출력하지 않는다) → REPLY
  local unit="$1" n
  n="$(journalctl -u "${unit}" --since '5 minutes ago' --no-pager 2>/dev/null | grep -cE 'ERROR|Traceback')" || true
  [[ "${n}" =~ ${RE_UINT} ]] || n="알 수 없음"
  REPLY="${n}"
}

# SHA 가 서버 레포에 있고 origin 의 배포 기준 브랜치에 있는지 확인. 전체 SHA 를 FULL_SHA 에 둔다.
FULL_SHA=""
resolve_sha() {
  local sha="$1" out branch found=0 any=0
  require_paths repo
  load_deploy_user
  run as_deploy git -C "${P_repo}" fetch --quiet origin ||
    step_fail "fetch-failed" "${RC_FAIL}" "origin 에서 받아오지 못했다"
  out="$(as_deploy git -C "${P_repo}" rev-parse --verify --quiet "${sha}^{commit}")" ||
    step_fail "sha-unknown" "${RC_USAGE}" "그 SHA 의 커밋이 서버 레포에 없다"
  [[ "${out}" =~ ${RE_SHA_FULL} ]] || step_fail "sha-unknown" "${RC_USAGE}" "SHA 를 풀 수 없다"
  FULL_SHA="${out}"
  out="$(as_deploy git -C "${P_repo}" branch -r --contains "${FULL_SHA}" --list 'origin/*')" || out=""
  [[ -n "${out}" ]] || step_fail "sha-not-on-origin" "${RC_USAGE}" "그 커밋은 origin 의 어느 브랜치에도 없다"
  # 배포 기준 브랜치: 정책 [deploy] allowed_branches 중 하나에 들어간 커밋만 받는다.
  while IFS= read -r branch; do
    [[ -n "${branch}" ]] || continue
    any=1
    [[ "${branch}" =~ ${RE_BRANCH} && "${branch}" != *..* ]] ||
      step_fail "config" "${RC_CONFIG}" "정책 [deploy] allowed_branches 형식이 잘못됐다"
    branch="origin/${branch#origin/}"   # "dev" 라고 적으면 origin/dev 를 본다
    if as_deploy git -C "${P_repo}" merge-base --is-ancestor "${FULL_SHA}" "${branch}"; then
      found=1
      break
    fi
  done <<< "${POLICY[deploy.allowed_branches]-}"
  (( any )) || step_fail "config" "${RC_CONFIG}" "정책 [deploy] allowed_branches 가 비어 있다(배포 기준 브랜치를 정해야 한다)"
  (( found )) || step_fail "sha-not-on-branch" "${RC_USAGE}" "그 커밋은 배포 기준 브랜치에 들어 있지 않다"
}

# 번들에 비밀값이 들어갔는지 본다. 값은 절대 출력하지 않고 변수 이름만 알린다.
# 검사할 env 파일 목록은 정책 [deploy] secret_env_files.
scan_bundle_tokens() { # 웹루트 → 0 깨끗, 1 발견, 2 검사 불가
  local root="$1" f line name val found=0 checked=0
  local re_env='^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$'
  local -a files=()
  policy_items deploy.secret_env_files "${RE_PATH}"
  files=("${ITEMS[@]}")
  for f in "${files[@]}"; do
    [[ "${f}" != *..* && -r "${f}" ]] || continue
    checked=1
    while IFS= read -r line || [[ -n "${line}" ]]; do
      line="${line%$'\r'}"
      [[ "${line}" =~ ${re_env} ]] || continue
      name="${BASH_REMATCH[2]}"
      val="${BASH_REMATCH[3]}"
      val="${val#[\"\']}"
      val="${val%[\"\']}"
      (( ${#val} >= 12 )) || continue
      # 번들에 있어야 하는 표시 문자열과, 배포 스크립트가 일부러 번들에 넣는 값은 예외다.
      if policy_list_has "deploy.required_strings" "${val}"; then
        continue
      fi
      if policy_list_has "deploy.bundle_allowed_env_names" "${name}"; then
        continue
      fi
      # 값이 프로세스 인자에 보이지 않게 -f 로 넘긴다.
      if grep -rqF -f <(printf '%s\n' "${val}") -- "${root}" 2>/dev/null; then
        say "  비밀값 발견: 변수 ${name} 의 값이 번들에 있다(값은 출력하지 않음)"
        found=1
      fi
    done < "${f}"
  done
  (( checked )) || return 2
  return "${found}"
}

check_bundle() { # 웹루트 → 0 통과
  local root="$1" ph bad=0 rc=0
  policy_items deploy.required_strings "${RE_MARKERSTR}"
  for ph in "${ITEMS[@]}"; do
    if grep -rqF -- "${ph}" "${root}" 2>/dev/null; then
      say "  표시 문자열 ${ph}: 있음"
    else
      say "  표시 문자열 ${ph}: 없음"
      bad=1
    fi
  done
  scan_bundle_tokens "${root}" || rc=$?
  case "${rc}" in
    0) say "  비밀값: 0" ;;
    1) bad=1 ;;
    *) say "  비밀값: 검사 불가(정책의 env 파일을 읽을 수 없다)"; bad=1 ;;
  esac
  return "${bad}"
}

# 번들 폴더 목록(정책 web_root_a, 있으면 web_root_b)을 WEB_ROOTS 에.
WEB_ROOTS=()
load_web_roots() {
  require_paths web_root_parent web_root_a
  WEB_ROOTS=("${P_web_root_a}")
  if path_set web_root_b; then
    WEB_ROOTS+=("${POLICY[paths.web_root_b]}")
  fi
}

web_root_ok() { # 번들 교체는 정책 web_root_parent 바로 아래 폴더에서만
  local root="$1"
  [[ "${root%/*}" == "${P_web_root_parent}" && -n "${root##*/}" ]] || return 1
  [[ -d "${root}" && ! -L "${root}" ]]
}

sum12() { # 파일 → REPLY (sha256 앞 12자)
  local sum
  sum="$(sha256sum < "$1")"
  REPLY="${sum:0:12}"
}

copy_compare() { # 이름표 정책경로키: nginx 설정과 사본의 체크섬 비교(알림만)
  local label="$1" key="$2" copy a b
  path_set "${key}" || { say "${label}: 정책에 경로 없음(비교 생략)"; return 0; }
  copy="${POLICY[paths.${key}]}"
  if [[ ! -r "${copy}" || ! -r "${P_nginx_conf}" ]]; then
    say "${label}: 사본이 아직 없거나 읽을 수 없다"
    return 0
  fi
  a="$(sha256sum < "${P_nginx_conf}")"
  b="$(sha256sum < "${copy}")"
  if [[ "${a}" == "${b}" ]]; then
    say "${label}: 실제 nginx 설정과 일치"
  else
    say "${label}: 어긋남 — 사본을 갱신하지 않고 실제 파일이 바뀌었거나 사본이 옛것이다(사람 확인 필요)"
  fi
}

ops_copy_sync() { # 변경이 성공한 뒤 운영 사본을 갱신하고 체크섬을 알린다
  local copy
  if path_set ops_copy_file; then
    copy="${POLICY[paths.ops_copy_file]}"
    run cp -- "${P_nginx_conf}" "${copy}"
    run chown --reference="${copy%/*}" -- "${copy}"
    say "운영 사본을 갱신했다"
  fi
  sum12 "${P_nginx_conf}"
  say "지금 nginx 설정의 sha256 앞 12자: ${REPLY}"
}

nginx_backup_current() { # 폴더이름 → 현재 설정을 백업하고 체크섬을 남긴다
  local bdir="${P_nginx_backup_dir}/$1" base="${P_nginx_conf##*/}"
  [[ ! -e "${bdir}" ]] || step_fail "backup-exists" "${RC_FAIL}" "백업 폴더가 이미 있다"
  (umask 022 && run mkdir -- "${bdir}")
  run cp -a -- "${P_nginx_conf}" "${bdir}/${base}"
  if (( ! DRY )); then
    (cd "${bdir}" && sha256sum "${base}" > "${base}.sha256")
  fi
}

nginx_test_reload_or_revert() { # 되돌릴백업폴더 → nginx -t 실패면 되돌리고 실패 처리
  local bdir="${P_nginx_backup_dir}/$1" base="${P_nginx_conf##*/}"
  if ! run nginx -t; then
    run cp -a -- "${bdir}/${base}" "${P_nginx_conf}"
    run nginx -t || say "경고: 되돌린 뒤에도 nginx -t 가 실패한다. 사람이 바로 봐야 한다"
    step_fail "nginx-test-failed" "${RC_FAIL}" "nginx -t 실패. 설정을 바꾸기 전으로 되돌렸고 reload 하지 않았다"
  fi
  run systemctl reload nginx
}

# ---------------------------------------------------------------------------
# 동작들 (뒷단, root)
# ---------------------------------------------------------------------------
do_status() {
  local u root head
  say "ax-gate ${GATE_VERSION} — status (읽기 전용)"
  lock_describe
  health_count
  say "health: ${H_OK}/${H_TOTAL}${H_BAD:+ — 실패:${H_BAD}}"
  policy_items status.urls "${RE_LOCALURL}"
  for u in "${ITEMS[@]}"; do
    http_code "${u}"
    say "확인 ${u#http://}: ${REPLY}"
  done
  if [[ -n "${POLICY[restart.unit]+x}" ]]; then
    service_unit
    if systemctl is-active --quiet "${REPLY}"; then
      say "서비스: active"
    else
      say "서비스: active 아님"
    fi
    journal_count "${REPLY}"
    say "최근 5분 서비스 로그의 오류 줄 수: ${REPLY} (내용은 출력하지 않음)"
  fi
  if path_set repo && [[ -n "${POLICY[deploy.user]+x}" ]]; then
    require_paths repo
    load_deploy_user
    head="$(as_deploy git -C "${P_repo}" rev-parse HEAD 2>/dev/null)" || head="알 수 없음"
    [[ "${head}" =~ ${RE_SHA_FULL} ]] || head="알 수 없음"
    say "서버 레포 HEAD: ${head}"
  fi
  if path_set web_root_parent && path_set web_root_a; then
    load_web_roots
    for root in "${WEB_ROOTS[@]}"; do
      say "번들 ${root##*/}:"
      check_bundle "${root}" || say "  → 번들 점검 실패"
    done
  fi
  if path_set nginx_conf; then
    require_paths nginx_conf
    copy_compare "운영 사본" ops_copy_file
    copy_compare "레포 사본" repo_nginx_copy
  fi
  pending_list
}

do_lock() {
  local reason="${ARGS[0]}" until="${ARGS[1]-}" now epoch def max tmp
  if lock_active; then
    lock_describe >&2
    step_fail "already-locked" "${RC_LOCKED}" "이미 잠겨 있다. 바꾸려면 unlock 뒤 다시 건다"
  fi
  policy_uint lock.default_hours 4
  def="${REPLY}"
  policy_uint lock.max_hours 24
  max="${REPLY}"
  now="$(date +%s)"
  if [[ -n "${until}" ]]; then
    epoch="$(date -d "${until/T/ }" +%s 2>/dev/null)" || step_fail "bad-until" "${RC_USAGE}" "해제시각을 날짜로 읽을 수 없다"
  else
    epoch=$((now + def * 3600))
    until="$(date -d "@${epoch}" '+%Y-%m-%dT%H:%M')"
  fi
  (( epoch > now )) || step_fail "bad-until" "${RC_USAGE}" "해제시각이 이미 지났다"
  (( epoch <= now + max * 3600 )) || step_fail "bad-until" "${RC_USAGE}" "해제시각이 최대 ${max}시간을 넘는다"
  if (( DRY )); then
    say "DRY_RUN: 잠금 파일을 쓴다 — 사유 ${reason}, 해제 예정 ${until}"
    return 0
  fi
  tmp="${LOCK_FILE}.tmp.$$"
  printf 'user=%s\nreason=%s\nuntil=%s\nuntil_epoch=%s\ncreated=%s\n' \
    "${KEY}" "${reason}" "${until}" "${epoch}" "$(date '+%Y-%m-%dT%H:%M:%S%z')" > "${tmp}"
  chmod 644 -- "${tmp}"
  mv -f -- "${tmp}" "${LOCK_FILE}"
  say "잠갔다 — 사유 ${reason}, 해제 예정 ${until}"
}

do_unlock() {
  if [[ ! -e "${LOCK_FILE}" ]]; then
    say "잠금이 없다(할 일 없음)"
    return 0
  fi
  lock_describe
  run rm -f -- "${LOCK_FILE}"
  say "잠금을 풀었다"
}

pr_reminder() { # nginx 를 바꾼 뒤: 변경을 PR 로 남기라고 알린다
  say "다음 할 일: 바뀐 nginx 설정을 팀이 정한 곳에 PR 로 남기고,"
  say "  PR 번호를 기록한다: record-pr $1 <PR번호>"
}

do_nginx_restore() {
  local folder="${ARGS[0]}" bdir src stamp base
  require_paths nginx_conf nginx_backup_dir
  base="${P_nginx_conf##*/}"
  bdir="${P_nginx_backup_dir}/${folder}"
  src="${bdir}/${base}"
  if [[ ! -d "${bdir}" || -L "${bdir}" || ! -f "${src}" || -L "${src}" ]]; then
    step_fail "backup-not-found" "${RC_USAGE}" "그 백업 폴더에 설정 파일이 없다"
  fi
  if [[ -f "${bdir}/${base}.sha256" ]]; then
    (cd "${bdir}" && sha256sum --status -c "${base}.sha256") ||
      step_fail "backup-checksum" "${RC_FAIL}" "백업의 체크섬이 맞지 않는다(백업이 바뀌었다)"
    say "백업 체크섬: 일치"
  else
    say "백업 체크섬: 파일 없음(확인 못 하고 진행)"
  fi
  copy_compare "운영 사본" ops_copy_file
  stamp_now
  stamp="${REPLY}"
  nginx_backup_current "pre-gate-restore-${stamp}"
  say "복원 전 상태 백업: pre-gate-restore-${stamp}"
  run cp -a -- "${src}" "${P_nginx_conf}"
  nginx_test_reload_or_revert "pre-gate-restore-${stamp}"
  ops_copy_sync
  say "nginx 설정을 ${folder} 로 복원하고 reload 했다. 라우팅(허용 메서드와 그 밖의 응답 코드)을 직접 확인한다"
  say "이 복원을 되돌리려면: nginx-restore pre-gate-restore-${stamp}"
  pr_reminder "pre-gate-restore-${stamp}"
}

# 정책에서 한 계정의 location 규칙(접두·메서드·업스트림·블록 줄)을 RULES 배열로 모은다.
RULES=()
collect_rules() {
  local key="$1" item k n=0
  RULES=()
  while IFS= read -r item; do
    if [[ -n "${item}" ]]; then
      RULES+=(--allowed-prefix "${item}")
      n=$((n + 1))
    fi
  done <<< "${POLICY[location_prefixes.${key}]-}"
  (( n > 0 )) || return 1
  while IFS= read -r item; do
    if [[ -n "${item}" ]]; then
      RULES+=(--allowed-method "${item}")
    fi
  done <<< "${POLICY[location.allowed_methods]-}"
  while IFS= read -r item; do
    if [[ -n "${item}" ]]; then
      RULES+=(--block-line "${item}")
    fi
  done <<< "${POLICY[location.block_lines]-}"
  for k in "${!POLICY[@]}"; do
    if [[ "${k}" == location_upstreams.* ]]; then
      RULES+=(--upstream "${k#location_upstreams.}=${POLICY[${k}]}")
    fi
  done
}

proposal_digest() { # 이름 메서드 정규식 제안자 → REPLY (sha256 앞 12자)
  local sum
  sum="$(printf '%s\n' "$1" "$2" "$3" "$4" | sha256sum)"
  REPLY="${sum:0:12}"
}

# 제안 파일을 읽어 다시 검증한다. Q_* 에 둔다. 0 읽음, 1 없음, 2 깨짐
Q_NAME="" Q_METHOD="" Q_REGEX="" Q_PROPOSER="" Q_EPOCH=""
proposal_read() {
  local f="$1" line
  Q_NAME="" Q_METHOD="" Q_REGEX="" Q_PROPOSER="" Q_EPOCH=""
  [[ -f "${f}" && ! -L "${f}" ]] || return 1
  while IFS= read -r line || [[ -n "${line}" ]]; do
    case "${line}" in
      name=*) Q_NAME="${line#name=}" ;;
      method=*) Q_METHOD="${line#method=}" ;;
      regex=*) Q_REGEX="${line#regex=}" ;;
      proposer=*) Q_PROPOSER="${line#proposer=}" ;;
      created_epoch=*) Q_EPOCH="${line#created_epoch=}" ;;
    esac
  done < "${f}" || return 2
  [[ "${Q_NAME}" =~ ${RE_LOCNAME} && "${Q_METHOD}" =~ ${RE_METHOD} && "${Q_REGEX}" =~ ${RE_LOCREGEX} &&
    "${Q_PROPOSER}" =~ ${RE_KEY} && "${Q_EPOCH}" =~ ${RE_EPOCH} ]] || return 2
  return 0
}

proposal_expired() {
  local ttl now
  policy_uint location.proposal_ttl_hours 24
  ttl="${REPLY}"
  now="$(date +%s)"
  (( now > Q_EPOCH + ttl * 3600 ))
}

pending_list() {
  local f any=0
  [[ -d "${PENDING_DIR}" ]] || return 0
  for f in "${PENDING_DIR}"/*.req; do
    [[ -f "${f}" ]] || continue
    proposal_read "${f}" || continue
    any=1
    if proposal_expired; then
      say "location 제안(만료): ${Q_NAME} — 제안자 ${Q_PROPOSER}"
    else
      proposal_digest "${Q_NAME}" "${Q_METHOD}" "${Q_REGEX}" "${Q_PROPOSER}"
      say "location 제안(승인 대기): 이름 ${Q_NAME} / 제안자 ${Q_PROPOSER} / ${Q_METHOD} ${Q_REGEX} / 확인값 ${REPLY}"
    fi
  done
  (( any )) || say "location 제안: 없음"
}

# 제안: 검사만 하고 승인 대기 파일을 만든다. nginx 는 건드리지 않는다.
do_nginx_add_location() {
  local name="${ARGS[0]}" method="${ARGS[1]}" regex="${ARGS[2]}" f tmp digest
  require_paths nginx_conf
  collect_rules "${KEY}" || step_fail "no-prefix" "${RC_PERM}" "계정 ${KEY} 에게 허용된 경로 접두가 정책에 없다"
  python3 "${CHECKER}" check --method "${method}" --regex "${regex}" "${RULES[@]}" ||
    step_fail "location-rejected" "${RC_USAGE}" "location 이 검증기를 통과하지 못했다"
  proposal_digest "${name}" "${method}" "${regex}" "${KEY}"
  digest="${REPLY}"
  NOTE="digest:${digest}"
  if (( DRY )); then
    say "DRY_RUN: 제안을 저장한다 — ${name} / ${method} ${regex} / 확인값 ${digest}"
    return 0
  fi
  f="${PENDING_DIR}/${name}.req"
  tmp="${PENDING_DIR}/.${name}.preview"
  # 지금 설정에 넣을 수 있는지(삽입 표시 줄·중복) 미리 본다. 결과 파일은 버린다.
  if ! python3 "${CHECKER}" apply --conf "${P_nginx_conf}" --out "${tmp}" --name "${name}" --user "${KEY}" \
    --stamp "00000000-000000" --method "${method}" --regex "${regex}" "${RULES[@]}" > /dev/null; then
    rm -f -- "${tmp}"
    step_fail "location-rejected" "${RC_USAGE}" "지금 설정 파일에 넣을 수 없다(삽입 표시 줄·중복 확인)"
  fi
  rm -f -- "${tmp}"
  (
    umask 022
    printf 'name=%s\nmethod=%s\nregex=%s\nproposer=%s\ncreated_epoch=%s\ncreated=%s\ndigest=%s\n' \
      "${name}" "${method}" "${regex}" "${KEY}" "$(date +%s)" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${digest}" > "${f}.tmp"
  )
  mv -f -- "${f}.tmp" "${f}"
  audit "proposed"
  say "제안을 저장했다(아직 적용되지 않았다)."
  say "  이름 ${name} / ${method} ${regex}"
  say "  확인값 ${digest}"
  say "다른 권한자 한 명이 status 로 내용을 확인하고 팀 채널에 알린 뒤 실행해야 적용된다:"
  say "  nginx-approve-location ${name} ${digest} --announced"
}

# 승인: 제안자와 다른 권한자가 확인값을 대고 실행하면 그때 적용한다.
do_nginx_approve_location() {
  local name="${ARGS[0]}" digest="${ARGS[1]}" f rc=0 bname new stamp SELF_APPROVED=0
  require_paths nginx_conf nginx_backup_dir
  f="${PENDING_DIR}/${name}.req"
  proposal_read "${f}" || rc=$?
  (( rc != 1 )) || step_fail "no-proposal" "${RC_USAGE}" "그 이름의 제안이 없다"
  (( rc == 0 )) || step_fail "proposal-corrupt" "${RC_FAIL}" "제안 파일을 해석할 수 없다"
  [[ "${Q_NAME}" == "${name}" ]] || step_fail "proposal-corrupt" "${RC_FAIL}" "제안 파일의 이름이 다르다"
  if proposal_expired; then
    step_fail "proposal-expired" "${RC_USAGE}" "제안이 만료됐다. 다시 제안해야 한다"
  fi
  proposal_digest "${Q_NAME}" "${Q_METHOD}" "${Q_REGEX}" "${Q_PROPOSER}"
  [[ "${REPLY}" == "${digest}" ]] || step_fail "digest-mismatch" "${RC_USAGE}" "확인값이 제안 내용과 다르다(status 로 다시 확인)"
  NOTE="proposer:${Q_PROPOSER},digest:${digest},self-approve:no"
  if [[ "${Q_PROPOSER}" == "${KEY}" ]]; then
    # 기본은 금지. 정책 [self_approve] accounts 에 든 계정만 통과한다(그 밖의 검증은 전부 그대로).
    policy_list_has self_approve.accounts "${KEY}" ||
      step_fail "self-approval" "${RC_PERM}" "제안한 사람은 자기 제안을 승인할 수 없다(둘 이상 확인)"
    SELF_APPROVED=1
    NOTE="proposer:${Q_PROPOSER},digest:${digest},self-approve:yes"
    say "셀프 승인(정책 허용 계정): ${KEY} — 제안자와 승인자가 같다. 다른 검증은 모두 그대로 적용한다"
    if command -v logger >/dev/null 2>&1; then
      logger -t ax-gate -- "셀프 승인(정책 허용 계정): user=${KEY} name=${name}" || true
    fi
  fi
  policy_list_has "permissions.${Q_PROPOSER}" "nginx-add-location" ||
    step_fail "proposer-not-permitted" "${RC_PERM}" "제안자에게 지금은 제안 권한이 없다"
  # 승인자와 제안자 둘 다 그 경로 접두 권한이 있어야 한다.
  collect_rules "${KEY}" || step_fail "no-prefix" "${RC_PERM}" "계정 ${KEY} 에게 허용된 경로 접두가 정책에 없다"
  python3 "${CHECKER}" check --method "${Q_METHOD}" --regex "${Q_REGEX}" "${RULES[@]}" ||
    step_fail "location-rejected" "${RC_USAGE}" "승인자의 허용 접두 밖이거나 검증기를 통과하지 못했다"
  collect_rules "${Q_PROPOSER}" || step_fail "no-prefix" "${RC_PERM}" "제안자에게 허용된 경로 접두가 정책에 없다"
  python3 "${CHECKER}" check --method "${Q_METHOD}" --regex "${Q_REGEX}" "${RULES[@]}" ||
    step_fail "location-rejected" "${RC_USAGE}" "location 이 검증기를 통과하지 못했다"
  copy_compare "운영 사본" ops_copy_file
  stamp_now
  stamp="${REPLY}"
  bname="pre-gate-add-${name}-${stamp}"
  nginx_backup_current "${bname}"
  new="${P_nginx_backup_dir}/${bname}/${P_nginx_conf##*/}.new"
  run python3 "${CHECKER}" apply --conf "${P_nginx_conf}" --out "${new}" \
    --name "${name}" --user "${Q_PROPOSER}" --stamp "${stamp}" \
    --method "${Q_METHOD}" --regex "${Q_REGEX}" "${RULES[@]}" ||
    step_fail "location-rejected" "${RC_USAGE}" "설정 파일에 넣지 못했다(삽입 표시 줄·중복 확인)"
  run cp -- "${new}" "${P_nginx_conf}"
  nginx_test_reload_or_revert "${bname}"
  if (( ! DRY )); then
    # 승인 기록: 제안 파일에 승인자·시각·백업 폴더를 덧붙여 보관한다.
    printf 'approver=%s\napproved=%s\nbackup=%s\n' "${KEY}" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${bname}" >> "${f}"
    mv -f -- "${f}" "${APPROVED_DIR}/${name}-${stamp}.rec"
    audit "applied:${bname}"
  fi
  ops_copy_sync
  say "location ${name} 을 추가하고 reload 했다(제안 ${Q_PROPOSER}, 승인 ${KEY}). 되돌리려면: nginx-restore ${bname}"
  (( SELF_APPROVED )) && say "이 적용은 셀프 승인(정책 허용 계정)이었다. 팀 채널 알림에 그 사실을 적는다"
  say "라우팅(허용 메서드와 그 밖의 응답 코드)을 직접 확인한다"
  pr_reminder "${bname}"
}

do_record_pr() {
  say "감사 로그에 남겼다: 참조 ${ARGS[0]} ↔ PR #${PR}"
}

# 배포 스크립트에 넘기는 환경변수는 전부 정책 값으로 고정한다(요청하는 사람이 못 바꾼다):
#   <ref_env>=<SHA>, 정책 [deploy] env 의 항목들, 두 번째 대상에는 env_b 의 항목들.
DEPLOY_ENV=()
deploy_one() { # 이름표 웹루트 [추가 환경변수...]
  local label="$1" root="$2" log rc=0
  shift 2
  stamp_now
  log="${P_backup_dir}/deploy-${label}-${FULL_SHA:0:8}-${REPLY}.log"
  say "배포 ${label}: 배포 스크립트 실행(출력은 서버의 root 전용 기록 파일로만 간다)"
  if (( DRY )); then
    run as_deploy env "${DEPLOY_ENV[@]}" "$@" "${P_deploy_script}"
    return 0
  fi
  as_deploy env "${DEPLOY_ENV[@]}" "$@" "${P_deploy_script}" > "${log}" 2>&1 || rc=$?
  (( rc == 0 )) || step_fail "deploy-failed" "${RC_FAIL}" "${label} 배포 스크립트가 끝 코드 ${rc} 로 끝났다(기록: ${log##*/})"
  say "번들 ${label} 점검:"
  check_bundle "${root}" ||
    step_fail "bundle-check" "${RC_FAIL}" "${label} 번들 점검 실패. 바로 rollback-bundle ${FULL_SHA:0:8} 을 검토한다"
}

do_deploy_bundle() {
  local out root base tgz head prevfile check_url="" i=0
  local -a env_b=()
  require_paths repo deploy_script backup_dir
  load_web_roots
  if [[ -n "${POLICY[deploy.check_url]+x}" ]]; then
    policy_str deploy.check_url "${RE_LOCALURL}"
    check_url="${REPLY}"
  fi
  policy_str deploy.ref_env "${RE_ENVNAME}"
  DEPLOY_ENV=("${REPLY}=PENDING")
  policy_items deploy.env "${RE_ENVPAIR}"
  DEPLOY_ENV+=("${ITEMS[@]}")
  policy_items deploy.env_b "${RE_ENVPAIR}"
  env_b=("${ITEMS[@]}")
  resolve_sha "${ARGS[0]}"
  DEPLOY_ENV[0]="${DEPLOY_ENV[0]%%=*}=${FULL_SHA}"
  out="$(as_deploy git -C "${P_repo}" status --porcelain)" || step_fail "git-status" "${RC_FAIL}" "레포 상태를 볼 수 없다"
  [[ -z "${out}" ]] || step_fail "repo-dirty" "${RC_FAIL}" "서버 레포에 로컬 변경이 있다"
  # 배포 스크립트가 "앞으로 감기(ff) 병합"만 하는 경우, 지금 HEAD 보다 뒤의 커밋을 주면 아무 일도
  # 안 하고 지금 코드를 다시 빌드한다. 그래서 앞선 커밋일 때만 받는다. 코드 롤백에는 쓸 수 없다.
  head="$(as_deploy git -C "${P_repo}" rev-parse HEAD)" || step_fail "git-head" "${RC_FAIL}" "레포 HEAD 를 볼 수 없다"
  [[ "${head}" =~ ${RE_SHA_FULL} ]] || step_fail "git-head" "${RC_FAIL}" "레포 HEAD 를 볼 수 없다"
  as_deploy git -C "${P_repo}" merge-base --is-ancestor "${head}" "${FULL_SHA}" ||
    step_fail "not-fast-forward" "${RC_FAIL}" "그 커밋은 지금 서버 코드(${head:0:8})보다 앞선 커밋이 아니다. 이전 커밋으로 되돌리는 데는 못 쓴다(rollback-bundle 을 본다)"
  # 직전 배포 해시를 남긴다(코드 롤백 때 필요). 백업 이름의 해시는 배포 "대상" 커밋이다.
  prevfile="${P_backup_dir}/deploy-prev-head.bak-before-${FULL_SHA:0:8}"
  say "직전 배포 해시: ${head}"
  if (( ! DRY )); then
    if [[ ! -e "${prevfile}" ]]; then
      printf '%s\n' "${head}" > "${prevfile}"
    fi
    audit "prev-head:${head}"
  fi
  for root in "${WEB_ROOTS[@]}"; do
    web_root_ok "${root}" || step_fail "web-root" "${RC_CONFIG}" "번들 폴더가 정책의 web_root_parent 바로 아래가 아니거나 없다"
    base="${root##*/}"
    tgz="${P_backup_dir}/www-${base}.bak-before-${FULL_SHA:0:8}.tgz"
    if [[ -e "${tgz}" ]]; then
      say "번들 백업이 이미 있다(처음 것을 유지): ${tgz##*/}"
    else
      run tar --force-local -czf "${tgz}" -C "${root%/*}" "${base}"
      run chmod 600 -- "${tgz}"
    fi
  done
  for root in "${WEB_ROOTS[@]}"; do
    if (( i == 0 )); then
      deploy_one "${root##*/}" "${root}"
    else
      deploy_one "${root##*/}" "${root}" "${env_b[@]}"
    fi
    i=$((i + 1))
  done
  if (( ! DRY )); then
    if [[ -n "${check_url}" ]]; then
      http_code "${check_url}"
      [[ "${REPLY}" == "200" ]] || step_fail "http-check" "${RC_FAIL}" "배포 뒤 확인 주소가 ${REPLY} 다"
    fi
    head="$(as_deploy git -C "${P_repo}" rev-parse HEAD)" || head=""
    [[ "${head}" == "${FULL_SHA}" ]] ||
      step_fail "head-after-deploy" "${RC_FAIL}" "배포 뒤 서버 레포 HEAD 가 그 SHA 가 아니다. 번들이 다른 코드로 빌드됐을 수 있다"
  fi
  say "번들 배포 끝: ${FULL_SHA}. 백엔드는 재시작하지 않았다"
}

do_rollback_bundle() {
  local root base tgz parent tmp old stamp prevfile head=""
  require_paths repo backup_dir
  load_web_roots
  resolve_sha "${ARGS[0]}"
  for root in "${WEB_ROOTS[@]}"; do
    web_root_ok "${root}" || step_fail "web-root" "${RC_CONFIG}" "번들 폴더가 정책의 web_root_parent 바로 아래가 아니거나 없다"
    tgz="${P_backup_dir}/www-${root##*/}.bak-before-${FULL_SHA:0:8}.tgz"
    [[ -f "${tgz}" && ! -L "${tgz}" ]] || step_fail "backup-not-found" "${RC_FAIL}" "그 SHA 배포 직전의 번들 백업이 없다"
  done
  stamp_now
  stamp="${REPLY}"
  for root in "${WEB_ROOTS[@]}"; do
    base="${root##*/}"
    parent="${root%/*}"
    tgz="${P_backup_dir}/www-${base}.bak-before-${FULL_SHA:0:8}.tgz"
    tmp="${parent}/.gate-restore-${base}-${stamp}"
    old="${parent}/.gate-old-${base}-${stamp}"
    run tar --force-local -czf "${P_backup_dir}/www-${base}.bak-before-rollback-${stamp}.tgz" -C "${parent}" "${base}"
    run chmod 600 -- "${P_backup_dir}/www-${base}.bak-before-rollback-${stamp}.tgz"
    run mkdir -- "${tmp}"
    run tar --force-local -xzf "${tgz}" -C "${tmp}"
    if (( ! DRY )) && [[ ! -d "${tmp}/${base}" ]]; then
      step_fail "backup-shape" "${RC_FAIL}" "백업 안에 ${base} 폴더가 없다"
    fi
    run mv -- "${root}" "${old}"
    run mv -- "${tmp}/${base}" "${root}"
    run rm -rf -- "${old}" "${tmp}"
    say "번들 ${base} 를 ${FULL_SHA:0:8} 배포 직전으로 되돌렸다"
  done
  say "주의: 서버 레포 체크아웃은 그대로다(번들만 되돌렸다). 코드 롤백은 게이트에 없다"
  prevfile="${P_backup_dir}/deploy-prev-head.bak-before-${FULL_SHA:0:8}"
  if [[ -f "${prevfile}" && ! -L "${prevfile}" ]]; then
    IFS= read -r head < "${prevfile}" || true
    if [[ "${head}" =~ ${RE_SHA_FULL} ]]; then
      say "직전 배포 해시: ${head} — 코드(레포)까지 되돌리는 것은 별도 절차다(서버 책임자가 직접)"
    fi
  fi
}

BACKUP_DEST=""
backup_dest() { # 원본경로 [확장자] → BACKUP_DEST. 같은 이름이 있으면 시각을 덧붙인다.
  local src="$1" ext="${2:-}" dest
  dest="${P_backup_dir}/${src##*/}.bak-before-${FULL_SHA:0:8}"
  if [[ -e "${dest}${ext}" ]]; then
    stamp_now
    dest="${dest}.${REPLY}"
  fi
  BACKUP_DEST="${dest}${ext}"
}

do_restart_backend() {
  local head timeout poll waited=0 unit src
  local -a sqlite_files=() plain_files=() dirs=()
  require_paths repo backup_dir
  service_unit
  unit="${REPLY}"
  policy_items restart.backup_sqlite "${RE_PATH}"
  sqlite_files=("${ITEMS[@]}")
  policy_items restart.backup_files "${RE_PATH}"
  plain_files=("${ITEMS[@]}")
  policy_items restart.backup_dirs "${RE_PATH}"
  dirs=("${ITEMS[@]}")
  resolve_sha "${ARGS[0]}"
  head="$(as_deploy git -C "${P_repo}" rev-parse HEAD)" || step_fail "git-head" "${RC_FAIL}" "레포 HEAD 를 볼 수 없다"
  [[ "${head}" == "${FULL_SHA}" ]] ||
    step_fail "head-mismatch" "${RC_FAIL}" "서버 레포 HEAD 가 그 SHA 가 아니다. 재시작하면 다른 코드가 뜬다"
  policy_uint restart.health_timeout_seconds 180
  timeout="${REPLY}"
  policy_uint restart.poll_seconds 5
  poll="${REPLY}"

  say "백업(umask 077, 600):"
  for src in "${sqlite_files[@]}"; do
    [[ "${src}" != *..* && -f "${src}" ]] || step_fail "backup-source" "${RC_FAIL}" "백업할 파일이 없다: ${src##*/}"
    backup_dest "${src}"
    run python3 -I -c "${PY_SQLITE_BACKUP}" "${src}" "${BACKUP_DEST}"
  done
  for src in "${plain_files[@]}"; do
    [[ "${src}" != *..* && -f "${src}" ]] || step_fail "backup-source" "${RC_FAIL}" "백업할 파일이 없다: ${src##*/}"
    backup_dest "${src}"
    run cp -p -- "${src}" "${BACKUP_DEST}"
  done
  for src in "${dirs[@]}"; do
    [[ "${src}" != *..* && -d "${src}" ]] || step_fail "backup-source" "${RC_FAIL}" "백업할 폴더가 없다: ${src##*/}"
    backup_dest "${src}" ".tgz"
    run tar --force-local -czf "${BACKUP_DEST}" -C "${src%/*}" "${src##*/}"
  done
  say "백업 끝"

  say "재시작: 메모리에만 있는 상태가 지워진다(--ack-data-loss 확인됨)"
  run systemctl reset-failed "${unit}"
  run systemctl restart "${unit}"
  if (( DRY )); then
    say "DRY_RUN: 정책의 health 주소가 모두 200 이 될 때까지 ${poll}초 간격으로 최대 ${timeout}초 기다린다"
    return 0
  fi
  while :; do
    health_count
    (( H_OK == H_TOTAL )) && break
    (( waited >= timeout )) &&
      step_fail "health-timeout" "${RC_FAIL}" "${timeout}초 안에 다 뜨지 않았다: ${H_OK}/${H_TOTAL} — 실패:${H_BAD}"
    sleep "${poll}"
    waited=$((waited + poll + 1))
  done
  say "health: ${H_OK}/${H_TOTAL} (약 ${waited}초)"
  journal_count "${unit}"
  say "최근 5분 서비스 로그의 오류 줄 수: ${REPLY} (재시작 전 줄도 섞여 있을 수 있다)"
}

# ---------------------------------------------------------------------------
# 뒷단
# ---------------------------------------------------------------------------
RUN_LOCK_HELD=""
release_run_lock() {
  if [[ -n "${RUN_LOCK_HELD}" ]]; then
    rmdir -- "${RUN_LOCK_HELD}" 2>/dev/null || true
  fi
}

acquire_run_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9> "${RUN_LOCK}"
    flock -n 9 || deny "busy" "${RC_FAIL}" "다른 게이트 작업이 돌고 있다. 끝난 뒤 다시 한다"
  else
    # flock 이 없는 곳(로컬 시험)용 대체
    mkdir -- "${RUN_LOCK}.d" 2>/dev/null || deny "busy" "${RC_FAIL}" "다른 게이트 작업이 돌고 있다. 끝난 뒤 다시 한다"
    RUN_LOCK_HELD="${RUN_LOCK}.d"
    trap release_run_lock EXIT
  fi
}

root_main() {
  local -a req=()
  local rc=0 a
  LAYER="root"
  if [[ -z "${TEST_ROOT}" && "${EUID}" -ne 0 ]]; then
    printf '뒷단은 root 로만 돈다(sudo -n %s)\n' "${SELF}" >&2
    exit "${RC_CONFIG}"
  fi
  # 신원: sudo 가 넣어 준 SUDO_USER 만 믿는다. 부르는 쪽은 sudo 를 거치면서 이 값을 바꿀 수 없다.
  # 요청(표준 입력)에는 이름 칸이 없다. 누가 이름을 적어 보내도 형식 오류로 거부된다.
  local su="${SUDO_USER:-}" uid=""
  if [[ ! "${su}" =~ ${RE_KEY} || "${su}" == "root" ]]; then
    printf '뒷단은 sudo 를 거쳐서만 돈다(SUDO_USER 가 없거나 쓸 수 없는 계정). 실행하지 않는다\n' >&2
    exit "${RC_CONFIG}"
  fi
  uid="$(id -u -- "${su}" 2>/dev/null)" || uid=""
  if [[ -z "${uid}" || "${uid}" != "${SUDO_UID:-}" ]]; then
    printf '뒷단: SUDO_USER 와 SUDO_UID 가 맞지 않는다. 실행하지 않는다\n' >&2
    exit "${RC_CONFIG}"
  fi
  KEY="${su}"
  mapfile -t -n 10 req
  if (( ${#req[@]} < 4 || ${#req[@]} > 9 )); then
    deny "bad-request" "${RC_USAGE}" "요청 형식이 잘못됐다"
  fi
  [[ "${req[0]}" == "0" || "${req[0]}" == "1" ]] || deny "bad-request" "${RC_USAGE}" "요청 형식이 잘못됐다"
  [[ "${req[1]}" == "0" || "${req[1]}" == "1" ]] || deny "bad-request" "${RC_USAGE}" "요청 형식이 잘못됐다"
  [[ "${req[2]}" == "-" || "${req[2]}" =~ ${RE_PR} ]] || deny "bad-request" "${RC_USAGE}" "요청 형식이 잘못됐다"
  DRY="${req[0]}"
  ANNOUNCED="${req[1]}"
  PR="${req[2]}"
  ACTION="${req[3]}"
  ARGS=("${req[@]:4}")
  for a in "${ARGS[@]}"; do
    if [[ -z "${a}" || "${a}" =~ ${RE_NONPRINT} || "${a}" == *" "* ]]; then
      ACTION="invalid"
      deny "bad-request" "${RC_USAGE}" "인자에 쓸 수 없는 글자가 있다"
    fi
  done
  umask 077
  load_policy
  validate_args
  check_permission
  check_root_owned "${CHECKER}" || deny "checker-perms" "${RC_CONFIG}" "검증기 파일이 root 소유가 아니거나 남이 쓸 수 있다"
  case "${ACTION}" in
    status | record-pr) ;;
    unlock) acquire_run_lock ;;
    *)
      deny_if_locked
      require_announced
      acquire_run_lock
      ;;
  esac
  if ! audit "start"; then
    printf '감사 로그에 쓸 수 없어 실행하지 않는다\n' >&2
    exit "${RC_CONFIG}"
  fi
  set +e
  (
    set -e
    case "${ACTION}" in
      status) do_status ;;
      lock) do_lock ;;
      unlock) do_unlock ;;
      nginx-restore) do_nginx_restore ;;
      nginx-add-location) do_nginx_add_location ;;
      nginx-approve-location) do_nginx_approve_location ;;
      record-pr) do_record_pr ;;
      deploy-bundle) do_deploy_bundle ;;
      restart-backend) do_restart_backend ;;
      rollback-bundle) do_rollback_bundle ;;
    esac
  )
  rc=$?
  set -e
  audit "end:rc=${rc}" || printf '경고: 감사 로그에 끝 기록을 쓰지 못했다\n' >&2
  exit "${rc}"
}

main() {
  if (( $# == 0 )); then
    root_main
  else
    front_main "$@"
  fi
}

main "$@"
