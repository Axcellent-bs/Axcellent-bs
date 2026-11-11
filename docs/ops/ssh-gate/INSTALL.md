# ax-gate 설치 절차 (일반 형태)

> **이 문서의 모든 단계는 보안 설정 변경이다. 서버 책임자가 직접 승인하고 직접 실행한다.** AI 세션이나 다른 사람이 대신 실행하지 않는다.
>
> 상태: 초안. 여기 있는 것은 절차의 일반 형태다. 실제 서버의 경로·주소·계정 이름·방화벽 규칙이 들어간 구체 절차는 이 레포에 없고 비공개 문서에 있다. `<...>`는 자리표시자다. 아래 명령은 아직 실제 서버에서 돌려 보지 않았다.

방식 요약: 공유 계정 없이 **팀원마다 개인 OS 계정**을 만든다. 게이트의 신원은 그 계정이다. 접속은 방화벽의 허용 출처와 authorized_keys의 `from=`을 같이 건다.

## 0. 먼저 정할 값

| 값 | 어디에 |
| --- | --- |
| 계정 이름(영어 소문자·숫자·`-`, 2~32자) | 계정 생성, 정책 `[permissions]` |
| 사람별 PC 주소 | 방화벽, `from=` |
| 권한표(모두 / 권한자) | 정책 `[permissions]` |
| 계정별 허용 경로 접두, 접두 → 포트 표, 허용 메서드, location 블록 줄 | 정책 `[location_prefixes]`, `[location_upstreams]`, `[location]` |
| 배포 기준 브랜치, 배포 계정, 배포 스크립트에 넘길 환경변수 | 정책 `[deploy]` |
| 번들 점검 기준(표시 문자열, 검사할 env 파일, 예외 이름) | 정책 `[deploy]` |
| 서비스 이름, 재시작 전 백업할 파일·폴더, 대기 시간 | 정책 `[restart]` |
| health 주소 목록, status 확인 주소 | 정책 `[health]`, `[status]` |
| 서버 경로(레포, 배포 스크립트, 번들 폴더, 백업 폴더, nginx 설정·백업) | 정책 `[paths]` |
| 잠금 기본·최대 시간, 만료 뒤 자동 해제 여부 | 정책 `[lock]` |
| 삽입 표시 줄을 넣을 위치 | nginx 설정 파일 |
| 감사 로그 보관·회전 방식 | 3단계 |

## 1. 파일 올리기

```bash
sudo install -o root -g root -m 755 ops-gate.sh /usr/local/sbin/ax-gate
sudo install -o root -g root -m 755 nginx_location_check.py /usr/local/sbin/ax-gate-location-check

sudo install -d -o root -g root -m 755 /etc/ax-gate
sudo install -o root -g root -m 644 <비공개 정책 파일> /etc/ax-gate/policy.toml
```

- 정책은 `policy.example.toml`을 그대로 쓰지 않는다. 예시는 가짜 값과 자리표시자뿐이다. 실제 값을 채운 정책 파일은 서버 책임자가 비공개로 관리한다.
- 실행 파일은 반드시 root 소유의 경로에 둔다. 다른 계정이 쓸 수 있는 폴더의 파일을 실행 경로로 쓰지 않는다. 게이트는 정책 파일과 검증기가 root 소유가 아니거나 남이 쓸 수 있으면 실행을 거부한다.

## 2. 그룹과 개인 계정

```bash
sudo groupadd --system axgate

# 사람마다 한 번씩
sudo adduser --disabled-password --gecos "<이름> (ax-gate)" --shell /bin/bash <계정>
sudo usermod -aG axgate <계정>
sudo passwd -l <계정>          # 비밀번호 잠금. 이 잠금은 풀지 않는다
id <계정>                      # 그룹이 <계정>, axgate 뿐인지 확인. 관리자·로그 열람 그룹이 있으면 안 된다
```

- **비밀번호 잠금을 풀지 않는다.** 접속은 공개키로만 한다. 비밀번호를 만들어 주지 않는다.
- 로그인 셸은 `/bin/bash`로 둔다. `nologin`으로 하면 안 된다. sshd는 강제 명령(`command=`)도 그 계정의 로그인 셸로 실행하기 때문에, nologin이면 게이트 자체가 실행되지 않는다. 셸을 못 쓰게 막는 것은 5단계의 `restrict`·`command=`·Match 블록이 한다.
- `axgate` 그룹에는 게이트를 쓸 사람의 개인 계정만 넣는다. 기존 관리자 계정은 넣지 않는다.

