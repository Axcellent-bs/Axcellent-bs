# 자동 배포 (ax-autodeploy)

운영 서버(Tencent)에서 `origin/dev` 를 90초마다 확인해 **프론트엔드(`/var/www/axcellent`, `/var/www/axcellent-admin`)와 홍보 사이트 정적 파일(`/var/www/promo`)만** 다시 배포한다. 백엔드는 `AX_BACKEND_STAGE=on` 일 때만 아래 "백엔드 단계"로 배포한다. 꺼져 있으면(기본) 새 커밋에 `backend/` 변경이 섞여도 프론트만 배포하고 로그에 `backend-changed: manual deploy needed`. nginx·`.env` 는 어느 경우에도 대상이 아니다.

- 스크립트: `ax-autodeploy` (서버에 `/usr/local/sbin/ax-autodeploy`, root:root 755, 실행은 ubuntu)
- 유닛: 서버에 이미 있는 `ax-autodeploy.service`(oneshot) / `ax-autodeploy.timer`(부팅 3분 뒤, 이전 실행 끝나고 90초마다). 이 폴더는 유닛을 바꾸지 않는다.

## 매 주기 순서

1. `flock` 으로 중복 실행 방지(이미 돌고 있으면 `busy` 로 건너뜀)
2. 정지 표시 확인: `AX_PAUSE_FILE` 이 설정되고 `/usr/local/sbin/ax-gate-autodeploy-paused` 가 있으면 호출해, 멈춰 있으면 `paused` 로 건너뜀(아래 "정지")
3. 빌드 전용 트리(`/opt/axcellent/autodeploy-src`, 별도 clone)에서 `git fetch origin dev`. **운영 트리 `/opt/axcellent/Axcellent-bs` 는 건드리지 않는다**(backend/.env 값을 읽기만 한다)
4. `last-deployed-ref` 이후 `git diff --name-only` 로 변경 판단
   - `frontend/`(문서·`*.test.ts(x)` 제외) 변경 -> 프론트 두 곳 재배포
   - `docs/promo/` 변경 -> 이미 `/var/www/promo` 에 있는 파일 갱신 + `assets/` 아래 새 파일 추가. `*.mjs *.md *.sh *.py *.json INTAKE* README* content.* build.*` 는 배포 안 함
   - 웹 변경이 없으면 기준 ref 만 갱신하고 끝(빌드 안 함)
   - 변경 파일 500개 초과면 중단(`abort`)
5. CI: `/etc/ax-autodeploy/gh-readonly-token` 이 있으면 GitHub check-runs 가 모두 끝났고 success(skipped·neutral 허용)일 때만 배포. 진행 중이면 다음 주기에 다시, 실패면 배포 안 함. 파일이 없으면 CI 확인 없이 진행하고 로그에 `ci-check=off`. 토큰은 이 스크립트가 만들지 않는다(읽기 전용 토큰을 사람이 둔다)
6. `/opt/axcellent/deploy.sh` 재사용(`AXCELLENT_REPO=빌드 전용 트리`, 두 웹 루트에 한 번씩). 열람 전용 토큰은 운영 `backend/.env` 의 `SHOP_VIEWER_TOKEN` 만 읽어 `VITE_SHOP_VIEWER_TOKEN` 으로 넘긴다(값 출력 없음), 사람 토큰은 자리표시자 `human-via-nginx`
7. 검증: index.html 이 가리키는 파일 존재 + HTTP 200 + **토큰 검사**(nginx 사람 토큰 맵·`.env` 의 HUMAN/WORK/ADMIN 토큰 실제 값이 번들에 없고 자리표시자만 있는지)
8. 실패(빌드 실패·시간 초과(1200초)·200 아님·토큰 검사 실패·예상 못 한 종료)면 배포 직전 백업(`/var/lib/ax-autodeploy/prev/`)으로 즉시 복원하고 기준 ref 는 갱신하지 않는다. 같은 커밋으로 3회 연속 실패하면 그 커밋은 `skipped-ref` 에 적고 다음 새 커밋을 기다린다

비밀값은 로그에 적지 않는다. 로그: `/var/log/ax-autodeploy.log`(실행 결과만, 변화 없는 주기는 안 적음), 빌드 출력: `/var/log/ax-autodeploy-build.log`(매 배포마다 덮어씀), journald 에도 같은 내용. 마지막 실행 요약: `/var/lib/ax-autodeploy/last-run`.

## 설치 (서버 책임자, sudo 필요)

