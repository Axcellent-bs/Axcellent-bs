#!/usr/bin/env bash
# ax-gate-autodeploy-paused — 자동 배포가 지금 멈춰 있는지 판정한다(읽기 전용).
#
# 게이트의 autodeploy-pause / autodeploy-resume 는 정지 표시 파일 하나만 쓴다
# (정책 [autodeploy] pause_file). 자동 배포를 돌리는 쪽이 매 주기 맨 앞에서 이 스크립트로
# 그 파일을 읽고, 멈춰 있으면 그 주기를 건너뛴다. 이 스크립트는 아무것도 바꾸지 않는다.
#
# 쓰는 법(자동 배포 스크립트의 맨 앞, 서버 책임자가 한 번 넣는다):
#   if /usr/local/sbin/ax-gate-autodeploy-paused <정지 표시 파일 경로>; then
#     exit 0        # 멈춰 있다. 이번 주기는 배포하지 않는다
#   fi
#
# 끝 코드: 0 = 멈춰 있다(배포하지 않는다), 1 = 멈춰 있지 않다(배포해도 된다), 2 = 사용법 오류.
# 파일이 없으면 멈춰 있지 않은 것이다. 파일이 있는데 읽을 수 없거나 해석할 수 없으면
# 멈춘 것으로 본다(안전한 쪽). 이유 한 줄을 표준 오류에 적는다. 파일의 내용은 실행하지 않는다.

set -Eeuo pipefail
IFS=$' \t\n'
export LC_ALL=C

if (( $# != 1 )) || [[ -z "$1" ]]; then
  printf '사용법: %s <정지 표시 파일 경로>\n' "${0##*/}" >&2
  exit 2
fi

file="$1"
epoch=""
until=""
who=""

if [[ ! -e "${file}" && ! -L "${file}" ]]; then
  exit 1
fi
if [[ ! -f "${file}" || -L "${file}" || ! -r "${file}" ]]; then
  printf '자동 배포 정지: 정지 표시 파일을 읽을 수 없다. 멈춘 것으로 본다\n' >&2
  exit 0
fi

while IFS= read -r line || [[ -n "${line}" ]]; do
  case "${line}" in
    until_epoch=*) epoch="${line#until_epoch=}" ;;
    until=*) until="${line#until=}" ;;
    user=*) who="${line#user=}" ;;
  esac
done < "${file}"

if [[ ! "${epoch}" =~ ^[0-9]{1,12}$ ]]; then
  printf '자동 배포 정지: 정지 표시 파일을 해석할 수 없다. 멈춘 것으로 본다\n' >&2
  exit 0
fi
[[ "${until}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$ ]] || until="알 수 없음"
[[ "${who}" =~ ^[a-z][a-z0-9-]{1,31}$ ]] || who="알 수 없음"

now="$(date +%s)"
if (( now < epoch )); then
  printf '자동 배포 정지 중: 건 사람 %s, 재개 예정 %s\n' "${who}" "${until}" >&2
  exit 0
fi
exit 1
