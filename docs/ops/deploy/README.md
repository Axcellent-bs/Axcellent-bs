# 운영 서버 화면 배포: api-app 직접 호출 기본값

우리 운영 서버(axcellent.cloud)의 화면 배포가 `VITE_API_DIRECT_BASE`·`VITE_API_DIRECT_SERVICES` 없이 빌드되면 읽기 API 가 api-app 으로 가지 않고 같은 출처로 돌아간다(2026-11-20 두 번 발생). 이 폴더는 서버의 `/opt/axcellent/deploy.sh` 가 그 두 값을 **기본으로 켜게** 하는 조각을 둔다.

서버에 실제로 설치하는 일은 이 PR 범위 밖이다. 머지 뒤 재헌 승인으로 한다.

## 파일

| 파일 | 내용 |
| --- | --- |
| `api-direct-env.sh` | deploy.sh 가 source 하는 조각. 기본값 설정(`ax_api_direct_defaults`)과 번들 확인(`ax_api_direct_verify`) |
| `tests/test_api_direct_env.py` | 값 우선순위·번들 확인 테스트(`cd backend && uv run pytest ../docs/ops/deploy/tests -q`. backend CI 의 testpaths 밖이라 CI 에서는 안 돈다) |

## 환경값 두 개

| 이름 | 뜻 | 기본값(이 조각) |
| --- | --- | --- |
| `VITE_API_DIRECT_BASE` | 직접 호스트 출처(https) | `https://api-app.axcellent.cloud` |
| `VITE_API_DIRECT_SERVICES` | 직접 보낼 서비스(`dashboard,orch,safety,planning,dev` 중) | `dashboard,orch,safety,planning,dev` |

둘 **다** 있어야 화면(`frontend/src/lib/apiDirect.ts`)이 직접 호출한다. 하나라도 비면 같은 출처다.

## 켜기·끄기·빈 값 처리

기본값은 `${이름-기본값}` 형태다(`:-` 아님).

| 배포 때 준 값 | 결과 |
| --- | --- |
| 아무것도 안 줌(unset) | 기본값 -> 켜짐 |
| 다른 값을 줌 | 그 값을 따름 |
| 빈 값을 줌(`VITE_API_DIRECT_SERVICES=`) | 빈 값 그대로 -> **꺼짐** |

- **끄기**: `VITE_API_DIRECT_SERVICES= bash /opt/axcellent/deploy.sh` (권장). 기존 롤백 문서(`docs/ops/api-direct/PATHS.md`)의 "SERVICES 를 비워 재배포"와 같은 방법이다. BASE 를 비워도 꺼진다.
- **일부만**: `VITE_API_DIRECT_SERVICES=dashboard,orch bash /opt/axcellent/deploy.sh`
- 왜 `-` 인가: `:-` 는 빈 값도 기본값으로 바꿔서 끌 방법이 없어진다(끄려면 스크립트를 고쳐야 한다). 반대로 `-` 의 위험은 어떤 호출 래퍼가 실수로 빈 값을 넘기는 경우인데, 그건 "끄기"로 보이고 로그에 `직접 호출: 꺼짐`이 찍혀 바로 눈에 띈다. 이번 사고(값을 아예 안 줌)는 `-` 로도 막힌다.
- 로그: 값 대신 `직접 호출: 켜짐/꺼짐` 한 줄. 배포 뒤 `번들 확인:` 한 줄(켜짐인데 번들에 직접 호스트 주소가 없으면 경고만 내고 배포는 계속된다).

드라이 확인(빌드 없음): `bash api-direct-env.sh --print-env`, `VITE_API_DIRECT_SERVICES= bash api-direct-env.sh --print-env`.

## 왜 deploy.sh 한 곳인가

운영 서버에서 화면을 빌드·배포하는 경로는 전부 `/opt/axcellent/deploy.sh` 를 거친다.