```bash
# 1) 스크립트
sudo install -o root -g root -m 755 ax-autodeploy /usr/local/sbin/ax-autodeploy
# 2) 로그·상태 폴더(실행 계정 ubuntu 가 쓴다)
sudo install -d -o ubuntu -g ubuntu -m 755 /var/lib/ax-autodeploy
sudo install -o ubuntu -g ubuntu -m 640 /dev/null /var/log/ax-autodeploy.log
sudo install -o ubuntu -g ubuntu -m 640 /dev/null /var/log/ax-autodeploy-build.log
# 3) 빌드 전용 트리(운영 트리와 분리된 별도 clone)
git clone /opt/axcellent/Axcellent-bs /opt/axcellent/autodeploy-src
git -C /opt/axcellent/autodeploy-src remote set-url origin https://github.com/Axcellent-bs/Axcellent-bs.git
git -C /opt/axcellent/autodeploy-src fetch origin dev
# 4) 기준 ref 시드: 지금 화면에 실제로 배포돼 있는 커밋(전체 40자)
echo <배포된 커밋 40자> > /var/lib/ax-autodeploy/last-deployed-ref
# 5) 확인 후 타이머 켜기(재헌 확인 뒤에만)
/usr/local/sbin/ax-autodeploy --dry-run
sudo systemctl enable --now ax-autodeploy.timer
```

CI 확인을 켜려면 읽기 전용 GitHub 토큰(Checks: read)을 `/etc/ax-autodeploy/gh-readonly-token`(600)에 둔다. 없으면 `ci-check=off`.

## 정지

- 급히 멈춤: `sudo systemctl disable --now ax-autodeploy.timer`
- 게이트 연결(`docs/ops/ssh-gate/INSTALL.md` 7-1): 게이트 정책 `[autodeploy] pause_file` 경로를 `/etc/ax-autodeploy/env` 에 `AX_PAUSE_FILE=<경로>` 로 적는다(root 소유, 그룹·기타 쓰기 없음). 그러면 매 주기 맨 앞에서 `ax-gate-autodeploy-paused` 를 불러 `autodeploy-pause` 중이면 건너뛴다. 연결 전에는 dry-run 이 `게이트 정지 연결: NOT-CONNECTED` 를 보여 준다

## 롤백

- 직전 배포 직후 화면만 되돌리기: `/var/lib/ax-autodeploy/prev/axcellent`·`axcellent-admin` 이 배포 직전 복사본이다(마지막 배포 1회분). `sudo rm -rf /var/www/axcellent && sudo cp -a /var/lib/ax-autodeploy/prev/axcellent /var/www/axcellent` (admin 도 같게). 롤백 전에 타이머를 먼저 멈추고, 기준 ref 를 되돌린 커밋으로 고쳐 다시 배포되지 않게 한다.
- 특정 커밋으로 고정: 타이머 정지 후 수동 배포(`AXCELLENT_REF=<sha>` + `/opt/axcellent/deploy.sh`)와 `last-deployed-ref` 갱신.
- 홍보 파일: `/var/lib/ax-autodeploy/prev-promo/` 에 직전 파일 복사본, `prev-promo.added` 에 새로 더한 파일 목록.
- 3회 실패로 건너뛴 커밋 풀기: `sudo rm /var/lib/ax-autodeploy/skipped-ref /var/lib/ax-autodeploy/fail-count` (ubuntu 소유라 sudo 없이도 됨).

## 시험

`--dry-run` 은 상태·화면을 바꾸지 않고(fetch 만) 대상 커밋, 변경 영역, CI 상태, 정지 연결을 출력한다. 시험용 환경 변수: `AX_STATE_DIR AX_SRC AX_DEPLOY_SH AX_WEB_ROOT_90 AX_WEB_ROOT_91 AX_URL_90 AX_URL_91 AX_PROMO_ROOT AX_URL_PROMO(off 가능) AX_LOG_FILE AX_BUILD_LOG AX_SUDO AX_PAUSE_FILE AX_GATE_BIN`.

## 백엔드 단계 (off / dry-run / on)

설계: `docs/superpowers/specs/2026-11-24-backend-autodeploy-design.md`. `AX_BACKEND_STAGE`(또는 `/etc/ax-autodeploy/env` 의 같은 키)로 고른다.

- `off`(기본): 아무것도 안 한다. 기존 프론트·홍보 동작과 같다.
- `dry-run`: 배포·재시작·슬랙 전송 없이 판정만 하고 로그(`backend-dry-run: ...`)와 공지 문안(`backend-notice-draft ...`)만 남긴다.
- `on`: 같은 판정을 통과하면 게이트(ax-gate 0.5.0)로 실제 배포한다. `ax-autodeploy --dry-run` 은 `on` 이어도 아무것도 바꾸지 않는다.
- 그 밖의 값: 오류를 로그에 남기고 백엔드 단계만 `off` 로 본다. 프론트·홍보 단계는 막히지 않는다.

### 정책(재헌 11/24 확정)

