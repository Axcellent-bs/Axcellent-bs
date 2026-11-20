#!/usr/bin/env bash
# api-direct-env.sh — 우리 운영 서버의 화면 배포 스크립트(/opt/axcellent/deploy.sh)가 source 하는 조각.
# 화면(vite build) 직전에 api-app 직접 호출 환경값 두 개의 기본값을 켜고, 배포 뒤 번들에 반영됐는지 본다.
#
# 이 파일은 우리 운영 서버에만 설치한다. 프론트 코드(apiDirect.ts)·.env.production 기본값은 꺼짐 그대로다.
# (고객 서버에 설치되는 AXcellent 는 이 파일이 없으므로 직접 호출이 꺼진 채로 빌드된다.)
#
# 함수
#   ax_api_direct_defaults        두 환경값에 기본값을 채워 export 하고 "직접 호출: 켜짐/꺼짐" 한 줄을 낸다
#   ax_api_direct_verify <폴더>   배포된 번들(폴더)에 직접 호스트 주소가 들어갔는지 본다. 없으면 경고만 내고 1 을 돌려준다
#
# 값 규칙 (명시한 값이 항상 이긴다)
#   VITE_API_DIRECT_BASE      안 정함(unset) -> https://api-app.axcellent.cloud
#                             빈 값으로 정함 -> 빈 값 그대로(= 끔).  다른 값 -> 그 값
#   VITE_API_DIRECT_SERVICES  안 정함(unset) -> dashboard,orch,safety,planning,dev
#                             빈 값으로 정함 -> 빈 값 그대로(= 끔).  다른 값 -> 그 값
#   둘 중 하나라도 비면 화면이 직접 호출을 하지 않는다(apiDirect.ts). 끄기: VITE_API_DIRECT_SERVICES= (권장)
#   기본값 대체에 `:-` 가 아니라 `-` 를 쓰는 이유는 README.md 의 "빈 값 처리" 참고.
#
# 직접 실행(드라이): bash api-direct-env.sh --print-env   -> 결정된 값 두 줄과 켜짐/꺼짐만 출력한다(빌드·네트워크 없음)
# 비밀값은 없다(공개 호스트 이름과 서비스 목록뿐). 배포 로그에는 값 대신 켜짐/꺼짐만 낸다.

AX_API_DIRECT_DEFAULT_BASE="https://api-app.axcellent.cloud"
AX_API_DIRECT_DEFAULT_SERVICES="dashboard,orch,safety,planning,dev"

ax_api_direct_defaults() {
  export VITE_API_DIRECT_BASE="${VITE_API_DIRECT_BASE-${AX_API_DIRECT_DEFAULT_BASE}}"
  export VITE_API_DIRECT_SERVICES="${VITE_API_DIRECT_SERVICES-${AX_API_DIRECT_DEFAULT_SERVICES}}"
  if [[ -n "${VITE_API_DIRECT_BASE}" && -n "${VITE_API_DIRECT_SERVICES}" ]]; then
    echo "직접 호출: 켜짐"
  else
    echo "직접 호출: 꺼짐"
  fi
}

ax_api_direct_verify() {
  local dir="${1:-}"
  if [[ -z "${VITE_API_DIRECT_BASE:-}" || -z "${VITE_API_DIRECT_SERVICES:-}" ]]; then
    echo "번들 확인: 건너뜀(직접 호출 꺼짐)"
    return 0
  fi
  if [[ -n "${dir}" && -d "${dir}" ]] && grep -rqF --include='*.js' -- "${VITE_API_DIRECT_BASE}" "${dir}" 2>/dev/null; then
    echo "번들 확인: 직접 호스트 주소 포함됨"
    return 0
  fi
  echo "경고: 번들에 직접 호스트 주소가 없다(환경값 없이 빌드됐을 수 있음). 배포는 계속된다. 폴더=${dir:-<없음>}" >&2
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    --print-env)
      ax_api_direct_defaults
      echo "VITE_API_DIRECT_BASE=${VITE_API_DIRECT_BASE}"
      echo "VITE_API_DIRECT_SERVICES=${VITE_API_DIRECT_SERVICES}"
      ;;
    *) echo "사용법: bash api-direct-env.sh --print-env   (평소에는 deploy.sh 가 source 한다)" >&2; exit 2 ;;
  esac
fi
