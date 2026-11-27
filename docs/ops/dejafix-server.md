# 운영 서버의 DejaFix·Zabbix 구성과 재배포

운영 서버에 같이 설치된 DejaFix(장애 대응)와 Zabbix(감시)가 어떻게 떠 있고, AXcellent와 어떻게 이어지며, DejaFix를 새 버전으로 올릴 때 무엇을 남기는지 적는다.
2026-11-27에 서버에서 읽기 전용(`docker ps`, `docker compose ls`, `docker inspect`, `ls`)으로 확인한 내용만 적었다. 비밀값(계정·토큰·`.env` 값)은 이 문서에 없다.

- DejaFix 코드의 원본은 `DejaFix/DejaFix` 레포다. 이 레포에서 고치지 않는다(CLAUDE.md). 여기 있는 것은 서버 운영 사실뿐이다.
- 이 Zabbix·DejaFix는 서버와 고객사 시스템을 감시하는 용도다. 팀원 PC 감시용이 아니다.
- 옛 서버(232·251)의 DejaFix·Zabbix는 이관으로 정지했다. 아래는 새 운영 서버 한 곳의 구성이다.

## 1. 구성

| 무엇 | 어떻게 떠 있나 | 위치 |
| --- | --- | --- |
| Zabbix 7.0.32 | 컨테이너 3개(`zabbix-db` postgres 16, `zabbix-server`, `zabbix-web`), compose 프로젝트 `zabbix` | `/opt/zabbix/docker-compose.yml` |
| Zabbix 포트 | 서버 10051(에이전트가 보내는 포트)만 밖에 열고, 웹은 `127.0.0.1:8081` | |
| 서버 자신의 감시 | 호스트에 `zabbix-agent2` 서비스 | |
| DejaFix | 합본 컨테이너 `dejafix-app` 하나(백엔드·화면을 한 이미지에), compose 프로젝트 `dejafix`, `restart: unless-stopped`, healthcheck 있음 | 아래 2절 |
| DejaFix 포트 | `127.0.0.1:18000`, `127.0.0.1:18080`(둘 다 컨테이너 8000). 밖에 열지 않는다 | |
| DejaFix 데이터 | 이름 고정 볼륨 `dejafix-data` → 컨테이너 `/data` | |
| DejaFix → Zabbix | compose 외부 네트워크 `zabbix_net`으로 `zabbix-web`의 API를 부른다(실제 Zabbix, 목 아님) | |

## 2. DejaFix 파일 위치

| 경로 | 내용 |
| --- | --- |
| `/opt/dejafix-app-build/` | 지금 이미지를 만든 소스 트리(복사본, git 저장소가 아니다). 직전 트리는 `/opt/dejafix-app-build.prev-<표시>`로 남긴다 |
| `/opt/dejafix-app-build/infra/docker-compose.app.yml` | 합본 컨테이너의 기본 compose(레포 원본 그대로, 서버에서 고치지 않는다) |
| `/opt/dejafix-ops/docker-compose.app.server.yml` | 서버용 override: 이미지 태그 고정(`dejafix-app:dev-<커밋 7자>`), 환경 파일 지정, `zabbix_net` 연결 |
| `/opt/dejafix/.env` | 환경 파일(비밀값. 읽거나 옮겨 적지 않는다) |
| `/home/ubuntu/dejafix-before-<태그>-<시각>/` | 재배포 직전 백업: 볼륨 묶음(`dejafix-data.tgz`), override 사본, 직전 이미지 id(`image-id.txt`) |

지금 떠 있는 이미지는 `dejafix-app:dev-566de75`(DejaFix dev `566de75`, 2026-11-27 재배포)다. 직전 이미지(`pr215`, 11/26)도 서버에 남아 있다.

## 3. AXcellent와의 연결

```
화면 /dejafix-api/*  ──▶ 화면용 nginx ──▶ dejafix-proxy(127.0.0.1:8030, AXcellent 백엔드가 같이 띄움) ──▶ DejaFix(127.0.0.1:18000)
인프라 AI(장애 감시·대화 근거) ─────────▶ dejafix-proxy ──▶ DejaFix
```