| # | 정책 | 설정 |
|---|---|---|
| 1 | 주말·공휴일 포함 항상 자동 배포(요일 제한 없음). 시각은 기본 24시간 | `AX_ALLOW_HOURS`(KST, `시작-끝`, 기본 `0-24`. `9-22`, 밤을 넘기는 `22-6` 가능) |
| 2 | 미루기 상한 4시간. 넘기면 알림만(강제 재시작 없음), SHA 당 1회 | `AX_BACKEND_DEFER_MAX_SEC`(14400) |
| 3 | dev 최신 커밋 10분 조용, 재시작 사이 최소 60분 | `AX_BACKEND_QUIET_SEC`(600), `AX_BACKEND_MIN_GAP_SEC`(3600) |
| 4 | 필수 체크 `ci`·`backend`(completed+success). `data-eval` 은 진행 중·실패면 보류(`ci-hold`/`ci-fail`), 취소·없음이면 대기(`ci-pending`) | |
| 5 | 배포 메모: 라벨 `manual-deploy` 또는 `## 배포 메모` 가 있고 "없음"이 아니면 보류. 섹션 없음은 `AX_MEMO_REQUIRED_SINCE`(YYYY-MM-DD) 이후 머지된 PR 만 보류 | `AX_MEMO_LABEL`, `AX_MEMO_REQUIRED_SINCE` |
| 6 | 롤백: 실패하면 `rollback-bundle` → `rollback-code` → `restart-backend <직전 SHA>`(설계 5절). 1회 실패한 SHA 는 새 커밋이 올 때까지 건너뜀 | |
| 7 | 범위에 `backend/uv.lock`·`pyproject.toml` 변경이 있으면 `deploy-bundle` 뒤에 `uv-sync` 삽입(롤백 때는 직전 SHA 로 `uv-sync`) | |
| 8 | 배포 전 Slack Incoming Webhook 공지(`/etc/ax-autodeploy/slack-webhook`), 공지 실패면 배포 중단. 끝·실패·롤백·보류·미루기 공지. SHA 별 중복 방지 | `AX_SLACK_WEBHOOK_FILE` |
| 9 | 정지 표시(`AX_PAUSE_FILE`)가 있으면 모든 배포 중단. `on` 에서 `AX_PAUSE_FILE` 가 비어 있거나 판정 헬퍼가 없으면 멈춘 것으로 본다 | `AX_PAUSE_FILE`, `AX_GATE_BIN` |
| 10 | 읽기 전용 토큰 `/etc/ax-autodeploy/gh-readonly-token` 이 없으면 `ci-off` 로 보류 | `AX_CI_TOKEN_FILE` |
| 11 | 동시 실행 방지(flock). 프론트·홍보 단계는 기존 동작 그대로 | |

게이트 호출 형식: `sudo -n -u axautodeploy env SSH_ORIGINAL_COMMAND="<동작> <SHA> --announced" /usr/local/sbin/ax-gate axautodeploy`(`AX_GATE_SUDO`, `AX_GATE_EXEC`, 동작당 제한 시간 `AX_GATE_TIMEOUT` 420초). 게이트가 끝 코드 4(잠금·사용 중)로 첫 단계를 거부하면 실패가 아니라 이번 주기 건너뜀이다. 배포가 끝나면 `deploy-bundle` 이 프론트도 다시 빌드하므로 그 주기의 프론트 단계는 건너뛰고 기준 ref 만 갱신한다.

선택: `AX_BUSY_URLS`(공백으로 나눈 조회 주소. 응답 목록·`count` 가 0 이 아니거나 조회가 실패하면 재시작을 미룬다). 기본은 비어 있어 확인하지 않는다.

판정 순서: 이전 배포 흔적(`backend-in-progress`)·정지 표시 연결·배포 금지 잠금(`/etc/ax-gate/deploy.lock`) → 운영 트리 HEAD..dev 최신에 `backend/` 변경 → 이전 실패 SHA(`backend-skipped-ref`) → CI → 범위 안 커밋의 PR 라벨·`## 배포 메모`(PR 을 못 찾으면 보류) → 허용 시각 → 조용한 구간 → 최소 간격 → 대기 건 → (on) 공지 → 게이트. API 실패·해석 실패는 `skip-cycle`(이번 주기 건너뜀, 상태 기록 없음). 상태 파일(`/var/lib/ax-autodeploy/`): `backend-verdict`, `backend-notified`, `backend-last-restart`, `backend-in-progress`(되돌림까지 실패하면 `halted ...` 로 남고 사람이 확인해 지울 때까지 아무것도 안 한다), `backend-skipped-ref`, `backend-defer-since`.

서버에 필요한 것(서버 작업은 사람이): 게이트 0.5.0 + `axautodeploy` 계정(권한 6개: `status deploy-bundle restart-backend rollback-bundle rollback-code uv-sync`, `docs/ops/ssh-gate/INSTALL.md`), `/etc/ax-autodeploy/gh-readonly-token`(Checks·Pull requests 읽기), `/etc/ax-autodeploy/slack-webhook`(root 600), `/etc/ax-autodeploy/env` 에 `AX_BACKEND_STAGE=on`·`AX_PAUSE_FILE=<게이트 정책 pause_file>`, `ax-gate-autodeploy-paused`.

시험: `python -m unittest discover -s docs/ops/autodeploy/tests -v` (임시 git 저장소, 가짜 게이트·가짜 GitHub API·가짜 Slack 웹훅을 쓴다. 서버·네트워크 불필요).