| 경로 | 부르는 것 |
| --- | --- |
| 사람이 수동 실행 | `/opt/axcellent/deploy.sh` (`/home/ubuntu/run-fe-deploy.sh`, `/home/ubuntu/apidirect.sh` 가 그걸 부른다. 서버 쪽 사실) |
| ssh-gate `deploy-bundle` | 정책 `[deploy]` 의 배포 스크립트(`runuser -u <배포계정> -- env <정책 env> <스크립트>`). 게이트가 넘기는 env 에 `VITE_*` 는 없다 -> 조각의 기본값이 적용된다 |
| ax-autodeploy(PR #1478, 머지 전) | `DEPLOY_SH=/opt/axcellent/deploy.sh` 를 빌드 전용 트리에서 재사용 |
| 앱의 배포 도구(`dev_agent/deploy*.py`, `infra_agent/deploy_tools.py`·`deploy_poll.py`, `demo_deploy.sh`) | 화면을 빌드하지 않는다. 배포 기록·판정, Jenkins/GitHub Actions 로 고객 쇼핑몰 재배포 요청, 시연 기록 |

deploy.sh 의 원본은 이 리포에 없다. 전체를 새로 복사해 두면 서버 파일과 어긋나 덮어쓸 때 서버에만 있던 수정이 사라질 수 있어 하지 않았다. 대신 작은 조각(이 폴더)을 두고 deploy.sh 에는 두 곳만 더한다. 비교한 대안:

| 방식 | 장단 |
| --- | --- |
| 조각 source (채택) | deploy.sh 수정이 두 줄. 조각은 리포에서 리뷰·테스트됨. 조각이 없으면(고객 서버) 꺼짐 |
| deploy.sh 전체를 리포에 추가 | 서버 파일과 어긋날 위험, 서버 쪽 수정 유실 가능 |
| `/etc/axcellent/deploy.env` 를 deploy.sh 가 source | 리포 밖 값이라 리뷰·테스트가 안 되고, 어차피 deploy.sh 수정이 필요하다 |
| 호출하는 쪽(gate 정책, autodeploy, run-fe-deploy.sh)마다 env 지정 | 경로가 늘 때마다 빠진다. 이번 사고가 그 모양이다 |

## 서버 설치 절차 (머지 뒤, 재헌 승인 후)

배포 계정으로, 자동 배포(ax-autodeploy)가 있으면 먼저 `autodeploy-pause` 로 멈추고 한다.

1. 현재 deploy.sh 를 읽어 `vite build` 직전 위치와 웹 루트 변수 이름을 확인한다(아래 줄은 `AXCELLENT_WEB_ROOT` 기준).
2. 백업: `cp -p /opt/axcellent/deploy.sh /opt/axcellent/deploy.sh.bak-$(date +%Y%m%d%H%M)`
3. 조각 설치: `install -m 644 api-direct-env.sh /opt/axcellent/api-direct-env.sh`
4. deploy.sh 에 두 곳 추가
   - `vite build` 직전(기존 `export VITE_*` 줄들 뒤):
     ```bash
     if [ -f /opt/axcellent/api-direct-env.sh ]; then . /opt/axcellent/api-direct-env.sh; ax_api_direct_defaults; fi
     ```
   - 웹 루트 교체 뒤, 200 확인 앞(또는 뒤):
     ```bash
     if command -v ax_api_direct_verify >/dev/null 2>&1; then ax_api_direct_verify "${AXCELLENT_WEB_ROOT}" || true; fi
     ```
5. `bash -n /opt/axcellent/deploy.sh` -> `bash /opt/axcellent/api-direct-env.sh --print-env` 로 켜짐 확인 -> 배포 한 번 -> 로그의 `직접 호출: 켜짐`, `번들 확인: ... 포함됨` 확인.
6. `autodeploy-resume`.

## 롤백

- 일시: 위의 "끄기"(`VITE_API_DIRECT_SERVICES=`)로 재배포.
- 설치 되돌리기: 백업한 `deploy.sh.bak-*` 를 원래 이름으로 복사하고 재배포. `api-direct-env.sh` 는 남겨 둬도 deploy.sh 가 부르지 않으면 영향이 없다.

## 주의

- 이 기본값은 우리 운영 서버의 스크립트에만 둔다. 프론트 코드·`.env.production` 기본값은 꺼짐을 유지한다(고객 서버에 설치되는 제품이 우리 api-app 을 부르면 안 된다).
- 직접 호스트(api-app nginx·CORS·인증)는 `docs/ops/api-direct/PATHS.md` 를 따른다. 이 폴더는 화면 쪽 환경값만 다룬다.
- 비밀값은 없다.