- dejafix-proxy(`backend/shared/dejafix-proxy`)가 DejaFix에 대신 로그인하고, 부르는 쪽 토큰을 확인한 뒤 정해 둔 조회만 넘긴다. 회원·서버 관리 화면의 Zabbix 주소·읽기 토큰 저장(`/dejafix-api/settings/zabbix`, 11/25 #1835)도 이 프록시를 거친다.
- AXcellent 쪽 연결 값은 `backend/.env`의 `DEJAFIX_*`·`INFRA_DEJAFIX_*`에 있다(값은 적지 않는다). 회사가 화면에서 따로 넣은 연결은 그 회사 보관함에 있다.
- 예전의 로그인 대행 게이트웨이 컨테이너(`dejafix-gateway-1`, `127.0.0.1:18001`)는 아직 떠 있지만, 화면용 nginx의 `/dejafix-api/`는 이제 dejafix-proxy로 간다. 게이트웨이를 내릴지는 서버 책임자가 정한다.

### 회사별 분리(DejaFix #019)는 지금 꺼져 있다

- DejaFix의 회사별 분리는 `DEFAULT_COMPANY_ID`를 넣어야 켜진다. 지금 컨테이너에는 이 값이 없다(꺼짐).
- 합의: Agent 이관이 들어올 때 값 `catbooks`로 켠다. 그 전에는 넣지 않는다.
- 꺼져 있는 동안 AXcellent 쪽 동작(`dejafix-proxy`, 11/26 #1884·#1950): 같이 설치된 DejaFix가 회사를 돌려주지 않으면 그 데이터는 캣북스 것으로 보고, 다른 회사에는 장애·현황을 넘기지 않으며 Zabbix 저장도 막는다.

## 4. DejaFix 재배포 순서

서버 변경이므로 서버 책임자가 한다. 11/27 재배포(dev `566de75`) 때 실제로 쓴 순서다.

1. 팀 채널에 알린다(장애 대응 탭이 잠깐 비거나 "연결 전"으로 보일 수 있다).
2. 백업 폴더 `/home/ubuntu/dejafix-before-<새 태그>-<시각>/`를 만들고 볼륨 `dejafix-data` 묶음, 지금의 override 사본, 지금 이미지 id를 넣는다.
3. 지금 이미지에 보관용 태그(`dejafix-app:<직전 태그>-keep`)를 붙이고, 지금 소스 트리를 `/opt/dejafix-app-build.prev-<표시>`로 남긴다. 올릴 커밋의 트리는 DejaFix 레포에서 `git archive <커밋>`으로 만들어 `/opt/dejafix-app-build/`에 둔다.
4. override의 `image:` 줄을 새 태그 `dejafix-app:dev-<커밋 7자>`로 바꾸고 이미지를 만든다. 프로젝트 이름과 compose 파일 두 개는 늘 같게 준다.
   ```bash
   docker compose -p dejafix \
     -f /opt/dejafix-app-build/infra/docker-compose.app.yml \
     -f /opt/dejafix-ops/docker-compose.app.server.yml build
   ```
5. 이 컨테이너만 다시 만든다.
   ```bash
   docker compose -p dejafix \
     -f /opt/dejafix-app-build/infra/docker-compose.app.yml \
     -f /opt/dejafix-ops/docker-compose.app.server.yml up -d --no-build dejafix-app
   ```
6. 확인: `docker ps`에서 `dejafix-app`이 `healthy`, 서버 안에서 `/healthz`가 ok, 컨테이너 로그에 에러 없음, AXcellent 화면의 DejaFix 연결 카드(회원·서버 관리)와 장애 대응 탭이 읽히는지.
7. 되돌리기: override의 `image:`를 직전 태그로 되돌려 5번을 다시 한다. 데이터까지 되돌려야 하면 2번의 볼륨 묶음을 쓴다.

지킬 것

- `docker compose ... down -v`는 하지 않는다. 이름 고정 볼륨 `dejafix-data`가 지워진다.
- 프로젝트 이름은 항상 `-p dejafix`. 다른 이름으로 올리면 컨테이너·네트워크가 따로 생긴다.
- 기본 compose(레포 원본)는 서버에서 고치지 않고 override만 고친다.
- `DEFAULT_COMPANY_ID`는 위 합의 전에는 넣지 않는다.
- Zabbix(`/opt/zabbix`)는 DejaFix 재배포와 따로다. 같이 내리지 않는다.