## 3. 폴더와 감사 로그

```bash
# 감사 로그: axgate 그룹은 덧붙이기만 한다
sudo install -d -o root -g axgate -m 750 /var/log/ax-gate
sudo install -o root -g axgate -m 660 /dev/null /var/log/ax-gate/audit.log
sudo chattr +a /var/log/ax-gate/audit.log      # 추가 전용(지우기·덮어쓰기 불가). 확인: lsattr

# 제안·승인 기록 (root 만 쓴다)
sudo install -d -o root -g root -m 755 /var/lib/ax-gate /var/lib/ax-gate/pending /var/lib/ax-gate/approved
```

- 감사 로그 권한: 폴더 `root:axgate 750`, 파일 `root:axgate 660` + `chattr +a`. 앞단(개인 계정)이 거부 기록을, 뒷단(root)이 실행 기록을 덧붙인다. 같은 줄이 `logger -t ax-gate`로 journal에도 간다.
- `chattr +a`가 걸린 파일은 logrotate가 못 돌린다. 회전이 필요하면 서버 책임자가 직접 속성을 풀고 옮긴 뒤 새 파일에 다시 건다. 초안은 자동 회전을 넣지 않았다.
- 잠금 파일 `/etc/ax-gate/deploy.lock`은 게이트(뒷단)가 `root:root 644`로 만든다. 미리 만들지 않는다.
- 백업 폴더는 정책 `[paths] backup_dir`에 적은 곳이다. 게이트가 만드는 백업 파일은 root 소유 600이다.

## 4. 제한 sudoers

그룹 한 줄로 한다.

```bash
sudo visudo -f /etc/sudoers.d/ax-gate
```

내용(한 줄):

```
%axgate ALL=(root) NOPASSWD: /usr/local/sbin/ax-gate ""
```

```bash
sudo chmod 0440 /etc/sudoers.d/ax-gate
sudo chown root:root /etc/sudoers.d/ax-gate
sudo visudo -cf /etc/sudoers.d/ax-gate
sudo visudo -c                       # 전체 검사
sudo -l -U <계정>                    # 위 한 줄만 나와야 한다(계정마다 확인)
```

- **계정별 파일 대신 그룹 하나로 묶은 이유**: sudo 권한은 모든 계정이 똑같이 "인자 없는 ax-gate 한 줄"이다. 사람마다 다른 것은 게이트 정책 표가 정한다. 파일을 사람 수만큼 두면 같은 줄이 여러 벌 생기고, 사람을 뺄 때 파일 하나를 잊으면 권한이 남는다. 그룹이면 넣고 빼는 것이 한 줄이고 sudoers 파일은 한 번만 검증하면 된다. 감사 로그 쓰기 권한(3단계)과 sshd Match(5단계)도 같은 그룹을 쓴다.
- `""`는 "인자가 없을 때만"이라는 뜻이다. 정확한 스크립트 경로 하나, 인자 없음. 임의 인자 허용은 없다.
- 요청 내용은 인자가 아니라 표준 입력으로 가고, 뒷단이 처음부터 다시 검증한다. 뒷단은 sudo가 넣어 준 `SUDO_USER`를 신원으로 쓴다.
- 기존 계정들의 sudo 설정은 이 설치로 바뀌지 않는다.

## 5. sshd와 키 등록

```bash
sudo install -d -o root -g root -m 755 /etc/ssh/ax-gate
sudoedit /etc/ssh/sshd_config.d/90-ax-gate.conf
```

내용:

```
Match Group axgate
    AuthorizedKeysFile /etc/ssh/ax-gate/%u
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitTTY no
    PermitUserRC no
```

```bash
sudo sshd -t                      # 문법 확인. 실패하면 reload 하지 않는다
sudo systemctl reload ssh
sudo sshd -T -C user=<계정> | grep -Ei 'authorizedkeysfile|allowtcpforwarding|x11forwarding|permittty'
sudo sshd -T -C user=<기존 관리자 계정> | grep -Ei 'authorizedkeysfile'     # 기존 계정은 그대로인지
```

- 서버의 전역 설정이 포워딩을 허용하고 있을 수 있으므로 Match 블록으로 이 그룹만 따로 막는다.
- `Match User`에 이름을 나열하지 않고 `Match Group`으로 한 이유: 사람을 넣고 뺄 때 sshd 설정을 다시 고치지 않아도 된다. 그룹에서 빠지면 sudo 권한도 같이 빠진다.
- `ForceCommand`는 넣지 않는다. 넣으면 authorized_keys의 `command=`를 덮어쓴다. 강제 명령은 키 줄에 건다.
- 키 파일을 홈 폴더가 아니라 root 소유의 `/etc/ssh/ax-gate/<계정>`에 둔다. 본인이 자기 키 줄(강제 명령·`from=`)을 고칠 수 없게 하려는 것이다. 홈 폴더 방식으로 하려면 `AuthorizedKeysFile` 줄을 빼고 홈의 키 파일을 root 소유 644로 둔다.
- reload 전에 **지금 쓰는 관리 접속을 끊지 말고**, 새 창에서 기존 접속이 되는지 먼저 확인한다.

### 키 등록(사람마다)

팀원이 보낸 **공개키**(`.pub`)의 지문을 본인에게 직접(통화·대면 등 채팅 밖 경로로) 확인한 뒤 그 사람 파일에 넣는다.

```bash
sudo install -o root -g root -m 644 /dev/null /etc/ssh/ax-gate/<계정>
sudoedit /etc/ssh/ax-gate/<계정>
```

줄 형식(한 줄):

```
restrict,from="<허용 출처 IP>",command="/usr/local/sbin/ax-gate <계정>" ssh-ed25519 <공개키> <계정>
```

- `restrict`는 pty·포트 포워딩·에이전트 포워딩·X11·user rc를 한꺼번에 끈다.
- `command=`의 계정명은 그 파일의 계정과 같아야 한다. 다르면 게이트가 설정 오류로 거부한다. 게이트의 신원은 여기 적힌 이름이 아니라 실제 로그인 계정이다.
- 모든 줄에 `command=`와 `restrict`가 있어야 한다. 하나라도 빠진 줄은 그 사람에게 셸을 준다. 넣은 뒤 `grep -vc 'command="/usr/local/sbin/ax-gate ' /etc/ssh/ax-gate/<계정>`이 0인지 본다.
- 개인키는 받지도 보관하지도 않는다. 실제 키 값과 주소를 레포에 넣지 않는다.
- 키 회수: 그 줄을 지운다. 즉시 효력이 난다.

## 6. 방화벽에 허용 출처 추가

서버 방화벽이 SSH를 정해진 출처에서만 받는다면, 팀원 PC의 주소를 허용 출처에 더한다. 방화벽 종류와 규칙 파일의 모양은 서버마다 다르므로 구체 절차는 비공개 문서에 둔다. 일반 순서는 다음과 같다.

1. 규칙 파일의 사본을 떠 둔다(되돌릴 용도).
2. SSH 허용 출처 목록에 `<허용 출처 IP>`를 더한다. 기존 항목은 지우지 않는다.
3. 적용하지 않고 문법만 검사한다(방화벽 도구의 검사 옵션).
4. **지금 쓰는 관리 접속을 끊지 않은 채** 적용하고, 새 창에서 기존 접속이 되는지 먼저 확인한다. 안 되면 열려 있는 창에서 사본으로 되돌린다.
5. 재부팅 뒤에도 규칙이 유지되는지 확인한다.

방화벽(그 주소가 SSH에 닿는지)과 `from=`(그 계정의 키가 그 주소에서만 쓰이는지)은 둘 다 건다. 주소가 바뀌면 두 곳을 같이 고친다. SSH를 여는 것은 그 주소에서 다른 계정으로의 시도도 닿게 한다는 뜻이므로 꼭 필요한 주소만 넣는다.

## 7. nginx 삽입 표시 줄

`nginx-add-location`이 블록을 넣을 자리를 한 번 정해 둔다. 서버의 기존 변경 순서(백업 → 수정 → `nginx -t` → reload → 사본 갱신) 그대로 한다.

쓰기 location들이 있는 server 블록 안, **읽기용 location보다 앞**에 아래 한 줄을 넣는다. 파일 전체에 정확히 한 번만.

```
    # AX-GATE-INSERT-BELOW
```

이때 정책 `[location] block_lines`가 서버의 기존 쓰기 location과 같은 모양인지 실제 블록 하나와 대조한다. 다르면 설치를 멈추고 정책을 먼저 고친다.

## 8. 설치 후 검증 체크리스트

`<서버>`는 대상 서버 주소, `<계정>`은 시험하는 사람의 계정. 각 줄을 실제로 실행하고 결과를 적는다.

접속과 신원

- [ ] 등록한 PC에서 `ssh <계정>@<서버> status` → 잠금·health·번들·사본 줄이 나오고 끝 코드 0
- [ ] 비밀번호가 잠긴 계정인데도 공개키 로그인이 되는지(안 되면 sshd의 PAM 설정을 본다. 비밀번호를 만들어 해결하지 않는다)
- [ ] 등록 안 한 주소에서 같은 키로 접속 → 방화벽 또는 `from=`에서 막힌다
- [ ] `ssh <계정>@<서버>` (명령 없음) → 사용법만 나오고 끊긴다. 셸이 뜨지 않는다
- [ ] `ssh -t <계정>@<서버> status` → pty가 거부된다
- [ ] 포트 포워딩(`ssh -L ...`), `scp`, `sftp` → 안 된다
- [ ] `ssh <계정>@<서버> 'status; id'`, `'cat /etc/shadow'`, `'deploy-bundle $(id)'` → 전부 거부(끝 코드 2)
- [ ] 감사 로그의 `user=` 칸이 접속한 계정과 같은지. 두 사람이 각자 `status`를 실행해 서로 다른 이름으로 남는지
- [ ] 서버에서 `sudo -l -U <계정>` → `/usr/local/sbin/ax-gate ""` 한 줄뿐
- [ ] 서버에서 `sudo -u <계정> sudo -n /usr/local/sbin/ax-gate status` → 거부(인자 있는 호출은 sudoers에 없다)
- [ ] 서버에서 `printf '0\n1\n-\nstatus\n' | sudo -u <계정> sudo -n /usr/local/sbin/ax-gate` → 실행되고 감사 로그에 `user=<계정>`으로 남는다(sudo가 `SUDO_USER`·`SUDO_UID`를 넣는지 확인)
- [ ] root 셸에서 `printf '0\n1\n-\nstatus\n' | /usr/local/sbin/ax-gate` → `SUDO_USER`가 없어 거부
- [ ] 서버에서 그 계정으로 비밀값 파일을 읽어 본다 → 권한 없음. 그 밖에 어떤 파일이 읽히는지도 본다
- [ ] 일부러 키 파일의 `command=` 이름을 다른 계정으로 바꿔 접속 → "계정명이 로그인 계정과 다르다"로 거부되고 감사 로그에 실제 계정 이름으로 남는다(확인 뒤 되돌린다)

권한표와 잠금

- [ ] 권한자가 아닌 계정으로 `restart-backend <SHA> --ack-data-loss --announced` → 끝 코드 3
- [ ] 권한자가 아닌 계정으로 `deploy-bundle <SHA> --dry-run` → 할 일만 출력, 끝 코드 0
- [ ] 권한자 계정으로 `lock test` → `status`에 잠금 표시 → 다른 계정의 `deploy-bundle <SHA> --dry-run` → 끝 코드 4 → `unlock`
- [ ] 잠금 파일이 root 소유 644인지, 개인 계정으로는 못 지우는지
- [ ] 재부팅 뒤에도 잠금 파일·방화벽 규칙·키 파일이 유지되는지

SHA·배포(먼저 `--dry-run`으로)

- [ ] origin에 없는 SHA, 배포 기준 브랜치에 없는 SHA → 거부
- [ ] `deploy-bundle <현재 배포된 SHA> --dry-run`의 출력에서 배포 스크립트에 넘기는 환경변수가 평소 수동 배포와 같은지 대조
- [ ] 배포 계정으로 `runuser`를 거쳐 돌 때 빌드 도구가 게이트의 고정 PATH에서 잡히는지
- [ ] 지금 서버 HEAD보다 이전 커밋으로 `deploy-bundle <이전 SHA> --dry-run` → "앞선 커밋이 아니다"로 거부
- [ ] 여유 있는 시간에 `deploy-bundle <SHA> --announced` 실제 1회: 번들 백업, 직전 배포 해시 기록, 번들 점검 통과
- [ ] `rollback-bundle <SHA> --announced` 실제 1회: 번들이 이전 것으로 돌아오는지

nginx

- [ ] `nginx-restore <7단계에서 뜬 백업 폴더> --dry-run` → 그다음 실제 1회 → 표시 줄이 사라진 상태로 돌아가고 reload되는지 → 다시 `nginx-restore pre-gate-restore-<시각>`으로 원래대로
- [ ] 시험용 제안: 권한자 A가 아직 열려 있지 않은 시험 경로를 제안 → A 본인의 승인 시도 거부(끝 코드 3) → 권한자 B가 `status`로 확인값 확인 → 여기서는 승인하지 않고 만료되게 둔다(실제 경로를 열지 않는다)
- [ ] 와일드카드(`.*`), 끝이 안 고정된 정규식, 허용 접두 밖 경로, GET → 제안 단계에서 거부
- [ ] `status`의 사본 비교 줄이 "일치"인지, 손으로 한 글자 바꾸면 "어긋남"이 되는지(바꾼 뒤 되돌린다)

감사 로그

- [ ] 위 시도가 전부 감사 로그에 계정·동작·결과와 함께 남았는지, 거부된 원문 명령과 비밀값이 없는지
- [ ] 개인 계정으로 감사 로그를 비우는 시도 → 실패(추가 전용)
- [ ] `journalctl -t ax-gate`에도 같은 줄이 있는지

재시작(지워지는 상태가 있으므로 꼭 필요할 때 1회만)

- [ ] `restart-backend <서버 HEAD SHA> --ack-data-loss --dry-run`의 백업 대상이 맞는지
- [ ] 실제 재시작은 다음에 재시작이 필요한 때 게이트로 해 보고 걸린 시간과 health 결과를 적는다

## 9. 사람을 빼거나 바꿀 때

한 사람 빼기(위에서부터. 첫 줄만으로 접속이 막힌다)

```bash
sudo rm /etc/ssh/ax-gate/<계정>              # 1) 키 회수(즉시)
sudo gpasswd -d <계정> axgate                # 2) sudo·감사 로그 쓰기 권한 회수
sudo usermod -L -e 1 <계정>                  # 3) 계정 만료(기록의 이름을 남기려고 계정은 지우지 않는다)
sudoedit /etc/ax-gate/policy.toml            # 4) 권한자였다면 [permissions]·[location_prefixes] 에서 지운다
# 5) 방화벽의 허용 출처에서 그 사람 주소를 뺀다(6단계와 같은 순서)
```

PC 주소가 바뀌었을 때: 키 파일의 `from=`과 방화벽 허용 출처를 같이 고친다. 키를 잃어버렸을 때: 그 줄을 바로 지우고 새 공개키를 다시 받는다.

## 10. 전체 되돌리기(게이트 제거)

게이트는 기존 관리 접속·배포 방식에 손대지 않으므로, 아래를 지우면 설치 전과 같다. 위에서부터 순서대로 하면 접속이 먼저 닫힌다.

```bash
sudo rm -r /etc/ssh/ax-gate                              # 1) 모든 개인 계정의 키 회수(즉시)
sudo rm /etc/ssh/sshd_config.d/90-ax-gate.conf && sudo sshd -t && sudo systemctl reload ssh
sudo rm /etc/sudoers.d/ax-gate && sudo visudo -c         # 2) sudo 권한 회수
for u in <계정 목록>; do sudo gpasswd -d "$u" axgate; sudo usermod -L -e 1 "$u"; done   # 3) 계정 만료
sudo groupdel axgate
# 4) 방화벽을 6단계에서 떠 둔 사본으로 되돌린다(검사 → 적용 → 확인)
sudo rm /usr/local/sbin/ax-gate /usr/local/sbin/ax-gate-location-check
sudo rm -r /etc/ax-gate                                  # 정책과 남아 있는 잠금 파일
# 감사 로그와 승인 기록은 지우지 않고 남긴다: /var/log/ax-gate, /var/lib/ax-gate
# 계정은 만료만 하고 지우지 않는다(감사 로그의 이름이 남도록)
# nginx 표시 줄은 주석이라 남겨도 동작에 영향이 없다
```

급할 때는 1)만 해도 게이트로는 아무도 못 들어온다. 게이트가 바꾼 nginx 설정을 되돌리려면 백업 폴더의 `pre-gate-*` 사본을 서버의 기존 복원 순서로 되돌린다.

## 11. 설치 뒤 운영

- 정책을 바꿀 때: `sudoedit /etc/ax-gate/policy.toml`. 문법이 틀리면 게이트가 전부 거부하므로 바꾼 뒤 `status`로 확인한다.
- 게이트 스크립트를 바꿀 때: 레포 PR → 머지 → 1단계의 `install` 두 줄을 다시 실행. 서버의 사본을 직접 고치지 않는다.
- `ANNOUNCE.md`를 팀에 보내는 것은 설치와 검증이 끝난 뒤 서버 책임자가 한다.
- 코드(레포)를 이전 커밋으로 되돌리는 것은 게이트에 없다. `rollback-bundle`이 알려 주는 직전 배포 해시를 보고 서버 책임자가 직접 한다.
- 서버의 파일 권한 점검(개인 계정이 늘어난 만큼 다른 계정이 읽을 수 있는 파일을 줄일지)은 게이트 설치와 별도의 결정이다.
