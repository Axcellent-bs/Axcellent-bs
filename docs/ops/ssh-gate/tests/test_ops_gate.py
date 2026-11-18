"""ops-gate.sh 로컬 테스트: 인자 파싱·신원·허용표·잠금·DRY_RUN·감사 로그 (서버 없이).

값은 전부 가짜 예시다(계정 owner-a, 서비스 example-backend, 포트 8201 등). 실제 서버 값은 여기 없다.

실제 systemctl·nginx·sudo·git·curl 은 부르지 않는다. 임시 폴더의 가짜 명령(fakebin)이 대신
불리고, 불린 내용은 기록 파일에 남는다. 게이트는 OPS_GATE_TEST_ROOT 로 모든 경로를 임시 폴더로 돌린다.

실행: python -m unittest discover -s docs/ops/ssh-gate/tests -v   (bash 가 있어야 한다)
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE.parent / "ops-gate.sh"
EXAMPLE_POLICY = HERE.parent / "policy.example.toml"

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"       # origin/dev 에 있음
SHA_LOCAL = "b" * 40                                    # 서버 레포에만 있고 origin 에 없음
SHA_FEATURE = "c" * 40                                  # origin 에 있지만 dev 에는 없음
SHA_OLD = "e" * 40                                      # dev 에 있지만 지금 서버 코드보다 뒤(이전) 커밋
SECRET = "sk-test-FAKE-0123456789abcdef"                # 테스트용 가짜 값
HUMAN = "front-FAKE-token-0123456789"                   # 번들에 들어가면 안 되는 값인 척하는 가짜 값
VIEWER = "viewer-FAKE-token-0123456789"                 # 일부러 번들에 들어가는 값인 척하는 가짜 값
HEALTH_N = 3                                            # 테스트 정책의 health 주소 개수

ORIG_CONF = """server {
    listen 8300;
    # AX-GATE-INSERT-BELOW
    location /example-api/ {
        limit_except GET HEAD { deny all; }
    }
}
"""
GOOD_REGEX = "^/example-api/api/items/cards/[^/]+/decision$"


def find_bash():
    cand = os.environ.get("OPS_GATE_TEST_BASH") or shutil.which("bash")
    if cand and "system32" in cand.lower():  # WSL 실행기는 쓰지 않는다
        cand = None
    return cand


BASH = find_bash()

FAKES = {
    # 진짜 sudo 처럼 SUDO_USER·SUDO_UID 를 부른 계정으로 덮어쓴다(부르는 쪽이 넣은 값은 버린다)
    "sudo": ('echo "sudo $*" >> "$FAKE_LOG"\nwhile [[ "${1:-}" == -* ]]; do shift; done\n'
             'SUDO_USER="$(id -un)"\nSUDO_UID=1000\nexport SUDO_USER SUDO_UID\nexec "$@"\n'),
    # 로그인 계정 흉내: id -un → $FAKE_USER, id -u <이름> → 1000 (없는 계정 "ghost" 는 실패)
    "id": ('case "$1" in\n  -un) echo "$FAKE_USER" ;;\n'
           '  -u) [[ "${3:-}" == "ghost" ]] && exit 1; echo 1000 ;;\n  *) exit 1 ;;\nesac\n'),
    # runuser -u <계정> -- 명령...: 어느 계정으로 돌렸는지 남기고 명령을 그대로 실행한다
    "runuser": 'echo "runuser as=$2" >> "$FAKE_LOG"\nshift 3\nexec "$@"\n',
    "git": r"""echo "git $*" >> "$FAKE_LOG"
[[ "$1" == "-C" ]] && shift 2
case "$1" in
  fetch) exit 0 ;;
  rev-parse)
    if [[ "$2" == "HEAD" ]]; then cat "$FAKE_STATE/git_head"; exit 0; fi
    want="${4%%^*}"
    while read -r full; do
      [[ "$full" == "$want"* ]] && { echo "$full"; exit 0; }
    done < "$FAKE_STATE/git_known"
    exit 1 ;;
  branch) grep -qx "$4" "$FAKE_STATE/git_on_origin" && echo "  origin/somewhere"; exit 0 ;;
  merge-base)
    # state/git_pairs 가 있으면 "조상 후손" 줄로만 판정한다(앞선/뒤처진 방향 시험용)
    if [[ "$4" != origin/* && -f "$FAKE_STATE/git_pairs" ]]; then
      grep -qx "$3 $4" "$FAKE_STATE/git_pairs"; exit $?
    fi
    if [[ "$4" != origin/* ]]; then   # HEAD 가 그 커밋의 조상인가(앞으로 감기 가능한가)
      grep -qx "$4" "$FAKE_STATE/git_behind_head" 2>/dev/null && exit 1
      exit 0
    fi
    grep -qx "$3" "$FAKE_STATE/git_on_${4#origin/}"; exit $? ;;
  status) cat "$FAKE_STATE/git_dirty" 2>/dev/null; exit 0 ;;
esac
exit 1
""",
    # state/systemctl_rc 가 있으면 그 끝 코드로 끝난다(reload 실패 시험용)
    "systemctl": ('echo "systemctl $*" >> "$FAKE_LOG"\n'
                  'exit "$(cat "$FAKE_STATE/systemctl_rc" 2>/dev/null || echo 0)"\n'),
    # 진짜 cp 를 부른다. state/cp_fail_dest 에 적힌 곳으로의 복사만 한 번 실패한다(교체 도중 실패 시험용)
    "cp": ('for last; do :; done\n'
           'if [[ -f "$FAKE_STATE/cp_fail_dest" && "$last" == "$(cat "$FAKE_STATE/cp_fail_dest")" ]]; then\n'
           '  rm -f "$FAKE_STATE/cp_fail_dest"\n  echo "cp FAILED" >> "$FAKE_STATE/cp_failures"\n  exit 1\nfi\n'
           'while IFS= read -r real; do\n'
           '  [[ "$(dirname "$real")" != "$(dirname "$0")" ]] && exec "$real" "$@"\n'
           'done < <(type -aP cp)\nexit 127\n'),
    "nginx": 'echo "nginx $*" >> "$FAKE_LOG"\nexit "$(cat "$FAKE_STATE/nginx_rc" 2>/dev/null || echo 0)"\n',
    # 주소별 응답 코드: state/curl_code_<포트> 가 있으면 그 값, 없으면 state/curl_code, 그것도 없으면 200
    "curl": ('url="${@: -1}"\nport="${url##*:}"\nport="${port%%/*}"\necho "curl $url" >> "$FAKE_LOG"\n'
             'if [[ -f "$FAKE_STATE/curl_code_$port" ]]; then printf "%s" "$(cat "$FAKE_STATE/curl_code_$port")"\n'
             'else printf "%s" "$(cat "$FAKE_STATE/curl_code" 2>/dev/null || echo 200)"\nfi\n'),
    # 게이트웨이 반영 명령(가짜). state/gw_reload_fail_once 가 있으면 한 번만 실패, state/gw_reload_rc 가 있으면 그 끝 코드
    "gw-reload": ('echo "gw-reload $*" >> "$FAKE_LOG"\n'
                  'if [[ -f "$FAKE_STATE/gw_reload_fail_once" ]]; then rm -f "$FAKE_STATE/gw_reload_fail_once"; exit 1; fi\n'
                  'exit "$(cat "$FAKE_STATE/gw_reload_rc" 2>/dev/null || echo 0)"\n'),
    "journalctl": 'echo "journalctl $*" >> "$FAKE_LOG"\nexit 0\n',
    "chown": "exit 0\n",
    "python3": ('script="$1"\nshift\n'
                'if [[ -f "$script" ]] && command -v cygpath >/dev/null 2>&1; then\n'
                '  script="$(cygpath -m "$script")"\nfi\n'
                'export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"\nexec "@PYTHON@" "$script" "$@"\n'),
}

# 가짜 배포 스크립트: 받은 환경변수를 기록만 한다
DEPLOY_SH = """#!/usr/bin/env bash
echo "deploy.sh REF=${DEPLOY_REF:-} MARK=${APP_PUBLIC_MARK:-} ROOT=${DEPLOY_WEB_ROOT:-} URL=${DEPLOY_CHECK_URL:-} SITE=${APP_SITE_URL:-}" >> "$FAKE_LOG"
# state/deploy_moves_head 가 있으면 진짜 배포처럼 서버 레포 HEAD 를 그 커밋으로 옮긴다
if [[ -f "$FAKE_STATE/deploy_moves_head" ]]; then echo "${DEPLOY_REF:-}" > "$FAKE_STATE/git_head"; fi
exit "$(cat "$FAKE_STATE/deploy_rc" 2>/dev/null || echo 0)"
"""

ALL = ('"status", "lock", "unlock", "nginx-restore", "nginx-add-location", "nginx-approve-location", '
       '"deploy-bundle", "restart-backend", "rollback-bundle", "record-pr"')


def policy_text(root, auto_expire="true", extra=""):
    return f"""# 테스트용 정책(가짜 값)
[gate]
label = "test-server"

[permissions]
owner-a = [{ALL}]
owner-b = [{ALL}]
owner-c = [{ALL}]
restore-only = ["status", "nginx-restore", "nginx-approve-location"]
_default = ["status", "deploy-bundle", "record-pr"]

[location_prefixes]
owner-b = ["/example-api/api/items/cards/"]
owner-a = ["/example-api/api/items/", "/other-api/v1/things/"]
owner-c = ["/example-api/api/items/"]

[location_upstreams]
example-api = 8201   # 주석
other-api = 8202

[location]
allowed_methods = ["POST"]
proposal_ttl_hours = 24
block_lines = ["limit_except {{method}} {{ deny all; }}", "rewrite ^/{{segment}}/(.*)$ /$1 break;", "proxy_pass http://127.0.0.1:{{port}};", "include snippets/example-proxy.conf;", "include snippets/example-auth.conf;"]

[lock]
default_hours = 4
max_hours = 24
auto_expire = {auto_expire}

[deploy]
user = "deploy"
allowed_branches = ["main", "dev"]
ref_env = "DEPLOY_REF"
env = ["APP_PUBLIC_MARK=placeholder-a", "APP_SITE_URL=http://site.test:8080"]
env_b = ["DEPLOY_WEB_ROOT={root}/var/www/site-b", "DEPLOY_CHECK_URL=http://localhost:8300/"]
required_strings = ["placeholder-a", "placeholder-b"]
secret_env_files = ["{root}/data/secrets.env", "{root}/opt/app/repo/backend/.env", "{root}/data/front.env"]
bundle_allowed_env_names = ["APP_VIEWER_TOKEN"]
check_url = "http://127.0.0.1:8300/"

[restart]
unit = "example-backend"
health_timeout_seconds = 0
poll_seconds = 0
backup_sqlite = ["{root}/data/app.db"]
backup_files = ["{root}/data/secrets.env", "{root}/data/state.json", "{root}/opt/app/repo/backend/.env"]
backup_dirs = ["{root}/data/data-dir"]

[health]
urls = ["http://127.0.0.1:8201/health", "http://127.0.0.1:8202/health", "http://127.0.0.1:8203/health"]

[status]
urls = ["http://127.0.0.1:8300/", "http://127.0.0.1:8301/extra"]

[paths]
repo = "{root}/opt/app/repo"
deploy_script = "{root}/opt/app/deploy.sh"
web_root_parent = "{root}/var/www"
web_root_a = "{root}/var/www/site-a"
web_root_b = "{root}/var/www/site-b"
backup_dir = "{root}/backups"
nginx_conf = "{root}/etc/nginx/conf.d/site.conf"
nginx_backup_dir = "{root}/etc/nginx/backup"
ops_copy_file = "{root}/opt/app/ops-copies/site.conf"
{extra}"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


@unittest.skipUnless(BASH, "bash 가 없어 게이트 테스트를 건너뛴다")
class GateCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="axgate")
        self.addCleanup(self._tmp.cleanup)
        self.rootp = Path(self._tmp.name).resolve()
        self.root = self.rootp.as_posix()
        r = self.rootp
        for d in ("etc/ax-gate", "var/log/ax-gate", "run/lock", "etc/nginx/backup", "backups",
                  "opt/app/repo/backend", "opt/app/ops-copies", "data/data-dir", "state",
                  "var/lib/ax-gate/pending", "var/lib/ax-gate/approved"):
            (r / d).mkdir(parents=True)
        self.policy = r / "etc/ax-gate/policy.toml"
        write(self.policy, policy_text(self.root))
        self.lock = r / "etc/ax-gate/deploy.lock"
        self.audit = r / "var/log/ax-gate/audit.log"
        self.fakelog = r / "state/calls.log"
        self.state = r / "state"
        for name, body in FAKES.items():
            body = body.replace("@PYTHON@", Path(sys.executable).as_posix())
            write(r / "fakebin" / name, "#!/usr/bin/env bash\n" + body)
            os.chmod(r / "fakebin" / name, 0o755)
        write(r / "opt/app/deploy.sh", DEPLOY_SH)
        os.chmod(r / "opt/app/deploy.sh", 0o755)
        self.conf = r / "etc/nginx/conf.d/site.conf"
        write(self.conf, ORIG_CONF)
        write(r / "opt/app/ops-copies/site.conf", ORIG_CONF)
        for web in ("site-a", "site-b"):
            write(r / "var/www" / web / "assets/app.js",
                  f'const t="placeholder-a",d="placeholder-b",v="{VIEWER}";\n')
        db = sqlite3.connect(str(r / "data/app.db"))
        db.execute("create table items (id integer primary key, note text)")
        db.execute("insert into items (note) values ('row-1')")
        db.commit()
        db.close()
        write(r / "data/front.env", f"APP_FRONT_TOKEN={HUMAN}\n")
        write(r / "data/secrets.env", f"SOME_API_TOKEN={SECRET}\nSHORT=abc\n")
        write(r / "data/state.json", "{}")
        (r / "data/data-dir").mkdir(parents=True, exist_ok=True)
        ddb = sqlite3.connect(str(r / "data/data-dir/x.db"))
        ddb.execute("create table t (v text)")
        ddb.execute("insert into t values ('dir-row')")
        ddb.commit()
        ddb.close()
        write(r / "data/data-dir/keep.txt", "keep")
        write(r / "opt/app/repo/backend/.env",
              f"APP_PUBLIC_MARK=placeholder-a\nAPP_VIEWER_TOKEN={VIEWER}\n")
        write(self.state / "git_known", f"{SHA}\n{SHA_LOCAL}\n{SHA_FEATURE}\n{SHA_OLD}\n")
        write(self.state / "git_on_origin", f"{SHA}\n{SHA_FEATURE}\n{SHA_OLD}\n")
        write(self.state / "git_on_dev", f"{SHA}\n{SHA_OLD}\n")
        write(self.state / "git_behind_head", f"{SHA_OLD}\n")
        write(self.state / "git_on_main", "")
        write(self.state / "git_head", f"{SHA}\n")
        write(self.fakelog, "")

    # --- 도구 ---
    def env(self, **extra):
        env = dict(os.environ)
        env.pop("SSH_CONNECTION", None)
        env.pop("DRY_RUN", None)
        env["PATH"] = str(self.rootp / "fakebin") + os.pathsep + env["PATH"]
        env["OPS_GATE_TEST_ROOT"] = self.root
        env["FAKE_LOG"] = self.fakelog.as_posix()
        env["FAKE_STATE"] = self.state.as_posix()
        env.pop("SUDO_USER", None)
        env.pop("SUDO_UID", None)
        env.update(extra)
        return env

    def gate(self, key, command, login=None, **extra_env):
        """key = authorized_keys 의 command= 에 적힌 계정명, login = 실제 로그인 계정(기본은 같음)."""
        env = self.env(FAKE_USER=login or key, **extra_env)
        if command is not None:
            env["SSH_ORIGINAL_COMMAND"] = command
        else:
            env.pop("SSH_ORIGINAL_COMMAND", None)
        return subprocess.run([BASH, GATE.as_posix(), key], env=env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def root_step(self, lines, **extra_env):
        """앞단을 건너뛰고 뒷단을 직접 부른다.

        lines[0] 은 sudo 가 넣어 주는 SUDO_USER(신뢰 근거)이고, 나머지 [dry, announced, pr, 동작, 인자...]
        가 표준 입력으로 가는 요청이다. lines[0] 이 None 이면 SUDO_USER 없이 부른다.
        """
        env = self.env(FAKE_USER="root", **extra_env)
        if lines[0] is not None:
            env.setdefault("SUDO_USER", lines[0])
            env.setdefault("SUDO_UID", "1000")
        p = subprocess.run([BASH, GATE.as_posix()], env=env, input=("\n".join(lines[1:]) + "\n").encode(),
                           capture_output=True, timeout=120)
        p.stdout = p.stdout.decode("utf-8", "replace")
        p.stderr = p.stderr.decode("utf-8", "replace")
        return p

    def propose(self, key="owner-b", name="card-decision", regex=GOOD_REGEX):
        """제안을 넣고 확인값(12자)을 돌려준다."""
        p = self.gate(key, f"nginx-add-location {name} POST {regex}")
        self.assertRc(p, 0)
        digest = [ln.split()[-1] for ln in p.stdout.splitlines() if ln.strip().startswith("확인값")][0]
        self.assertRegex(digest, r"^[0-9a-f]{12}$")
        return digest

    def calls(self):
        return self.fakelog.read_text(encoding="utf-8")

    def audit_text(self):
        return self.audit.read_text(encoding="utf-8") if self.audit.exists() else ""

    def assertRc(self, proc, rc):
        self.assertEqual(proc.returncode, rc, f"stdout={proc.stdout!r} stderr={proc.stderr!r}")


class ParsingTests(GateCase):
    def test_no_command_is_refused(self):
        p = self.gate("owner-a", None)
        self.assertRc(p, 2)
        self.assertIn("result=denied:no-command", self.audit_text())

    def test_unknown_action_and_raw_command_not_logged(self):
        p = self.gate("owner-a", "cat /etc/shadow")
        self.assertRc(p, 2)
        self.assertIn("result=denied:unknown-action", self.audit_text())
        self.assertNotIn("/etc/shadow", self.audit_text())
        self.assertEqual(self.calls(), "")

    def test_shell_injection_attempts_never_reach_sudo(self):
        marker = self.rootp / "pwned"
        m = marker.as_posix()
        attempts = [
            f"status; touch {m}",
            f"status && touch {m}",
            f"status | touch {m}",
            f"status $(touch {m})",
            f"status `touch {m}`",
            f"deploy-bundle {SHA[:8]};touch${{IFS}}{m} --announced",
            f"deploy-bundle $(touch {m}) --announced",
            f"lock x 2099-01-01T00:00;touch {m}",
            f"nginx-restore ../../{marker.name} --announced",
            f"record-pr x 12;touch {m}",
            f"deploy-bundle {SHA[:8]} --announced pr=12;touch",
            "status\nunlock",
            "status\tunlock",
            "lock 잠금",
            f"unlock > {m}",
            "status " + "a" * 600,
        ]
        for cmd in attempts:
            with self.subTest(cmd=cmd):
                p = self.gate("owner-a", cmd)
                self.assertRc(p, 2)
                self.assertFalse(marker.exists())
        self.assertEqual(self.calls(), "")
        self.assertEqual(self.audit_text().count("result=denied:"), len(attempts))

    def test_sha_must_be_lowercase_hex_7_to_40(self):
        for sha in ("abc123", "ABCDEF12", "g1b2c3d4", "a" * 41, "HEAD", "origin/dev", "-rf", "a1b2c3d~1"):
            with self.subTest(sha=sha):
                for action in ("deploy-bundle", "rollback-bundle"):
                    self.assertRc(self.gate("owner-a", f"{action} {sha} --announced"), 2)
                self.assertRc(self.gate("owner-a", f"restart-backend {sha} --ack-data-loss --announced"), 2)
        self.assertEqual(self.calls(), "")

    def test_restart_requires_ack_flag(self):
        self.assertRc(self.gate("owner-a", f"restart-backend {SHA} --announced"), 2)
        p = self.gate("owner-a", f"restart-backend {SHA} --yes --announced")
        self.assertRc(p, 2)
        self.assertIn("--ack-data-loss", p.stderr)
        self.assertIn("result=denied:no-ack", self.audit_text())
        self.assertNotIn("systemctl", self.calls())

    def test_arg_counts(self):
        for cmd in ("status now", "unlock x", "lock", "nginx-restore", "nginx-restore a b",
                    "deploy-bundle", f"deploy-bundle {SHA} extra", "nginx-add-location n POST",
                    "nginx-approve-location n", "nginx-approve-location n XYZ", "record-pr x",
                    "record-pr x abc", f"deploy-bundle {SHA} pr=abc", "--announced", "--dry-run status"):
            with self.subTest(cmd=cmd):
                self.assertRc(self.gate("owner-a", cmd), 2)

    def test_bad_key_name_in_authorized_keys(self):
        self.assertRc(self.gate("Bad Key", "status"), 5)
        self.assertRc(self.gate("../x", "status"), 5)


class PermissionTests(GateCase):
    def test_default_key_can_deploy_by_hand_but_nothing_else(self):
        self.assertRc(self.gate("member-x", "status"), 0)
        for cmd in ("lock x", "unlock", "nginx-restore pre-x --announced",
                    f"restart-backend {SHA} --ack-data-loss --announced", f"rollback-bundle {SHA} --announced",
                    f"nginx-add-location n POST {GOOD_REGEX}", "nginx-approve-location n 0123456789ab --announced"):
            with self.subTest(cmd=cmd):
                self.assertRc(self.gate("member-x", cmd), 3)
        self.assertIn("user=member-x\taction=restart-backend", self.audit_text())
        self.assertIn("result=denied:not-permitted", self.audit_text())
        self.assertEqual(self.calls().count("deploy.sh"), 0)
        # 자동 배포가 막혔을 때의 수동 처리는 누구나 된다(기록은 같은 방식으로 남는다)
        self.assertRc(self.gate("member-x", f"deploy-bundle {SHA} --announced pr=963"), 0)
        self.assertEqual(self.calls().count("deploy.sh REF="), 2)
        self.assertIn("user=member-x\taction=deploy-bundle\targs=" + SHA + "\tmode=real\tannounced=yes\tpr=963",
                      self.audit_text())

    def test_three_owners_have_nginx_restart_rollback(self):
        for key in ("owner-a", "owner-b", "owner-c"):
            with self.subTest(key=key):
                # 권한 단계는 지나고 "백업 없음"에서 멈춘다
                self.assertRc(self.gate(key, "nginx-restore pre-none-20260101 --announced"), 2)
                self.assertRc(self.gate(key, f"rollback-bundle {SHA} --announced"), 1)
                self.assertRc(self.gate(key, "lock demo --dry-run"), 0)
        self.assertEqual(self.audit_text().count("result=fail:backup-not-found"), 6)
        self.assertNotIn("not-permitted", self.audit_text())

    def test_root_layer_rechecks_everything_itself(self):
        # 앞단을 건너뛰고 뒷단을 직접 불러도 허용표·인자·알림 확인이 다시 걸린다
        self.assertRc(self.root_step(["member-x", "0", "1", "-", "restart-backend", SHA, "--ack-data-loss"]), 3)
        self.assertRc(self.root_step(["owner-a", "0", "1", "-", "deploy-bundle", "HEAD"]), 2)
        self.assertRc(self.root_step(["owner-a", "0", "1", "-", "nginx-restore", "a b"]), 2)
        self.assertRc(self.root_step(["owner-a", "0", "1", "-", "rm"]), 2)
        self.assertRc(self.root_step(["owner-a", "2", "1", "-", "status"]), 2)
        self.assertRc(self.root_step(["owner-a", "0", "x", "-", "status"]), 2)
        self.assertRc(self.root_step(["owner-a", "0", "1", "12;id", "status"]), 2)
        self.assertRc(self.root_step(["Bad", "0", "1", "-", "status"]), 5)
        self.assertRc(self.root_step(["owner-a", "0", "1", "-"]), 2)
        self.assertRc(self.root_step(["owner-a", "0", "1", "-", "status"] + ["x"] * 6), 2)
        self.assertRc(self.root_step(["owner-a", "0", "0", "-", "deploy-bundle", SHA]), 2)   # 알림 확인 없음
        self.assertNotIn("deploy.sh", self.calls())
        self.assertIn("layer=root\tresult=denied:not-permitted", self.audit_text())
        self.assertIn("layer=root\tresult=denied:not-announced", self.audit_text())

    def test_broken_policy_refuses_everything(self):
        write(self.policy, policy_text(self.root) + "\nthis line is not toml subset\n")
        self.assertRc(self.gate("owner-a", "status"), 5)
        write(self.policy, policy_text(self.root, extra='repo = "/dup"'))
        self.assertRc(self.gate("owner-a", "status"), 5)
        self.policy.unlink()
        self.assertRc(self.gate("owner-a", "status"), 5)
        self.assertNotIn("sudo", self.calls())

    def test_test_mode_is_ignored_under_ssh(self):
        # SSH 접속 중이면 OPS_GATE_TEST_ROOT 를 무시한다(운영 경로를 본다 → 여기서는 정책 없음)
        p = self.gate("owner-a", "status", SSH_CONNECTION="198.51.100.7 5555 198.51.100.8 22")
        self.assertRc(p, 5)
        self.assertEqual(self.audit_text(), "")
        self.assertEqual(self.calls(), "")


class IdentityTests(GateCase):
    """신원은 OS 계정이다: 앞단은 로그인 계정, 뒷단은 SUDO_USER 만 믿는다."""

    RESTART = f"restart-backend {SHA} --ack-data-loss --announced"

    def test_name_in_authorized_keys_must_match_login_account(self):
        # member-x 계정의 authorized_keys 에 권한자 이름을 적어 놓아도 권한자가 되지 않는다
        p = self.gate("owner-a", self.RESTART, login="member-x")
        self.assertRc(p, 5)
        self.assertIn("user=member-x\taction=-", self.audit_text())
        self.assertIn("result=denied:account-mismatch", self.audit_text())
        self.assertNotIn("user=owner-a", self.audit_text())
        self.assertEqual(self.calls(), "")

    def test_front_ignores_sudo_user_set_by_caller(self):
        # 부르는 쪽이 환경변수로 SUDO_USER 를 넣어도 앞단은 로그인 계정을, 뒷단은 sudo 가 덮어쓴 값을 쓴다
        p = self.gate("member-x", self.RESTART, SUDO_USER="owner-a", SUDO_UID="1000")
        self.assertRc(p, 3)
        self.assertIn("user=member-x\taction=restart-backend", self.audit_text())
        self.assertNotIn("user=owner-a", self.audit_text())
        self.assertNotIn("systemctl", self.calls())
        # 권한이 있는 동작은 자기 이름으로 기록된다
        self.assertRc(self.gate("member-x", "status", SUDO_USER="owner-a"), 0)
        self.assertIn("user=member-x\taction=status", self.audit_text())
        self.assertNotIn("user=owner-a", self.audit_text())

    def test_root_layer_refuses_without_sudo_user(self):
        for su in (None, "root", "Bad Name", "", "../x"):
            with self.subTest(sudo_user=su):
                lines = [su, "0", "1", "-", "status"]
                if su == "":
                    p = self.root_step([None] + lines[1:], SUDO_USER="")
                else:
                    p = self.root_step(lines)
                self.assertRc(p, 5)
                self.assertEqual(p.stdout, "")
        self.assertEqual(self.audit_text(), "")
        self.assertEqual(self.calls(), "")

    def test_root_layer_refuses_unknown_account_or_uid_mismatch(self):
        self.assertRc(self.root_step(["ghost", "0", "1", "-", "status"]), 5)             # 없는 계정
        self.assertRc(self.root_step(["member-x", "0", "1", "-", "status"], SUDO_UID="0"), 5)
        self.assertRc(self.root_step(["member-x", "0", "1", "-", "status"], SUDO_UID=""), 5)
        self.assertEqual(self.audit_text(), "")

    def test_name_written_in_request_is_never_used(self):
        # 요청에 권한자 이름을 적어 보내도 신원은 SUDO_USER(member-x)다
        for req in (["owner-a", "0", "1", "-", "restart-backend", SHA, "--ack-data-loss"],
                    ["0", "1", "-", "restart-backend", SHA, "--ack-data-loss", "owner-a"],
                    ["0", "1", "-", "restart-backend", SHA, "--ack-data-loss", "user=owner-a"],
                    ["0", "1", "-", "owner-a", "restart-backend", SHA, "--ack-data-loss"]):
            with self.subTest(req=req):
                p = self.root_step(["member-x"] + req)
                self.assertIn(p.returncode, (2, 3))
        self.assertRc(self.root_step(["member-x", "0", "1", "-", "restart-backend", SHA, "--ack-data-loss"]), 3)
        audit = self.audit_text()
        self.assertEqual(audit.count("user=member-x\t"), 5)
        self.assertNotIn("user=owner-a", audit)
        self.assertNotIn("result=start", audit)
        self.assertNotIn("systemctl", self.calls())

    def test_same_request_runs_under_the_sudo_user_identity(self):
        req = ["0", "1", "-", "restart-backend", SHA, "--ack-data-loss"]
        self.assertRc(self.root_step(["owner-b"] + req), 0)
        self.assertIn("user=owner-b\taction=restart-backend", self.audit_text())
        self.assertIn("systemctl restart example-backend", self.calls())


class AnnounceAndPrTests(GateCase):
    def test_changing_actions_need_announced_and_print_notice_text(self):
        write(self.rootp / "etc/nginx/backup/pre-old/site.conf", "# old\n")
        for cmd, undo in ((f"deploy-bundle {SHA[:8]}", f"rollback-bundle {SHA[:8]}"),
                          (f"restart-backend {SHA} --ack-data-loss", "되돌릴 수 없음"),
                          (f"rollback-bundle {SHA}", "서버의 www-*.bak-before-rollback"),
                          ("nginx-restore pre-old", "nginx-restore pre-gate-restore-"),
                          ("nginx-approve-location card 0123456789ab", "nginx-restore pre-gate-add-card-")):
            with self.subTest(cmd=cmd):
                p = self.gate("owner-a", cmd)
                self.assertRc(p, 2)
                self.assertIn("팀 채널", p.stderr)
                self.assertIn("[test-server] owner-a: " + cmd.split()[0], p.stderr)
                self.assertIn("되돌리는 방법: " + undo, p.stderr)
        self.assertEqual(self.audit_text().count("result=denied:not-announced"), 5)
        self.assertEqual(self.calls(), "")
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)

    def test_announced_and_pr_are_recorded_in_any_order(self):
        self.assertRc(self.gate("owner-b", f"deploy-bundle {SHA} pr=963 --announced"), 0)
        self.assertRc(self.gate("owner-b", f"deploy-bundle {SHA} --announced pr=964"), 0)
        audit = self.audit_text()
        self.assertIn("announced=yes\tpr=963\tnote=-\tlayer=root\tresult=start", audit)
        self.assertIn("announced=yes\tpr=964\tnote=-\tlayer=root\tresult=end:rc=0", audit)

    def test_record_pr_writes_audit_line_only(self):
        p = self.gate("member-x", "record-pr pre-gate-add-card-20261111-101500 970")
        self.assertRc(p, 0)
        self.assertIn("user=member-x\taction=record-pr\targs=pre-gate-add-card-20261111-101500 970\tmode=real\t"
                      "announced=no\tpr=970\tnote=ref:pre-gate-add-card-20261111-101500\tlayer=root\tresult=start",
                      self.audit_text())
        self.assertTrue(all(ln.startswith("sudo -n -- bash ") for ln in self.calls().splitlines()))
        # 잠금 중에도 기록은 된다
        write(self.lock, "garbage\n")
        self.assertRc(self.gate("member-x", f"record-pr {SHA[:8]} 971"), 0)


class LockTests(GateCase):
    def write_lock(self, epoch, key="owner-b"):
        write(self.lock, f"user={key}\nreason=demo\nuntil=2026-01-01T00:00\nuntil_epoch={epoch}\ncreated=x\n")

    def test_lock_blocks_everything_but_status_unlock_record(self):
        p = self.gate("owner-b", "lock team-demo")
        self.assertRc(p, 0)
        text = self.lock.read_text(encoding="utf-8")
        self.assertIn("user=owner-b\nreason=team-demo\n", text)
        for cmd in (f"deploy-bundle {SHA} --announced", f"restart-backend {SHA} --ack-data-loss --announced",
                    f"rollback-bundle {SHA} --announced", "nginx-restore pre-x --announced",
                    f"nginx-add-location n POST {GOOD_REGEX}", "nginx-approve-location n 0123456789ab --announced",
                    "lock again", f"deploy-bundle {SHA} --dry-run"):
            with self.subTest(cmd=cmd):
                self.assertRc(self.gate("owner-a", cmd), 4)
        self.assertRc(self.gate("member-x", f"deploy-bundle {SHA} --announced"), 4)
        self.assertIn("result=denied:locked", self.audit_text())
        self.assertNotIn("deploy.sh", self.calls())
        p = self.gate("member-x", "status")
        self.assertRc(p, 0)
        self.assertIn("team-demo", p.stdout)
        self.assertRc(self.gate("member-x", "unlock"), 3)          # 잠금 해제는 권한자만
        self.assertRc(self.gate("owner-c", "unlock"), 0)
        self.assertFalse(self.lock.exists())
        self.assertRc(self.gate("member-x", f"deploy-bundle {SHA} --announced"), 0)

    def test_root_layer_also_checks_lock(self):
        self.write_lock(int(time.time()) + 3600)
        self.assertRc(self.root_step(["owner-a", "0", "1", "-", "deploy-bundle", SHA]), 4)
        self.assertNotIn("deploy.sh", self.calls())

    def test_until_validation(self):
        self.assertRc(self.gate("owner-a", "lock demo 2020-01-01T00:00"), 2)   # 과거
        self.assertRc(self.gate("owner-a", "lock demo 2099-01-01T00:00"), 2)   # 최대 시간 초과
        self.assertRc(self.gate("owner-a", "lock demo 2026-13-45T99:99"), 2)   # 날짜 아님
        self.assertRc(self.gate("owner-a", "lock demo tomorrow"), 2)
        self.assertRc(self.gate("owner-a", "lock two words"), 2)
        self.assertFalse(self.lock.exists())
        soon = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 2 * 3600))
        self.assertRc(self.gate("owner-a", f"lock demo {soon}"), 0)
        self.assertIn(f"until={soon}\n", self.lock.read_text(encoding="utf-8"))

    def test_expired_lock_with_auto_expire(self):
        self.write_lock(int(time.time()) - 60)
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --dry-run"), 0)

    def test_expired_lock_without_auto_expire_still_blocks(self):
        write(self.policy, policy_text(self.root, auto_expire="false"))
        self.write_lock(int(time.time()) - 60)
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --dry-run"), 4)

    def test_corrupt_lock_file_counts_as_locked(self):
        write(self.lock, "garbage\n")
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 4)
        self.assertRc(self.gate("owner-a", "unlock"), 0)
        self.assertFalse(self.lock.exists())

    def test_dry_run_lock_and_unlock_change_nothing(self):
        self.assertRc(self.gate("owner-a", "lock demo --dry-run"), 0)
        self.assertFalse(self.lock.exists())
        self.write_lock(int(time.time()) + 3600)
        self.assertRc(self.gate("owner-a", "unlock --dry-run"), 0)
        self.assertTrue(self.lock.exists())


class DryRunTests(GateCase):
    def test_dry_run_flag_and_env_execute_nothing(self):
        before = self.conf.read_text(encoding="utf-8")
        digest = self.propose()
        cmds = [f"deploy-bundle {SHA}", f"restart-backend {SHA} --ack-data-loss", "nginx-restore pre-old",
                f"nginx-add-location other POST {GOOD_REGEX}", f"nginx-approve-location card-decision {digest}",
                f"rollback-bundle {SHA}"]
        write(self.rootp / "etc/nginx/backup/pre-old/site.conf", "# old\n")
        write(self.rootp / "backups" / f"www-site-a.bak-before-{SHA[:8]}.tgz", "x")
        write(self.rootp / "backups" / f"www-site-b.bak-before-{SHA[:8]}.tgz", "x")
        backups_before = sorted(os.listdir(self.rootp / "backups"))
        for cmd in cmds:
            for how in ("flag", "env"):
                with self.subTest(cmd=cmd, how=how):
                    if how == "flag":
                        p = self.gate("owner-a", cmd + " --dry-run")
                    else:
                        p = self.gate("owner-a", cmd, DRY_RUN="1")
                    self.assertRc(p, 0)
                    self.assertIn("DRY_RUN:", p.stdout)
        calls = self.calls()
        for word in ("deploy.sh", "systemctl", "nginx ", "sqlite3", "fetch"):
            self.assertNotIn(word, calls)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), before)
        self.assertEqual(sorted(os.listdir(self.rootp / "backups")), backups_before)
        self.assertEqual(sorted(os.listdir(self.rootp / "etc/nginx/backup")), ["pre-old"])
        self.assertEqual(os.listdir(self.rootp / "var/lib/ax-gate/pending"), ["card-decision.req"])
        self.assertEqual(os.listdir(self.rootp / "var/lib/ax-gate/approved"), [])
        self.assertEqual(self.audit_text().count("mode=real"), 3)   # 준비용 제안 한 번(start·proposed·end)

    def test_dry_run_still_needs_permission_and_valid_sha(self):
        self.assertRc(self.gate("member-x", f"rollback-bundle {SHA} --dry-run"), 3)
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA_LOCAL} --dry-run"), 2)


class DeployTests(GateCase):
    def test_dry_run_shows_fixed_env_and_runs_as_deploy_user(self):
        p = self.gate("member-x", f"deploy-bundle {SHA} --dry-run")
        self.assertRc(p, 0)
        self.assertIn("DRY_RUN: as_deploy env", p.stdout)
        # 환경변수는 전부 정책 값이다: <ref_env>=<SHA>, env 항목, 두 번째 대상에는 env_b 항목
        self.assertIn(f"DEPLOY_REF={SHA}", p.stdout)
        self.assertIn("APP_PUBLIC_MARK=placeholder-a", p.stdout)
        self.assertIn("APP_SITE_URL=http://site.test:8080", p.stdout)
        self.assertIn(f"DEPLOY_WEB_ROOT={self.root}/var/www/site-b", p.stdout)
        self.assertIn("DEPLOY_CHECK_URL=http://localhost:8300/", p.stdout)
        self.assertEqual(p.stdout.count("DRY_RUN: as_deploy env"), 2)
        self.assertEqual(p.stdout.count("DEPLOY_WEB_ROOT="), 1)       # env_b 는 두 번째 대상에만
        self.assertNotIn("deploy.sh REF=", self.calls())

    def test_deploy_script_and_git_run_as_the_deploy_account_not_root(self):
        self.assertRc(self.gate("member-x", f"deploy-bundle {SHA} --announced"), 0)
        calls = self.calls().splitlines()
        self.assertIn("runuser as=deploy", calls)
        # 배포 스크립트 호출 바로 앞에는 항상 runuser 가 있다
        for i, line in enumerate(calls):
            if line.startswith("deploy.sh REF=") or line.startswith("git "):
                self.assertEqual(calls[i - 1], "runuser as=deploy", line)

    def test_single_target_when_web_root_b_is_not_set(self):
        text = policy_text(self.root).replace(f'web_root_b = "{self.root}/var/www/site-b"\n', "")
        write(self.policy, text)
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 0)
        self.assertEqual(self.calls().count("deploy.sh REF="), 1)
        backups = os.listdir(self.rootp / "backups")
        self.assertIn(f"www-site-a.bak-before-{SHA[:8]}.tgz", backups)
        self.assertNotIn(f"www-site-b.bak-before-{SHA[:8]}.tgz", backups)

    def test_deploy_runs_both_bundles_with_expected_env(self):
        p = self.gate("owner-b", f"deploy-bundle {SHA[:8]} --announced")
        self.assertRc(p, 0)
        calls = self.calls()
        self.assertIn(f"deploy.sh REF={SHA} MARK=placeholder-a ROOT= URL= SITE=http://site.test:8080\n", calls)
        self.assertIn(f"deploy.sh REF={SHA} MARK=placeholder-a ROOT={self.root}/var/www/site-b "
                      "URL=http://localhost:8300/ SITE=http://site.test:8080\n", calls)
        # 직전 배포 해시를 남긴다
        prev = self.rootp / "backups" / f"deploy-prev-head.bak-before-{SHA[:8]}"
        self.assertEqual(prev.read_text(encoding="utf-8").strip(), SHA)
        self.assertIn(f"result=prev-head:{SHA}", self.audit_text())
        self.assertNotIn("systemctl", calls)           # 번들 배포는 백엔드를 재시작하지 않는다
        backups = sorted(os.listdir(self.rootp / "backups"))
        self.assertIn(f"www-site-a.bak-before-{SHA[:8]}.tgz", backups)
        self.assertIn(f"www-site-b.bak-before-{SHA[:8]}.tgz", backups)
        audit = self.audit_text()
        self.assertIn(f"user=owner-b\taction=deploy-bundle\targs={SHA[:8]}\tmode=real\tannounced=yes\tpr=-\t"
                      "note=-\tlayer=root\tresult=start", audit)
        self.assertIn("result=end:rc=0", audit)

    def test_sha_must_be_on_origin_and_on_an_allowed_branch(self):
        self.assertRc(self.gate("owner-a", "deploy-bundle 0123abcd --announced"), 2)        # 없는 커밋
        self.assertIn("result=fail:sha-unknown", self.audit_text())
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA_LOCAL} --announced"), 2)    # origin 에 없음
        self.assertIn("result=fail:sha-not-on-origin", self.audit_text())
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA_FEATURE} --announced"), 2)  # 기준 브랜치에 없음
        self.assertIn("result=fail:sha-not-on-branch", self.audit_text())
        self.assertRc(self.gate("owner-a", f"restart-backend {SHA_FEATURE} --ack-data-loss --announced"), 2)
        self.assertNotIn("deploy.sh", self.calls())
        self.assertNotIn("systemctl", self.calls())

    def test_branch_list_is_a_setting_and_empty_list_refuses(self):
        # 기준 브랜치를 main 만으로 바꾸면 dev 에만 있는 커밋은 거부된다
        text = policy_text(self.root).replace('["main", "dev"]', '["main"]')
        write(self.policy, text)
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 2)
        self.assertIn("result=fail:sha-not-on-branch", self.audit_text())
        write(self.policy, text.replace('["main"]', "[]"))
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 5)
        self.assertNotIn("deploy.sh", self.calls())

    def test_older_commit_is_refused_because_deploy_is_fast_forward_only(self):
        p = self.gate("owner-a", f"deploy-bundle {SHA_OLD} --announced")
        self.assertRc(p, 1)
        self.assertIn("result=fail:not-fast-forward", self.audit_text())
        self.assertNotIn("deploy.sh", self.calls())
        self.assertEqual(os.listdir(self.rootp / "backups"), [])

    def test_deploy_env_comes_only_from_policy_and_is_validated(self):
        text = policy_text(self.root)
        cases = [
            text.replace('ref_env = "DEPLOY_REF"\n', ""),                                 # SHA 를 넘길 변수 이름 없음
            text.replace('ref_env = "DEPLOY_REF"', 'ref_env = "deploy ref"'),
            text.replace("APP_SITE_URL=http://site.test:8080", "APP_SITE_URL=http://site.test:8080/;id"),
            text.replace("APP_SITE_URL=http://site.test:8080", "APP_SITE_URL=$(id)"),
            text.replace("APP_PUBLIC_MARK=placeholder-a", "lower=x"),
            text.replace("DEPLOY_CHECK_URL=http://localhost:8300/", "LD_PRELOAD=/tmp/x y"),
            text.replace('user = "deploy"', 'user = "root"'),                             # 배포 계정이 root 면 안 된다
            text.replace('check_url = "http://127.0.0.1:8300/"', 'check_url = "http://example.org/"'),
        ]
        for i, bad in enumerate(cases):
            with self.subTest(case=i):
                write(self.policy, bad)
                self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 5)
        self.assertNotIn("deploy.sh REF=", self.calls())

    def test_failed_check_url_after_deploy_is_a_failure(self):
        write(self.state / "curl_code_8300", "502\n")
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 1)
        self.assertIn("result=fail:http-check", self.audit_text())

    def test_human_token_from_web_env_in_bundle_fails(self):
        write(self.rootp / "var/www/site-b/assets/leak.js", f'const k="{HUMAN}";\n')
        p = self.gate("owner-a", f"deploy-bundle {SHA} --announced")
        self.assertRc(p, 1)
        self.assertIn("APP_FRONT_TOKEN", p.stdout)
        for text in (p.stdout, p.stderr, self.audit_text()):
            self.assertNotIn(HUMAN, text)

    def test_viewer_token_is_allowed_only_by_name_in_policy(self):
        # 정책에서 예외 이름을 빼면 번들 안의 열람 토큰도 실토큰으로 잡힌다
        write(self.policy, policy_text(self.root).replace('["APP_VIEWER_TOKEN"]', "[]"))
        p = self.gate("owner-a", f"deploy-bundle {SHA} --announced")
        self.assertRc(p, 1)
        self.assertIn("APP_VIEWER_TOKEN", p.stdout)
        self.assertNotIn(VIEWER, p.stdout)

    def test_dirty_repo_stops(self):
        write(self.state / "git_dirty", " M frontend/x\n")
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 1)
        self.assertNotIn("deploy.sh", self.calls())

    def test_real_token_in_bundle_fails_without_printing_it(self):
        write(self.rootp / "var/www/site-a/assets/leak.js", f'const k="{SECRET}";\n')
        p = self.gate("owner-a", f"deploy-bundle {SHA} --announced")
        self.assertRc(p, 1)
        self.assertIn("SOME_API_TOKEN", p.stdout)
        for text in (p.stdout, p.stderr, self.audit_text()):
            self.assertNotIn(SECRET, text)
        self.assertIn("result=fail:bundle-check", self.audit_text())

    def test_missing_placeholder_fails(self):
        write(self.rootp / "var/www/site-a/assets/app.js", "const t=1;\n")
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 1)

    def test_deploy_script_failure_is_reported(self):
        write(self.state / "deploy_rc", "7\n")
        self.assertRc(self.gate("owner-a", f"deploy-bundle {SHA} --announced"), 1)
        self.assertIn("result=fail:deploy-failed", self.audit_text())

    def test_rollback_restores_bundle_from_before_deploy(self):
        app = self.rootp / "var/www/site-a/assets/app.js"
        before = app.read_text(encoding="utf-8")
        self.assertRc(self.gate("member-x", f"deploy-bundle {SHA} --announced"), 0)
        write(app, 'const t="placeholder-a",d="placeholder-b",v=2;\n')    # 새 번들인 척
        self.assertRc(self.gate("member-x", f"rollback-bundle {SHA} --announced"), 3)   # 롤백은 권한자만
        p = self.gate("owner-b", f"rollback-bundle {SHA} --announced")
        self.assertRc(p, 0)
        self.assertEqual(app.read_text(encoding="utf-8"), before)
        self.assertIn(f"직전 배포 해시: {SHA}", p.stdout)
        leftovers = [n for n in os.listdir(self.rootp / "var/www") if n.startswith(".gate-")]
        self.assertEqual(leftovers, [])

    def test_rollback_without_backup_fails(self):
        self.assertRc(self.gate("owner-a", f"rollback-bundle {SHA} --announced"), 1)
        self.assertIn("result=fail:backup-not-found", self.audit_text())


class RestartTests(GateCase):
    CMD = f"restart-backend {SHA} --ack-data-loss --announced"

    def test_restart_backs_up_then_restarts_and_polls(self):
        p = self.gate("owner-b", self.CMD)
        self.assertRc(p, 0)
        calls = self.calls()
        copy = sqlite3.connect(str(self.rootp / "backups" / f"app.db.bak-before-{SHA[:8]}"))
        self.assertEqual(copy.execute("select note from items").fetchall(), [("row-1",)])
        copy.close()
        self.assertLess(calls.index("systemctl reset-failed example-backend"),
                        calls.index("systemctl restart example-backend"))
        self.assertEqual([ln for ln in calls.splitlines() if ln.startswith("systemctl")],
                         ["systemctl reset-failed example-backend", "systemctl restart example-backend"])
        backups = sorted(os.listdir(self.rootp / "backups"))
        for name in ("secrets.env", "state.json", ".env"):
            self.assertIn(f"{name}.bak-before-{SHA[:8]}", backups)
        self.assertIn(f"data-dir.bak-before-{SHA[:8]}.tgz", backups)
        self.assertIn(f"data-dir.bak-before-{SHA[:8]}-sqlite", backups)
        self.assertIn("health: 3/3", p.stdout)
        for text in (p.stdout, p.stderr, self.audit_text()):
            self.assertNotIn(SECRET, text)

    def test_dir_backup_excludes_sqlite_and_copies_it_separately(self):
        import tarfile
        self.assertRc(self.gate("owner-a", self.CMD), 0)
        bdir = self.rootp / "backups"
        with tarfile.open(bdir / f"data-dir.bak-before-{SHA[:8]}.tgz") as tf:
            names = tf.getnames()
        self.assertIn("data-dir/keep.txt", names)
        self.assertNotIn("data-dir/x.db", names)
        sq = bdir / f"data-dir.bak-before-{SHA[:8]}-sqlite"
        self.assertTrue(sq.is_dir())
        if os.name == "posix":
            self.assertEqual(sq.stat().st_mode & 0o777, 0o700)
        copy = sqlite3.connect(str(sq / "x.db"))
        self.assertEqual(copy.execute("select v from t").fetchall(), [("dir-row",)])
        copy.close()

    def test_dry_run_shows_exclude_and_sqlite_backup_and_writes_nothing(self):
        p = self.gate("owner-a", f"restart-backend {SHA} --ack-data-loss --dry-run")
        self.assertRc(p, 0)
        self.assertIn(r"--exclude=\*.db", p.stdout)
        self.assertIn(r"--exclude=\*.db-wal", p.stdout)
        self.assertIn(r"--exclude=\*.db-shm", p.stdout)
        self.assertIn("DRY_RUN: mkdir -m 700 --", p.stdout)
        self.assertIn("DRY_RUN: python3 -I -c", p.stdout)
        self.assertEqual(os.listdir(self.rootp / "backups"), [])

    def test_second_restart_keeps_first_backup(self):
        self.assertRc(self.gate("owner-a", self.CMD), 0)
        time.sleep(1.1)
        self.assertRc(self.gate("owner-a", self.CMD), 0)
        names = [n for n in os.listdir(self.rootp / "backups") if n.startswith("secrets.env.bak-before-")]
        self.assertEqual(len(names), 2)

    def test_head_mismatch_stops_before_restart(self):
        write(self.state / "git_head", "d" * 40 + "\n")
        self.assertRc(self.gate("owner-a", self.CMD), 1)
        self.assertNotIn("systemctl", self.calls())

    def test_health_timeout_is_a_failure(self):
        write(self.state / "curl_code", "503\n")
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 1)
        self.assertIn("result=fail:health-timeout", self.audit_text())
        self.assertIn("result=end:rc=1", self.audit_text())


class RestartBackupRetentionTests(GateCase):
    """restart-backend 백업 보관 개수 제한(정책 [restart] backup_keep)."""
    CMD = f"restart-backend {SHA} --ack-data-loss --announced"
    OLD = [f"{i:08x}" for i in range(0xa0, 0xa4)]      # 오래된 순서 4세트

    def set_keep(self, line="backup_keep = 3"):
        text = policy_text(self.root).replace("poll_seconds = 0\n", f"poll_seconds = 0\n{line}\n")
        write(self.policy, text)

    def make_sets(self, base="data-dir", shas=None, plain=False):
        """오래된 순서로 세트를 만든다. 폴더 원본이면 .tgz + -sqlite, 파일 원본(plain)이면 이름 하나."""
        bdir = self.rootp / "backups"
        t0 = time.time() - 100000
        for n, sha in enumerate(shas or self.OLD):
            when = t0 + n * 1000
            if plain:
                paths = [bdir / f"{base}.bak-before-{sha}"]
                paths[0].write_text("x", encoding="utf-8")
            else:
                tgz = bdir / f"{base}.bak-before-{sha}.tgz"
                tgz.write_text("x", encoding="utf-8")
                sq = bdir / f"{base}.bak-before-{sha}-sqlite"
                sq.mkdir()
                (sq / "x.db").write_text("x", encoding="utf-8")
                paths = [tgz, sq]
            for p in paths:
                os.utime(p, (when, when))

    def names(self):
        return sorted(os.listdir(self.rootp / "backups"))

    def sets_of(self, base):
        """base 의 .tgz 이름에서 세트 키(SHA 8자[.시각])만 뽑는다."""
        pre, suf = f"{base}.bak-before-", ".tgz"
        return sorted(n[len(pre):-len(suf)] for n in self.names() if n.startswith(pre) and n.endswith(suf))

    def test_keeps_newest_n_sets_and_deletes_older(self):
        self.make_sets()
        self.make_sets(base="app.db", plain=True)
        self.set_keep()
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        bdir = self.rootp / "backups"
        new = SHA[:8]
        self.assertEqual(self.sets_of("data-dir"), sorted([self.OLD[2], self.OLD[3], new]))
        for sha in self.OLD[:2]:
            self.assertFalse((bdir / f"data-dir.bak-before-{sha}.tgz").exists())
            self.assertFalse((bdir / f"data-dir.bak-before-{sha}-sqlite").exists())
            self.assertFalse((bdir / f"app.db.bak-before-{sha}").exists())
            self.assertRegex(p.stdout, rf"보관 정리: data-dir\.bak-before-{sha}\.tgz \(")
            self.assertRegex(p.stdout, rf"보관 정리: data-dir\.bak-before-{sha}-sqlite \(")
        for sha in self.OLD[2:]:
            self.assertTrue((bdir / f"data-dir.bak-before-{sha}.tgz").exists())
            self.assertTrue((bdir / f"data-dir.bak-before-{sha}-sqlite" / "x.db").exists())
            self.assertTrue((bdir / f"app.db.bak-before-{sha}").exists())
        self.assertTrue((bdir / f"data-dir.bak-before-{new}-sqlite").is_dir())
        self.assertIn("result=backup-prune:", self.audit_text())
        self.assertIn("systemctl restart", self.calls())
        # 정리는 백업이 끝난 뒤에 한다
        self.assertLess(p.stdout.index("백업 끝"), p.stdout.index("보관 정리:"))

    def test_other_names_symlinks_and_other_kinds_are_never_touched(self):
        self.make_sets()
        bdir = self.rootp / "backups"
        keep_names = [
            "www-site-a.bak-before-000000a0.tgz",            # 번들 백업
            "deploy-prev-head.bak-before-000000a0",          # 직전 HEAD 기록
            ".env.bak-before-000000a0",                      # .env 백업
            "other.bak-before-000000a0.tgz",                 # 정책에 없는 원본
            "data-dir.bak-before-000000a0.tgz.old",          # 접미가 다름
            "data-dir.bak-before-000000g0.tgz",              # 16진수가 아님
            "data-dir.bak-before-a0.tgz",                    # 8자가 아님
            "data-dir.bak-before-000000a0.zip",              # 모르는 접미
            "data-dir.bak-before-000000a0",                  # 폴더 원본의 접미 없는 이름
            "data-dir.bak-before-000000a0.20250101.tgz",     # 시각 형식이 다름
            "notes.txt",
        ]
        for n in keep_names:
            (bdir / n).write_text("x", encoding="utf-8")
            os.utime(bdir / n, (1000, 1000))                 # 아주 오래된 것처럼
        outside = self.rootp / "outside.txt"
        outside.write_text("do-not-delete", encoding="utf-8")
        linked = bdir / "data-dir.bak-before-000000b0.tgz"   # 패턴에 맞는 이름의 심볼릭 링크
        try:
            os.symlink(outside, linked)
            have_link = True
        except (OSError, NotImplementedError):
            have_link = False
        self.set_keep()
        self.assertRc(self.gate("owner-a", self.CMD), 0)
        for n in keep_names:
            self.assertTrue((bdir / n).exists(), n)
        self.assertEqual(outside.read_text(encoding="utf-8"), "do-not-delete")
        if have_link:
            self.assertTrue(os.path.islink(linked))
        self.assertFalse((bdir / f"data-dir.bak-before-{self.OLD[0]}.tgz").exists())
        self.assertTrue((bdir / f".env.bak-before-{SHA[:8]}").exists())

    def test_default_without_backup_keep_deletes_nothing(self):
        self.make_sets()
        before = self.names()
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        self.assertTrue(set(before) <= set(self.names()))
        self.assertNotIn("보관 정리", p.stdout)
        self.assertNotIn("backup-prune", self.audit_text())

    def test_dry_run_lists_but_does_not_delete(self):
        self.make_sets()
        before = self.names()
        self.set_keep()
        p = self.gate("owner-a", f"restart-backend {SHA} --ack-data-loss --dry-run")
        self.assertRc(p, 0)
        self.assertEqual(self.names(), before)
        # 새 세트 한 자리를 빼고 2세트만 남기므로 오래된 2세트가 지울 목록이다
        self.assertIn(f"보관 정리 예정: data-dir.bak-before-{self.OLD[0]}.tgz", p.stdout)
        self.assertIn(f"보관 정리 예정: data-dir.bak-before-{self.OLD[1]}-sqlite", p.stdout)
        self.assertNotIn(f"보관 정리 예정: data-dir.bak-before-{self.OLD[2]}", p.stdout)
        self.assertNotIn("backup-prune", self.audit_text())

    def test_hidden_dot_named_sets_are_pruned_too(self):
        (self.rootp / "data/.hid-dir").mkdir()
        write(self.rootp / "data/.hid-dir/keep.txt", "k")
        old = f'backup_dirs = ["{self.root}/data/data-dir"]'
        new = f'backup_dirs = ["{self.root}/data/data-dir", "{self.root}/data/.hid-dir"]\nbackup_keep = 3'
        text = policy_text(self.root)
        self.assertIn(old, text)
        write(self.policy, text.replace(old, new))
        self.make_sets(base=".hid-dir")
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        self.assertEqual(self.sets_of(".hid-dir"), sorted([self.OLD[2], self.OLD[3], SHA[:8]]))
        self.assertIn(f"보관 정리: .hid-dir.bak-before-{self.OLD[0]}.tgz", p.stdout)

    def test_current_and_previous_sets_always_kept(self):
        self.make_sets()
        self.make_sets(shas=[SHA[:8]])                       # 같은 SHA 의 옛 세트: 이번 세트는 이름에 시각이 붙는다
        for suffix in (".tgz", "-sqlite"):
            recent = time.time() - 50
            os.utime(self.rootp / "backups" / f"data-dir.bak-before-{SHA[:8]}{suffix}", (recent, recent))
        self.set_keep("backup_keep = 2")
        self.assertRc(self.gate("owner-a", self.CMD), 0)
        sets = self.sets_of("data-dir")
        self.assertEqual(len(sets), 2)
        self.assertIn(SHA[:8], sets)                                         # 직전 세트
        self.assertTrue(any(s.startswith(SHA[:8] + ".") for s in sets))      # 이번 세트(시각 붙음)

    def test_delete_failure_only_warns_and_restart_continues(self):
        self.make_sets()
        self.set_keep()
        write(self.rootp / "fakebin/rm", "#!/usr/bin/env bash\nexit 1\n")
        os.chmod(self.rootp / "fakebin/rm", 0o755)
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        self.assertIn("경고: 보관 정리 실패", p.stderr)
        self.assertIn("systemctl restart", self.calls())

    def test_backup_failure_prunes_nothing(self):
        self.make_sets()
        self.set_keep()
        (self.rootp / "data/state.json").unlink()            # 백업할 파일이 없어 백업이 실패한다
        before = self.names()
        self.assertRc(self.gate("owner-a", self.CMD), 1)
        self.assertTrue(set(before) <= set(self.names()))
        self.assertNotIn("backup-prune", self.audit_text())

    def test_invalid_backup_keep_is_policy_error(self):
        for line in ("backup_keep = 1", "backup_keep = 0", 'backup_keep = "abc"', "backup_keep = true",
                     'backup_keep = ["3"]', "backup_keep = 02"):
            self.set_keep(line)
            p = self.gate("owner-a", "status")
            self.assertRc(p, 5)
            self.assertIn("backup_keep", p.stderr, line)
        self.set_keep("backup_keep = 2")
        self.assertRc(self.gate("owner-a", "status"), 0)

    def test_status_shows_retention_line_counting_hidden(self):
        self.make_sets()
        p = self.gate("owner-a", "status")
        self.assertRc(p, 0)
        self.assertRegex(p.stdout, r"백업 보관: 4세트, 합계 .*제한 없음")
        (self.rootp / "data/.hid").mkdir()
        self.make_sets(base=".hid", shas=[self.OLD[0]])
        old = f'backup_dirs = ["{self.root}/data/data-dir"]'
        text = policy_text(self.root)
        write(self.policy, text.replace(
            old, f'backup_dirs = ["{self.root}/data/data-dir", "{self.root}/data/.hid"]\nbackup_keep = 5'))
        p = self.gate("owner-a", "status")
        self.assertRc(p, 0)
        self.assertRegex(p.stdout, r"백업 보관: 5세트, 합계 .*backup_keep=5")


class RestartSqliteDedupeTests(GateCase):
    """backup_sqlite 로 이미 뜬 파일은 폴더 백업의 -sqlite 복사에서 건너뛴다(같은 파일을 두 번 저장하지 않는다)."""
    CMD = f"restart-backend {SHA} --ack-data-loss --announced"

    def setUp(self):
        super().setUp()
        d = self.rootp / "data/data-dir"
        y = sqlite3.connect(str(d / "y.db"))
        y.execute("create table t (v text)")
        y.execute("insert into t values ('y-row')")
        y.commit()
        y.close()
        self.sq = self.rootp / "backups" / f"data-dir.bak-before-{SHA[:8]}-sqlite"

    def set_sqlite(self, items):
        arr = ", ".join(f'"{self.root}/{i}"' for i in items)
        old = f'backup_sqlite = ["{self.root}/data/app.db"]'
        write(self.policy, policy_text(self.root).replace(old, f"backup_sqlite = [{arr}]"))

    def test_overlap_goes_to_separate_file_only(self):
        self.set_sqlite(["data/app.db", "data/data-dir/x.db"])
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        bdir = self.rootp / "backups"
        self.assertTrue((bdir / f"x.db.bak-before-{SHA[:8]}").is_file())
        self.assertFalse((self.sq / "x.db").exists())
        self.assertTrue((self.sq / "y.db").is_file())
        self.assertIn("중복 건너뜀: x.db (backup_sqlite로 이미 백업)", p.stdout)
        self.assertNotIn("중복 건너뜀: y.db", p.stdout)

    def test_no_overlap_behaves_as_before(self):
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        self.assertTrue((self.sq / "x.db").is_file())
        self.assertTrue((self.sq / "y.db").is_file())
        self.assertNotIn("중복 건너뜀", p.stdout)

    def test_empty_backup_sqlite_puts_everything_in_folder(self):
        self.set_sqlite([])
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        self.assertTrue((self.sq / "x.db").is_file())
        self.assertTrue((self.sq / "y.db").is_file())
        self.assertNotIn("중복 건너뜀", p.stdout)

    def test_dry_run_shows_skip_line_and_writes_nothing(self):
        self.set_sqlite(["data/app.db", "data/data-dir/x.db"])
        p = self.gate("owner-a", f"restart-backend {SHA} --ack-data-loss --dry-run")
        self.assertRc(p, 0)
        self.assertIn("중복 건너뜀: x.db (backup_sqlite로 이미 백업)", p.stdout)
        self.assertEqual(os.listdir(self.rootp / "backups"), [])

    def test_all_dbs_overlapping_leaves_empty_folder_still_a_set_for_prune(self):
        self.set_sqlite(["data/app.db", "data/data-dir/x.db", "data/data-dir/y.db"])
        bdir = self.rootp / "backups"
        t0 = time.time() - 100000
        olds = [f"{i:08x}" for i in range(0xa0, 0xa4)]
        for n, sha in enumerate(olds):
            tgz = bdir / f"data-dir.bak-before-{sha}.tgz"
            tgz.write_text("x", encoding="utf-8")
            sq = bdir / f"data-dir.bak-before-{sha}-sqlite"
            sq.mkdir()                                     # 안이 빈 -sqlite 폴더
            for q in (tgz, sq):
                os.utime(q, (t0 + n * 1000, t0 + n * 1000))
        text = policy_text(self.root).replace("poll_seconds = 0\n", "poll_seconds = 0\nbackup_keep = 3\n")
        write(self.policy, text.replace(
            f'backup_sqlite = ["{self.root}/data/app.db"]',
            f'backup_sqlite = ["{self.root}/data/app.db", "{self.root}/data/data-dir/x.db", "{self.root}/data/data-dir/y.db"]'))
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        self.assertEqual(os.listdir(self.sq), [])
        for sha in olds[:2]:
            self.assertFalse((bdir / f"data-dir.bak-before-{sha}.tgz").exists())
            self.assertFalse((bdir / f"data-dir.bak-before-{sha}-sqlite").exists())
        for sha in olds[2:]:
            self.assertTrue((bdir / f"data-dir.bak-before-{sha}.tgz").exists())
            self.assertTrue((bdir / f"data-dir.bak-before-{sha}-sqlite").is_dir())


class NginxTwoPersonTests(GateCase):
    def backup_dirs(self):
        return sorted(os.listdir(self.rootp / "etc/nginx/backup"))

    def pending(self):
        return sorted(os.listdir(self.rootp / "var/lib/ax-gate/pending"))

    def test_proposal_alone_changes_nothing(self):
        digest = self.propose()
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertEqual(self.backup_dirs(), [])
        self.assertNotIn("nginx", self.calls())
        self.assertEqual(self.pending(), ["card-decision.req"])
        p = self.gate("member-x", "status")
        self.assertIn(f"location 제안(승인 대기): 이름 card-decision / 제안자 owner-b / POST {GOOD_REGEX} / "
                      f"확인값 {digest}", p.stdout)
        self.assertIn(f"note=digest:{digest}\tlayer=root\tresult=proposed", self.audit_text())

    def test_second_owner_approval_applies_and_records(self):
        digest = self.propose()
        p = self.gate("owner-c", f"nginx-approve-location card-decision {digest} --announced pr=975")
        self.assertRc(p, 0)
        conf = self.conf.read_text(encoding="utf-8")
        self.assertIn("# ax-gate: name=card-decision user=owner-b added=", conf)
        self.assertIn("location ~ " + GOOD_REGEX + " {", conf)
        self.assertIn("proxy_pass http://127.0.0.1:8201;", conf)
        self.assertIn("include snippets/example-auth.conf; }", conf)
        self.assertLess(conf.index(GOOD_REGEX), conf.index("limit_except GET HEAD"))
        calls = self.calls()
        self.assertLess(calls.index("nginx -t"), calls.index("systemctl reload nginx"))
        (bdir,) = self.backup_dirs()
        self.assertTrue(bdir.startswith("pre-gate-add-card-decision-"))
        saved = self.rootp / "etc/nginx/backup" / bdir
        self.assertEqual((saved / "site.conf").read_text(encoding="utf-8"), ORIG_CONF)
        self.assertTrue((saved / "site.conf.sha256").exists())
        copy = self.rootp / "opt/app/ops-copies/site.conf"
        self.assertEqual(copy.read_text(encoding="utf-8"), conf)
        self.assertIn(f"nginx-restore {bdir}", p.stdout)
        self.assertIn(f"record-pr {bdir} <PR번호>", p.stdout)
        # 승인 기록
        self.assertEqual(self.pending(), [])
        (rec,) = os.listdir(self.rootp / "var/lib/ax-gate/approved")
        text = (self.rootp / "var/lib/ax-gate/approved" / rec).read_text(encoding="utf-8")
        self.assertIn("proposer=owner-b\n", text)
        self.assertIn("approver=owner-c\n", text)
        self.assertIn(f"backup={bdir}\n", text)
        self.assertIn(f"user=owner-c\taction=nginx-approve-location\targs=card-decision {digest}\tmode=real\t"
                      f"announced=yes\tpr=975\tnote=proposer:owner-b,digest:{digest},self-approve:no\tlayer=root\t"
                      f"result=applied:{bdir}", self.audit_text())

        # 같은 정규식을 다시 제안할 수 없다
        self.assertRc(self.gate("owner-b", f"nginx-add-location again POST {GOOD_REGEX}"), 2)
        # 그 백업으로 복원하면 원래대로 돌아온다
        time.sleep(1.1)
        p = self.gate("owner-a", f"nginx-restore {bdir} --announced")
        self.assertRc(p, 0)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertEqual(copy.read_text(encoding="utf-8"), ORIG_CONF)

    def test_proposer_cannot_approve_own_proposal(self):
        digest = self.propose(key="owner-b")
        p = self.gate("owner-b", f"nginx-approve-location card-decision {digest} --announced")
        self.assertRc(p, 3)
        self.assertIn("result=fail:self-approval", self.audit_text())
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertEqual(self.pending(), ["card-decision.req"])
        self.assertNotIn("nginx", self.calls())

    # --- 정책으로 지정한 계정만 셀프 승인 ([self_approve] accounts) ---
    def set_self_approve(self, body):
        write(self.policy, policy_text(self.root) + "\n[self_approve]\n" + body + "\n")

    def approve_cmd(self, digest):
        return f"nginx-approve-location card-decision {digest} --announced"

    def test_self_approve_default_empty_list_still_refuses(self):
        self.set_self_approve("accounts = []")
        digest = self.propose(key="owner-b")
        self.assertRc(self.gate("owner-b", self.approve_cmd(digest)), 3)
        self.assertIn("result=fail:self-approval", self.audit_text())
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertEqual(self.pending(), ["card-decision.req"])

    def test_self_approve_account_outside_list_refused(self):
        self.set_self_approve('accounts = ["owner-c"]')
        digest = self.propose(key="owner-b")
        self.assertRc(self.gate("owner-b", self.approve_cmd(digest)), 3)
        self.assertIn("result=fail:self-approval", self.audit_text())
        self.assertNotIn("nginx", self.calls())
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)

    def test_self_approve_listed_account_passes_with_all_checks_and_audit(self):
        self.set_self_approve('accounts = ["owner-b", "owner-c"]')
        digest = self.propose(key="owner-b")
        p = self.gate("owner-b", self.approve_cmd(digest))
        self.assertRc(p, 0)
        self.assertIn("셀프 승인(정책 허용 계정)", p.stdout)
        conf = self.conf.read_text(encoding="utf-8")
        self.assertIn("location ~ " + GOOD_REGEX + " {", conf)
        calls = self.calls()
        self.assertLess(calls.index("nginx -t"), calls.index("systemctl reload nginx"))
        (bdir,) = self.backup_dirs()
        self.assertEqual((self.rootp / "etc/nginx/backup" / bdir / "site.conf").read_text(encoding="utf-8"),
                         ORIG_CONF)
        self.assertIn(f"note=proposer:owner-b,digest:{digest},self-approve:yes\tlayer=root\t"
                      f"result=applied:{bdir}", self.audit_text())
        self.assertEqual(self.pending(), [])

    def test_self_approve_keeps_every_other_check(self):
        self.set_self_approve('accounts = ["owner-b"]')
        digest = self.propose(key="owner-b")
        wrong = "0" * 12 if digest != "0" * 12 else "1" * 12
        self.assertRc(self.gate("owner-b", self.approve_cmd(wrong)), 2)           # 확인값
        self.assertIn("result=fail:digest-mismatch", self.audit_text())
        p = self.gate("owner-b", f"nginx-approve-location card-decision {digest}")   # 알림 확인
        self.assertNotEqual(p.returncode, 0)
        req = self.rootp / "var/lib/ax-gate/pending/card-decision.req"
        original = req.read_text(encoding="utf-8")
        lines = [("created_epoch=1" if ln.startswith("created_epoch=") else ln) for ln in original.splitlines()]
        write(req, "\n".join(lines) + "\n")                                         # 24시간 TTL
        self.assertRc(self.gate("owner-b", self.approve_cmd(digest)), 2)
        self.assertIn("result=fail:proposal-expired", self.audit_text())
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertNotIn("nginx", self.calls())

    def test_self_approve_does_not_widen_prefixes_or_validator(self):
        self.set_self_approve('accounts = ["owner-b"]')
        # owner-b 의 접두 밖 경로는 제안 단계에서 막힌다
        p = self.gate("owner-b", "nginx-add-location wide POST ^/example-api/api/items/other/x$")
        self.assertRc(p, 2)
        self.assertEqual(self.pending(), [])

    def test_second_person_approval_records_self_approve_no(self):
        self.set_self_approve('accounts = ["owner-b"]')
        digest = self.propose(key="owner-b")
        p = self.gate("owner-c", self.approve_cmd(digest))
        self.assertRc(p, 0)
        self.assertNotIn("셀프 승인", p.stdout)
        self.assertIn(f"note=proposer:owner-b,digest:{digest},self-approve:no\t", self.audit_text())
        self.assertNotIn("self-approve:yes", self.audit_text())

    def test_self_approve_bad_policy_fails_safe(self):
        bad = [
            'account = ["owner-b"]',                    # 키 오타
            'accounts = "owner-b"',                     # 문자열(목록 아님)
            'accounts = true',
            'accounts = 5',
            'accounts = ["ghost-user"]',                # [permissions] 에 없음
            'accounts = ["Owner-B"]',                   # 계정 형식 틀림
            'accounts = ["owner-b"]\naccounts = []',    # 중복 키
            'accounts = ["owner-b", 7]',                # 문자열 아닌 항목
        ]
        for body in bad:
            with self.subTest(body=body):
                self.set_self_approve(body)
                self.assertRc(self.gate("owner-b", "status"), 5)      # 정책 로드 실패 = 전체 거부
                self.assertRc(self.gate("owner-b", self.approve_cmd("0" * 12)), 5)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertNotIn("nginx", self.calls())

    def test_self_approve_section_typo_means_no_self_approval(self):
        write(self.policy, policy_text(self.root) + '\n[self_aprove]\naccounts = ["owner-b"]\n')
        digest = self.propose(key="owner-b")
        self.assertRc(self.gate("owner-b", self.approve_cmd(digest)), 3)
        self.assertIn("result=fail:self-approval", self.audit_text())

    def test_approval_needs_matching_digest_existing_and_fresh_proposal(self):
        digest = self.propose()
        wrong = "0" * 12 if digest != "0" * 12 else "1" * 12
        self.assertRc(self.gate("owner-c", f"nginx-approve-location card-decision {wrong} --announced"), 2)
        self.assertIn("result=fail:digest-mismatch", self.audit_text())
        self.assertRc(self.gate("owner-c", f"nginx-approve-location nothing {digest} --announced"), 2)
        self.assertIn("result=fail:no-proposal", self.audit_text())
        # 제안 파일을 누가 바꾸면 확인값이 달라져 승인되지 않는다
        req = self.rootp / "var/lib/ax-gate/pending/card-decision.req"
        original = req.read_text(encoding="utf-8")
        write(req, original.replace("cards", "other-cards"))
        self.assertRc(self.gate("owner-c", f"nginx-approve-location card-decision {digest} --announced"), 2)
        # 만료
        lines = [("created_epoch=1" if ln.startswith("created_epoch=") else ln) for ln in original.splitlines()]
        write(req, "\n".join(lines) + "\n")
        self.assertRc(self.gate("owner-c", f"nginx-approve-location card-decision {digest} --announced"), 2)
        self.assertIn("result=fail:proposal-expired", self.audit_text())
        self.assertIn("location 제안(만료): card-decision", self.gate("member-x", "status").stdout)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertNotIn("nginx", self.calls())

    def test_approver_needs_prefix_too(self):
        # owner-a 는 other-api 접두가 있지만 owner-c 는 없다 → owner-c 는 승인할 수 없다
        digest = self.propose(key="owner-a", name="bulk-fill", regex="^/other-api/v1/things/bulk-fill$")
        self.assertRc(self.gate("owner-c", f"nginx-approve-location bulk-fill {digest} --announced"), 2)
        self.assertIn("result=fail:location-rejected", self.audit_text())
        # 접두 표에 아예 없는 승인자
        self.assertRc(self.gate("restore-only", f"nginx-approve-location bulk-fill {digest} --announced"), 3)
        self.assertIn("result=fail:no-prefix", self.audit_text())
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)

    def test_proposal_rejections(self):
        cases = [
            ("owner-b", "^/example-api/api/items/other/[^/]+/x$"),     # 이 키의 접두 밖
            ("owner-b", "^/other-api/v1/things/bulk-fill$"),
            ("owner-a", "^/example-api/api/items/.*$"),                  # 와일드카드
            ("owner-a", "^/example-api/api/items/x"),                    # 끝이 고정 안 됨
            ("owner-a", "^/example-api/api/items/[^/]*$"),
            ("owner-a", "^/unknown-api/api/x$"),
        ]
        for key, regex in cases:
            with self.subTest(regex=regex):
                self.assertRc(self.gate(key, f"nginx-add-location n1 POST {regex}"), 2)
        for method in ("GET", "HEAD", "DELETE", "post"):
            with self.subTest(method=method):
                self.assertRc(self.gate("owner-a", f"nginx-add-location n1 {method} {GOOD_REGEX}"), 2)
        self.assertRc(self.gate("restore-only", f"nginx-add-location n1 POST {GOOD_REGEX}"), 3)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertNotIn("nginx", self.calls())

    def test_missing_insert_marker_stops_at_proposal(self):
        write(self.conf, ORIG_CONF.replace("    # AX-GATE-INSERT-BELOW\n", ""))
        self.assertRc(self.gate("owner-a", f"nginx-add-location n1 POST {GOOD_REGEX}"), 2)
        self.assertEqual(self.pending(), [])

    def test_failed_nginx_test_reverts_keeps_proposal_and_does_not_reload(self):
        digest = self.propose()
        write(self.state / "nginx_rc", "1\n")
        p = self.gate("owner-c", f"nginx-approve-location card-decision {digest} --announced")
        self.assertRc(p, 1)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertNotIn("reload", self.calls())
        self.assertIn("result=fail:nginx-test-failed", self.audit_text())
        self.assertEqual(self.pending(), ["card-decision.req"])


class NginxRestoreAndStatusTests(GateCase):
    def test_restore_checks_folder_and_checksum(self):
        self.assertRc(self.gate("owner-c", "nginx-restore ../backup --announced"), 2)
        self.assertRc(self.gate("owner-c", "nginx-restore a/b --announced"), 2)
        self.assertRc(self.gate("owner-c", "nginx-restore pre-none --announced"), 2)
        bdir = self.rootp / "etc/nginx/backup/pre-manual-20261111"
        write(bdir / "site.conf", "# older\n")
        write(bdir / "site.conf.sha256", "0" * 64 + "  site.conf\n")
        self.assertRc(self.gate("owner-c", "nginx-restore pre-manual-20261111 --announced"), 1)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        (bdir / "site.conf.sha256").unlink()
        p = self.gate("owner-c", "nginx-restore pre-manual-20261111 --announced")
        self.assertRc(p, 0)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), "# older\n")
        self.assertIn("체크섬: 파일 없음", p.stdout)
        self.assertIn("이 복원을 되돌리려면: nginx-restore pre-gate-restore-", p.stdout)

    def test_restore_reverts_when_nginx_test_fails(self):
        write(self.rootp / "etc/nginx/backup/pre-bad/site.conf", "# broken\n")
        write(self.state / "nginx_rc", "1\n")
        self.assertRc(self.gate("owner-a", "nginx-restore pre-bad --announced"), 1)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertNotIn("reload", self.calls())

    def test_status_reports_copies_and_is_read_only(self):
        p = self.gate("member-x", "status")
        self.assertRc(p, 0)
        self.assertIn("운영 사본: 실제 nginx 설정과 일치", p.stdout)
        self.assertIn("레포 사본: 정책에 경로 없음", p.stdout)
        self.assertIn("health: 3/3", p.stdout)
        self.assertIn("비밀값: 0", p.stdout)
        self.assertIn("location 제안: 없음", p.stdout)
        self.assertIn("확인 127.0.0.1:8301/extra: 200", p.stdout)
        for value in (SECRET, HUMAN, VIEWER):
            self.assertNotIn(value, p.stdout)
        write(self.conf, ORIG_CONF + "# hand edit\n")
        copy = self.rootp / "opt/app/repo/docs/ops/nginx/site.conf"
        write(copy, ORIG_CONF)
        write(self.policy, policy_text(self.root, extra=f'repo_nginx_copy = "{copy.as_posix()}"'))
        p = self.gate("member-x", "status")
        self.assertIn("운영 사본: 어긋남", p.stdout)
        self.assertIn("레포 사본: 어긋남", p.stdout)
        for word in ("systemctl restart", "systemctl reload", "deploy.sh", "nginx -t"):
            self.assertNotIn(word, self.calls())
        self.assertNotIn(SECRET, p.stdout)


PAUSED_SH = HERE.parent / "autodeploy-paused.sh"
OWNER_PLUS = (ALL + ', "gateway-allow-propose", "gateway-allow-approve", "gateway-allow-restore", '
              '"autodeploy-pause", "autodeploy-resume"')


class AutodeployPauseTests(GateCase):
    """자동 배포 정지·재개: 정책의 정지 표시 파일 하나만 쓴다. 권한자만, 최대 시간 안에서."""

    def setUp(self):
        super().setUp()
        (self.rootp / "var/lib/autodeploy").mkdir(parents=True)
        self.pause = self.rootp / "var/lib/autodeploy/autodeploy.pause"
        self.write_policy()

    def write_policy(self, section=None, default=None, owners=OWNER_PLUS):
        text = policy_text(self.root).replace(f"owner-a = [{ALL}]", f"owner-a = [{owners}]")
        text = text.replace(f"owner-b = [{ALL}]", f"owner-b = [{owners}]")
        if default is not None:
            text = text.replace('_default = ["status", "deploy-bundle", "record-pr"]', f"_default = [{default}]")
        if section is None:
            section = (f'[autodeploy]\npause_file = "{self.root}/var/lib/autodeploy/autodeploy.pause"\n'
                       "default_hours = 2\nmax_hours = 12\n")
        write(self.policy, text + "\n" + section)

    def tree(self):
        """임시 서버 폴더의 모든 파일(게이트가 쓰는 감사 로그·호출 기록·실행 잠금 제외)."""
        skip = ("var/log/ax-gate/", "state/", "run/lock/")
        out = {}
        for path in sorted(self.rootp.rglob("*")):
            rel = path.relative_to(self.rootp).as_posix()
            if path.is_file() and not rel.startswith(skip):
                out[rel] = path.read_bytes()
        return out

    def paused(self):
        """동봉한 판정 스크립트를 돌린다: 0 = 멈춤, 1 = 안 멈춤."""
        return subprocess.run([BASH, PAUSED_SH.as_posix(), self.pause.as_posix()], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60)

    def fields(self):
        return dict(ln.split("=", 1) for ln in self.pause.read_text(encoding="utf-8").splitlines())

    def test_pause_writes_only_the_pause_file_and_resume_overwrites_it(self):
        before = self.tree()
        self.assertEqual(self.paused().returncode, 1)                  # 파일이 없으면 멈춘 것이 아니다
        p = self.gate("owner-a", "autodeploy-pause team-demo")
        self.assertRc(p, 0)
        after = self.tree()
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        self.assertEqual(changed, {"var/lib/autodeploy/autodeploy.pause"})     # 그 파일 하나만
        f = self.fields()
        self.assertEqual((f["user"], f["reason"]), ("owner-a", "team-demo"))
        self.assertAlmostEqual(int(f["until_epoch"]), time.time() + 2 * 3600, delta=120)   # 기본 2시간
        r = self.paused()
        self.assertEqual(r.returncode, 0)
        self.assertIn("건 사람 owner-a", r.stderr)
        st = self.gate("member-x", "status").stdout
        self.assertIn("자동 배포: 정지 중 — 건 사람 owner-a, 사유 team-demo, 재개 예정 " + f["until"], st)
        # 정지는 게이트의 수동 배포를 막지 않는다(자동 배포 쪽만 읽는다)
        self.assertRc(self.gate("member-x", f"deploy-bundle {SHA} --dry-run"), 0)
        # 재개: 지우지 않고 재개 시각을 지금으로 덮어쓴다
        p = self.gate("owner-b", "autodeploy-resume")
        self.assertRc(p, 0)
        self.assertTrue(self.pause.exists())
        f = self.fields()
        self.assertLessEqual(int(f["until_epoch"]), int(time.time()))
        self.assertEqual((f["user"], f["reason"], f["resumed_by"]), ("owner-a", "team-demo", "owner-b"))
        self.assertEqual(self.paused().returncode, 1)
        self.assertIn("자동 배포: 정지 아님(owner-b 가 ", self.gate("member-x", "status").stdout)
        audit = self.audit_text()
        self.assertIn("user=owner-a\taction=autodeploy-pause\targs=team-demo\tmode=real\tannounced=no\tpr=-\t"
                      "note=-\tlayer=root\tresult=start", audit)
        self.assertIn("layer=root\tresult=paused:", audit)
        self.assertIn("user=owner-b\taction=autodeploy-resume\t", audit)
        self.assertIn("layer=root\tresult=resumed", audit)
        # 자동 배포의 타이머·서비스·스크립트는 건드리지 않는다(status 가 부르는 읽기 명령만 있다)
        for word in ("systemctl stop", "systemctl start", "systemctl disable", "systemctl restart",
                     "systemctl reload", "nginx -t", "deploy.sh REF="):
            self.assertNotIn(word, self.calls())
        self.assertRc(self.gate("owner-a", "autodeploy-resume"), 0)     # 이미 재개됨: 할 일 없음
        self.assertEqual(self.fields()["resumed_by"], "owner-b")

    def test_time_limit_comes_from_policy(self):
        soon = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 3 * 3600))
        self.assertRc(self.gate("owner-a", f"autodeploy-pause demo {soon}"), 0)
        self.assertEqual(self.fields()["until"], soon)
        before = self.pause.read_bytes()
        late = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 13 * 3600))
        for until in (late, "2099-01-01T00:00", "2020-01-01T00:00", "2026-13-45T99:99"):
            with self.subTest(until=until):
                self.assertRc(self.gate("owner-a", f"autodeploy-pause demo {until}"), 2)
        for cmd in ("autodeploy-pause", "autodeploy-pause two words x", "autodeploy-pause demo tomorrow",
                    "autodeploy-pause demo;id", "autodeploy-resume now", "autodeploy-pause ../x"):
            with self.subTest(cmd=cmd):
                self.assertRc(self.gate("owner-a", cmd), 2)
        self.assertEqual(self.pause.read_bytes(), before)
        # 최대 시간은 정책 값이다: 1시간으로 줄이면 3시간 뒤는 거부된다
        self.write_policy(section=(f'[autodeploy]\npause_file = "{self.root}/var/lib/autodeploy/autodeploy.pause"\n'
                                   "default_hours = 1\nmax_hours = 1\n"))
        self.assertRc(self.gate("owner-a", f"autodeploy-pause demo {soon}"), 2)
        self.assertIn("result=fail:bad-until", self.audit_text())

    def test_expired_pause_is_not_paused_without_resume(self):
        write(self.pause, f"user=owner-a\nreason=demo\nuntil=2026-01-01T00:00\nuntil_epoch={int(time.time()) - 60}\n")
        self.assertEqual(self.paused().returncode, 1)
        self.assertIn("자동 배포: 정지 아님(재개 예정 2026-01-01T00:00 지남", self.gate("member-x", "status").stdout)
        before = self.pause.read_bytes()
        self.assertRc(self.gate("owner-a", "autodeploy-resume"), 0)
        self.assertEqual(self.pause.read_bytes(), before)

    def test_only_listed_accounts_and_never_default(self):
        for cmd in ("autodeploy-pause demo", "autodeploy-resume"):
            with self.subTest(cmd=cmd):
                self.assertRc(self.gate("member-x", cmd), 3)              # 기본 계정
                self.assertRc(self.gate("owner-c", cmd), 3)               # 표에 그 동작이 없는 권한자
                self.assertRc(self.root_step(["member-x", "0", "0", "-"] + cmd.split()), 3)
        self.assertFalse(self.pause.exists())
        # _default 에 이 동작을 두면 정책 로드가 실패한다(전체 거부)
        for extra in ('"autodeploy-pause"', '"autodeploy-resume"'):
            with self.subTest(extra=extra):
                self.write_policy(default=f'"status", "deploy-bundle", "record-pr", {extra}')
                self.assertRc(self.gate("owner-a", "status"), 5)
                self.assertRc(self.gate("member-x", "autodeploy-pause demo"), 5)
        self.assertFalse(self.pause.exists())
        self.assertNotIn("sudo", self.calls())

    def test_while_locked_only_pause_is_allowed(self):
        self.assertRc(self.gate("owner-a", "lock team-demo"), 0)
        self.assertRc(self.gate("owner-a", "autodeploy-pause team-demo"), 0)
        self.assertEqual(self.paused().returncode, 0)
        self.assertRc(self.gate("owner-a", "autodeploy-resume"), 4)
        self.assertRc(self.root_step(["owner-a", "0", "0", "-", "autodeploy-resume"]), 4)
        self.assertEqual(self.paused().returncode, 0)
        self.assertRc(self.gate("owner-a", "unlock"), 0)
        self.assertRc(self.gate("owner-a", "autodeploy-resume"), 0)
        self.assertEqual(self.paused().returncode, 1)

    def test_dry_run_changes_nothing(self):
        self.assertRc(self.gate("owner-a", "autodeploy-pause demo --dry-run"), 0)
        self.assertFalse(self.pause.exists())
        self.assertRc(self.gate("owner-a", "autodeploy-pause demo"), 0)
        before = self.pause.read_bytes()
        p = self.gate("owner-a", "autodeploy-resume --dry-run")
        self.assertRc(p, 0)
        self.assertIn("DRY_RUN:", p.stdout)
        self.assertEqual(self.pause.read_bytes(), before)

    def test_bad_policy_for_pause_file(self):
        r = self.root
        other = self.rootp / "data/state.json"
        before = other.read_bytes()
        cases = [
            ("", 5),                                                            # [autodeploy] 없음
            ('[autodeploy]\npause_file = "TODO"\n', 5),
            ('[autodeploy]\npause_file = "relative/autodeploy.pause"\n', 5),
            (f'[autodeploy]\npause_file = "{r}/data/state.json"\n', 5),          # 이름이 autodeploy.pause 가 아님
            (f'[autodeploy]\npause_file = "{r}/etc/ax-gate/policy.toml"\n', 5),
            (f'[autodeploy]\npause_file = "{r}/var/lib/../lib/autodeploy/autodeploy.pause"\n', 5),
            (f'[autodeploy]\npause_file = "{r}/no/such/dir/autodeploy.pause"\n', 5),
        ]
        for section, rc in cases:
            with self.subTest(section=section):
                self.write_policy(section=section)
                self.assertRc(self.gate("owner-a", "autodeploy-pause demo"), rc)
                self.assertRc(self.gate("member-x", "status"), 0)                # status 는 계속 된다
        self.assertFalse(self.pause.exists())
        self.assertEqual(other.read_bytes(), before)
        # 모르는 키는 정책 로드 실패
        self.write_policy(section=f'[autodeploy]\npause_file = "{r}/var/lib/autodeploy/autodeploy.pause"\nunit = "x"\n')
        self.assertRc(self.gate("owner-a", "status"), 5)
        # 정책에 [autodeploy] 가 없으면 status 에 줄이 없다(예전과 같다)
        self.write_policy(section="")
        self.assertNotIn("자동 배포", self.gate("member-x", "status").stdout)

    def test_pause_file_spot_that_is_not_a_regular_file_is_never_written(self):
        self.pause.mkdir()
        self.assertRc(self.gate("owner-a", "autodeploy-pause demo"), 1)
        self.assertIn("result=fail:pause-file", self.audit_text())
        self.assertTrue(self.pause.is_dir())
        self.assertEqual(self.paused().returncode, 0)                    # 판정은 안전한 쪽(멈춤)

    def test_unreadable_pause_file_counts_as_paused_and_resume_repairs_it(self):
        write(self.pause, "garbage\n")
        self.assertEqual(self.paused().returncode, 0)
        self.assertIn("정지 중으로 본다", self.gate("member-x", "status").stdout)
        self.assertRc(self.gate("owner-a", "autodeploy-resume"), 0)
        self.assertEqual(self.paused().returncode, 1)
        self.assertEqual(self.fields()["resumed_by"], "owner-a")

    def test_helper_script_usage_and_never_executes_file_content(self):
        marker = self.rootp / "pwned"
        write(self.pause, f"until_epoch=$(touch {marker.as_posix()})\nuser=`id`\n")
        self.assertEqual(self.paused().returncode, 0)                    # 해석 불가 → 멈춤
        self.assertFalse(marker.exists())
        p = subprocess.run([BASH, PAUSED_SH.as_posix()], capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 2)
        future = int(time.time()) + 600
        write(self.pause, f"user=owner-a\nuntil=2026-01-01T00:00\nuntil_epoch={future}\n")
        self.assertEqual(self.paused().returncode, 0)


class RestartHeadMismatchTests(GateCase):
    """restart-backend 의 head-mismatch: 어느 쪽이 앞섰는지 가려 다음 명령을 알려 준다."""

    CMD = f"restart-backend {SHA} --ack-data-loss --announced"
    HEAD = "d" * 40

    def set_head(self, relation):
        write(self.state / "git_head", self.HEAD + "\n")
        pairs = {"behind": f"{self.HEAD} {SHA}\n", "ahead": f"{SHA} {self.HEAD}\n", "diverged": "\n"}[relation]
        write(self.state / "git_pairs", pairs)

    def test_server_behind_tells_to_deploy_first(self):
        self.set_head("behind")
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 1)
        self.assertIn(f"  1) deploy-bundle {SHA[:8]} --announced", p.stdout)
        self.assertIn(f"  2) restart-backend {SHA[:8]} --ack-data-loss --announced", p.stdout)
        self.assertIn(f"먼저 deploy-bundle {SHA[:8]}", p.stderr)
        self.assertIn("result=fail:head-mismatch", self.audit_text())
        self.assertNotIn("systemctl", self.calls())
        self.assertNotIn("deploy.sh REF=", self.calls())

    def test_server_ahead_tells_to_restart_with_current_head(self):
        self.set_head("ahead")
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 1)
        self.assertIn(f"  restart-backend {self.HEAD[:8]} --ack-data-loss --announced", p.stdout)
        self.assertNotIn("deploy-bundle", p.stdout)
        self.assertIn("result=fail:head-mismatch", self.audit_text())
        self.assertNotIn("systemctl", self.calls())

    def test_diverged_gives_no_command(self):
        self.set_head("diverged")
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 1)
        self.assertIn("서로 다른 갈래", p.stderr)
        self.assertNotIn("deploy-bundle", p.stdout + p.stderr)
        self.assertNotIn("systemctl", self.calls())

    def deploy_first_policy(self, value="true", perms=None):
        text = policy_text(self.root).replace('unit = "example-backend"',
                                              f'unit = "example-backend"\ndeploy_first = {value}')
        if perms:
            text = text.replace(f"owner-c = [{ALL}]", f"owner-c = [{perms}]")
        write(self.policy, text)

    def test_deploy_first_is_off_by_default_and_when_false(self):
        self.set_head("behind")
        self.deploy_first_policy("false")
        self.assertRc(self.gate("owner-a", self.CMD), 1)
        self.assertNotIn("deploy.sh REF=", self.calls())
        self.assertNotIn("systemctl", self.calls())

    def test_deploy_first_stops_before_restart_when_deploy_does_not_move_head(self):
        self.set_head("behind")
        self.deploy_first_policy("true")
        # 배포가 HEAD 를 옮기지 못하면 배포 뒤 HEAD 확인에서 멈춘다(재시작하지 않는다)
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 1)
        self.assertEqual(self.calls().count("deploy.sh REF=" + SHA), 2)
        self.assertNotIn("systemctl", self.calls())
        self.assertIn(f"result=deploy-first:{SHA[:8]}", self.audit_text())
        self.assertIn("result=fail:head-after-deploy", self.audit_text())

    def test_deploy_first_deploys_then_restarts(self):
        self.set_head("behind")
        self.deploy_first_policy("true")
        write(self.state / "deploy_moves_head", "1\n")
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 0)
        calls = self.calls()
        self.assertEqual(calls.count("deploy.sh REF=" + SHA), 2)
        self.assertLess(calls.index("deploy.sh REF=" + SHA), calls.index("systemctl restart example-backend"))
        backups = os.listdir(self.rootp / "backups")
        self.assertIn(f"www-site-a.bak-before-{SHA[:8]}.tgz", backups)      # 배포의 번들 백업도 그대로 뜬다
        self.assertIn(f"app.db.bak-before-{SHA[:8]}", backups)              # 재시작의 백업도 뜬다
        audit = self.audit_text()
        self.assertIn(f"result=deploy-first:{SHA[:8]}", audit)
        self.assertIn(f"result=prev-head:{self.HEAD}", audit)
        self.assertIn("health: 3/3", p.stdout)

    def test_deploy_first_failure_in_bundle_check_stops_before_restart(self):
        self.set_head("behind")
        self.deploy_first_policy("true")
        write(self.state / "deploy_moves_head", "1\n")
        write(self.rootp / "var/www/site-a/assets/leak.js", f'const k="{SECRET}";\n')
        p = self.gate("owner-a", self.CMD)
        self.assertRc(p, 1)
        self.assertIn("result=fail:bundle-check", self.audit_text())
        self.assertNotIn("systemctl", self.calls())
        self.assertNotIn(SECRET, p.stdout + p.stderr + self.audit_text())

    def test_deploy_first_needs_deploy_permission_too(self):
        self.set_head("behind")
        self.deploy_first_policy("true", perms='"status", "restart-backend"')
        p = self.gate("owner-c", self.CMD)
        self.assertRc(p, 1)
        self.assertIn(f"  1) deploy-bundle {SHA[:8]} --announced", p.stdout)
        self.assertNotIn("deploy.sh REF=", self.calls())
        self.assertNotIn("deploy-first", self.audit_text())

    def test_deploy_first_never_applies_when_server_is_ahead_or_diverged(self):
        self.deploy_first_policy("true")
        for relation in ("ahead", "diverged"):
            with self.subTest(relation=relation):
                self.set_head(relation)
                self.assertRc(self.gate("owner-a", self.CMD), 1)
        self.assertNotIn("deploy.sh REF=", self.calls())
        self.assertNotIn("systemctl", self.calls())

    def test_deploy_first_dry_run_shows_both_steps_and_changes_nothing(self):
        self.set_head("behind")
        self.deploy_first_policy("true")
        p = self.gate("owner-a", f"restart-backend {SHA} --ack-data-loss --dry-run")
        self.assertRc(p, 0)
        self.assertIn("DRY_RUN: as_deploy env", p.stdout)
        self.assertIn("systemctl restart example-backend", p.stdout)
        self.assertNotIn("deploy.sh REF=", self.calls())
        self.assertNotIn("systemctl", self.calls())
        self.assertEqual(os.listdir(self.rootp / "backups"), [])


class AutodeployHelpTests(GateCase):
    """새 동작이 help·오류 안내에 나오는지(PR1 의 사용성 장치와 맞물림)."""

    def test_new_actions_show_in_help_for_accounts_that_have_them(self):
        text = policy_text(self.root).replace(f"owner-a = [{ALL}]", f"owner-a = [{OWNER_PLUS}]")
        write(self.policy, text)
        p = self.gate("owner-a", "help")
        self.assertRc(p, 0)
        self.assertIn("  autodeploy-pause <사유> [재개시각 YYYY-MM-DDTHH:MM]\n", p.stdout)
        self.assertIn("  autodeploy-resume\n", p.stdout)
        self.assertNotIn("이 계정에 없는 동작", p.stdout)
        # 권한이 없는 계정에는 "없는 동작"으로만 나온다
        p = self.gate("member-x", "help")
        self.assertIn("rollback-bundle autodeploy-pause autodeploy-resume", p.stdout)
        self.assertNotIn("  autodeploy-pause <사유>", p.stdout)
        p = self.gate("member-x", "help autodeploy-pause")
        self.assertRc(p, 0)
        self.assertIn("형식: autodeploy-pause <사유> [재개시각 YYYY-MM-DDTHH:MM]", p.stdout)
        self.assertIn("이 동작 권한이 없다", p.stdout)
        self.assertEqual(self.calls(), "")

    def test_bad_args_and_suggestions_for_new_actions(self):
        text = policy_text(self.root).replace(f"owner-a = [{ALL}]", f"owner-a = [{OWNER_PLUS}]")
        write(self.policy, text)
        p = self.gate("owner-a", "autodeploy-pause")
        self.assertRc(p, 2)
        self.assertIn("형식: autodeploy-pause <사유> [재개시각 YYYY-MM-DDTHH:MM]", p.stderr)
        p = self.gate("owner-a", "autodeploy-resume now")
        self.assertRc(p, 2)
        self.assertIn("형식: autodeploy-resume", p.stderr)
        p = self.gate("owner-a", "autodeploy")
        self.assertRc(p, 2)
        self.assertIn("비슷한 동작: autodeploy-pause, autodeploy-resume", p.stderr)
        p = self.gate("owner-a", "pause demo")
        self.assertIn("비슷한 동작: autodeploy-pause", p.stderr)
        # 플래그는 동작 뒤 어디에든 온다
        self.assertRc(self.gate("owner-a", "autodeploy-pause --dry-run demo"), 5)   # 정책에 [autodeploy] 없음
        p = self.gate("member-x", "autodeploy-pause demo")
        self.assertRc(p, 3)
        self.assertIn("이 계정에 허용된 동작: status deploy-bundle record-pr", p.stderr)


CONF_B = """server {
    listen 8301;
    # AX-GATE-INSERT-BELOW
    location /example-api/ {
        limit_except GET HEAD { deny all; }
    }
}
"""
PROFILES = ('\n[location_profiles]\n'
            'long-timeout = ["proxy_read_timeout 300s;", "proxy_send_timeout 300s;"]\n'
            'big-body = ["client_max_body_size 50m;"]\n')


def old_digest(name, method, regex, proposer):
    """프로필·다중 대상이 생기기 전의 확인값 공식. 프로필 없는 제안은 이 값 그대로여야 한다."""
    import hashlib
    return hashlib.sha256(f"{name}\n{method}\n{regex}\n{proposer}\n".encode()).hexdigest()[:12]


class NginxMultiTargetTests(GateCase):
    """정책 [nginx_targets]: 대상 파일 전부를 한 묶음으로 바꾼다. 실패하면 전부 되돌린다."""

    def setUp(self):
        super().setUp()
        self.conf_b = self.rootp / "etc/nginx/conf.d/site-b.conf"
        write(self.conf_b, CONF_B)
        self.targets = f'\n[nginx_targets]\nsite-b = "{self.root}/etc/nginx/conf.d/site-b.conf"\n'
        write(self.policy, policy_text(self.root) + self.targets)

    def backup_dirs(self):
        return sorted(os.listdir(self.rootp / "etc/nginx/backup"))

    def pending(self):
        return sorted(os.listdir(self.rootp / "var/lib/ax-gate/pending"))

    def both(self):
        return self.conf.read_text(encoding="utf-8"), self.conf_b.read_text(encoding="utf-8")

    def approve(self, digest, key="owner-c"):
        return self.gate(key, f"nginx-approve-location card-decision {digest} --announced")

    def test_approval_changes_every_target_with_one_test_and_one_reload(self):
        digest = self.propose()
        self.assertEqual(digest, old_digest("card-decision", "POST", GOOD_REGEX, "owner-b"))
        self.assertEqual(self.both(), (ORIG_CONF, CONF_B))             # 제안만으로는 아무것도 안 바뀐다
        p = self.approve(digest)
        self.assertRc(p, 0)
        a, b = self.both()
        for text in (a, b):
            self.assertIn("location ~ " + GOOD_REGEX + " {", text)
            self.assertIn("proxy_pass http://127.0.0.1:8201;", text)
        self.assertIn("listen 8301;", b)
        calls = self.calls()
        self.assertEqual(calls.count("nginx -t"), 1)
        self.assertEqual(calls.count("systemctl reload nginx"), 1)
        self.assertLess(calls.index("nginx -t"), calls.index("systemctl reload nginx"))
        (bdir,) = self.backup_dirs()
        saved = self.rootp / "etc/nginx/backup" / bdir
        # 기본 대상은 예전 자리, 추가 대상은 targets/<이름>/ 아래
        self.assertEqual((saved / "site.conf").read_text(encoding="utf-8"), ORIG_CONF)
        self.assertTrue((saved / "site.conf.sha256").exists())
        self.assertEqual((saved / "targets/site-b/site-b.conf").read_text(encoding="utf-8"), CONF_B)
        self.assertTrue((saved / "targets/site-b/site-b.conf.sha256").exists())
        self.assertIn("적용한 대상(전부 한 번에): 기본, site-b", p.stdout)
        (rec,) = os.listdir(self.rootp / "var/lib/ax-gate/approved")
        self.assertIn("targets=기본, site-b\n",
                      (self.rootp / "var/lib/ax-gate/approved" / rec).read_text(encoding="utf-8"))
        self.assertIn("nginx 대상 파일: 기본, site-b", self.gate("member-x", "status").stdout)
        # 그 백업으로 복원하면 두 파일 다 원래대로 돌아온다(한 번의 nginx -t·reload)
        time.sleep(1.1)
        p = self.gate("owner-a", f"nginx-restore {bdir} --announced")
        self.assertRc(p, 0)
        self.assertEqual(self.both(), (ORIG_CONF, CONF_B))
        self.assertIn("복원한 대상: 기본, site-b", p.stdout)
        self.assertEqual(self.calls().count("nginx -t"), 2)
        self.assertEqual(self.calls().count("systemctl reload nginx"), 2)

    def test_failed_nginx_test_restores_every_target(self):
        digest = self.propose()
        write(self.state / "nginx_rc", "1\n")
        p = self.approve(digest)
        self.assertRc(p, 1)
        self.assertEqual(self.both(), (ORIG_CONF, CONF_B))
        self.assertNotIn("reload", self.calls())
        self.assertIn("result=fail:nginx-test-failed", self.audit_text())
        self.assertEqual(self.pending(), ["card-decision.req"])
        self.assertEqual(os.listdir(self.rootp / "var/lib/ax-gate/approved"), [])

    def test_failure_while_replacing_second_target_restores_the_first(self):
        digest = self.propose()
        write(self.state / "cp_fail_dest", f"{self.root}/etc/nginx/conf.d/site-b.conf")
        p = self.approve(digest)
        self.assertRc(p, 1)
        self.assertTrue((self.state / "cp_failures").exists())          # 실제로 교체 도중에 실패했다
        self.assertEqual(self.both(), (ORIG_CONF, CONF_B))              # 먼저 바꾼 기본 대상도 되돌아왔다
        self.assertNotIn("nginx -t", self.calls())
        self.assertNotIn("reload", self.calls())
        self.assertIn("result=fail:nginx-replace-failed", self.audit_text())
        self.assertEqual(self.pending(), ["card-decision.req"])

    def test_failed_reload_restores_every_target(self):
        digest = self.propose()
        write(self.state / "systemctl_rc", "1\n")
        p = self.approve(digest)
        self.assertRc(p, 1)
        self.assertEqual(self.both(), (ORIG_CONF, CONF_B))
        self.assertIn("result=fail:nginx-reload-failed", self.audit_text())
        self.assertEqual(self.pending(), ["card-decision.req"])

    def test_every_target_is_checked_before_anything_changes(self):
        # 둘째 대상에 삽입 표시 줄이 없으면 제안부터 안 된다
        write(self.conf_b, CONF_B.replace("    # AX-GATE-INSERT-BELOW\n", ""))
        self.assertRc(self.gate("owner-b", f"nginx-add-location card-decision POST {GOOD_REGEX}"), 2)
        self.assertEqual(self.pending(), [])
        # 제안 뒤에 둘째 대상이 바뀌어 넣을 수 없게 되면, 승인은 아무것도 바꾸지 않고 멈춘다(백업 폴더도 없다)
        write(self.conf_b, CONF_B)
        digest = self.propose()
        broken = CONF_B.replace("    # AX-GATE-INSERT-BELOW\n", "")
        write(self.conf_b, broken)
        p = self.approve(digest)
        self.assertRc(p, 2)
        self.assertEqual(self.both(), (ORIG_CONF, broken))
        self.assertEqual(self.backup_dirs(), [])
        self.assertNotIn("nginx", self.calls())
        self.assertIn("result=fail:location-rejected", self.audit_text())
        # 둘째 대상에 같은 정규식이 이미 있어도 마찬가지다
        write(self.conf_b, CONF_B.replace("# AX-GATE-INSERT-BELOW\n",
                                          "# AX-GATE-INSERT-BELOW\n    location ~ " + GOOD_REGEX + " { }\n"))
        self.assertRc(self.approve(digest), 2)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertEqual(self.backup_dirs(), [])

    def test_target_specific_block_lines(self):
        lines = ('["limit_except {method} { deny all; }", "proxy_pass http://127.0.0.1:{port};", '
                 '"include snippets/example-proxy-b.conf;"]')
        write(self.policy, policy_text(self.root) + self.targets
              + f"\n[nginx_target_block_lines]\nsite-b = {lines}\n")
        self.assertRc(self.approve(self.propose()), 0)
        a, b = self.both()
        self.assertIn("include snippets/example-auth.conf; }", a)
        self.assertNotIn("example-proxy-b", a)
        self.assertIn("proxy_pass http://127.0.0.1:8201; include snippets/example-proxy-b.conf; }", b)
        self.assertNotIn("rewrite", b)
        # 대상별 블록 줄도 검증기를 거친다: 메서드 제한이 빠진 줄이면 제안이 안 된다
        bad = '["proxy_pass http://127.0.0.1:{port};"]'
        write(self.policy, policy_text(self.root) + self.targets
              + f"\n[nginx_target_block_lines]\nsite-b = {bad}\n")
        self.assertRc(self.gate("owner-b", f"nginx-add-location other POST {GOOD_REGEX[:-1]}/x$"), 2)
        self.assertEqual(self.pending(), [])

    def test_restore_reverts_every_target_when_nginx_test_fails(self):
        self.assertRc(self.approve(self.propose()), 0)
        changed = self.both()
        (bdir,) = self.backup_dirs()
        write(self.state / "nginx_rc", "1\n")
        time.sleep(1.1)
        self.assertRc(self.gate("owner-a", f"nginx-restore {bdir} --announced"), 1)
        self.assertEqual(self.both(), changed)
        self.assertEqual(self.calls().count("systemctl reload nginx"), 1)   # 승인 때 한 번뿐

    def test_restore_checks_every_copy_before_changing_anything(self):
        self.assertRc(self.approve(self.propose()), 0)
        changed = self.both()
        (bdir,) = self.backup_dirs()
        write(self.rootp / "etc/nginx/backup" / bdir / "targets/site-b/site-b.conf", "# tampered\n")
        self.assertRc(self.gate("owner-a", f"nginx-restore {bdir} --announced"), 1)
        self.assertIn("result=fail:backup-checksum", self.audit_text())
        self.assertEqual(self.both(), changed)
        self.assertEqual(self.backup_dirs(), [bdir])

    def test_restore_from_backup_made_before_targets_leaves_extra_target_alone(self):
        old = self.rootp / "etc/nginx/backup/pre-manual-20261111"
        write(old / "site.conf", "# older\n")
        p = self.gate("owner-c", "nginx-restore pre-manual-20261111 --announced")
        self.assertRc(p, 0)
        self.assertEqual(self.both(), ("# older\n", CONF_B))
        self.assertIn("대상 site-b: 이 백업에 사본이 없다(그 대상은 그대로 둔다)", p.stdout)
        self.assertIn("복원한 대상: 기본 / 백업에 없어 그대로 둔 대상: site-b", p.stdout)
        # 복원 직전 상태는 대상 전부를 백업해 둔다(되돌리기는 전부 한 번에)
        undo = [d for d in self.backup_dirs() if d.startswith("pre-gate-restore-")][0]
        self.assertTrue((self.rootp / "etc/nginx/backup" / undo / "targets/site-b/site-b.conf").exists())

    def test_requester_cannot_pick_a_target(self):
        # 넷째 인자는 프로필 이름이다. 대상 이름을 적어도 대상이 골라지지 않는다
        p = self.gate("owner-b", f"nginx-add-location card-decision POST {GOOD_REGEX} site-b")
        self.assertRc(p, 2)
        self.assertIn("result=fail:profile-unknown", self.audit_text())
        self.assertEqual(self.pending(), [])

    def test_bad_target_policy_stops_nginx_actions_but_not_status(self):
        r = self.root
        bad = [
            f'[nginx_targets]\nSite_B = "{r}/etc/nginx/conf.d/site-b.conf"\n',          # 이름 형식
            f'[nginx_targets]\nsite-b = "{r}/etc/nginx/conf.d/site.conf"\n',            # 기본 대상과 같은 파일
            '[nginx_targets]\nsite-b = "relative/site-b.conf"\n',                       # 경로 아님
            f'[nginx_targets]\nsite-b = "{r}/etc/nginx/../nginx/conf.d/site-b.conf"\n',
            '[nginx_targets]\nsite-b = "TODO"\n',
            f'[nginx_targets]\nsite-b = ["{r}/etc/nginx/conf.d/site-b.conf"]\n',        # 목록(글이 아님)
            self.targets.strip() + '\n\n[nginx_target_block_lines]\nsite-c = ["x;"]\n',   # 없는 대상의 블록 줄
        ]
        write(self.rootp / "etc/nginx/backup/pre-old/site.conf", "# old\n")
        for body in bad:
            with self.subTest(body=body):
                write(self.policy, policy_text(self.root) + "\n" + body)
                self.assertRc(self.gate("owner-b", f"nginx-add-location n1 POST {GOOD_REGEX}"), 5)
                self.assertRc(self.gate("owner-a", "nginx-restore pre-old --announced"), 5)
                self.assertRc(self.gate("member-x", "status"), 0)
        self.assertEqual(self.both(), (ORIG_CONF, CONF_B))
        self.assertEqual(self.pending(), [])
        self.assertNotIn("nginx", self.calls())

    def test_dry_run_changes_nothing_on_any_target(self):
        digest = self.propose()
        p = self.gate("owner-c", f"nginx-approve-location card-decision {digest} --dry-run")
        self.assertRc(p, 0)
        self.assertIn("DRY_RUN:", p.stdout)
        self.assertEqual(self.both(), (ORIG_CONF, CONF_B))
        self.assertEqual(self.backup_dirs(), [])
        self.assertNotIn("nginx", self.calls())


class NginxProfileTests(GateCase):
    """정책 [location_profiles]: 요청하는 사람은 이름만 고른다. 내용은 정책과 검증기 허용 목록이 정한다."""

    def setUp(self):
        super().setUp()
        write(self.policy, policy_text(self.root) + PROFILES)

    def propose_with(self, profile, key="owner-b"):
        p = self.gate(key, f"nginx-add-location card-decision POST {GOOD_REGEX} {profile}")
        self.assertRc(p, 0)
        return [ln.split()[-1] for ln in p.stdout.splitlines() if ln.strip().startswith("확인값")][0]

    def test_without_profile_nothing_changes_from_before(self):
        digest = self.propose()
        self.assertEqual(digest, old_digest("card-decision", "POST", GOOD_REGEX, "owner-b"))
        req = (self.rootp / "var/lib/ax-gate/pending/card-decision.req").read_text(encoding="utf-8")
        self.assertNotIn("profile=", req)
        p = self.gate("owner-c", f"nginx-approve-location card-decision {digest} --announced")
        self.assertRc(p, 0)
        conf = self.conf.read_text(encoding="utf-8")
        self.assertIn("include snippets/example-auth.conf; }", conf)
        self.assertNotIn("profile=", conf)
        self.assertNotIn("timeout", conf)
        self.assertIn(f"note=proposer:owner-b,digest:{digest},self-approve:no\tlayer=root\t", self.audit_text())
        (bdir,) = os.listdir(self.rootp / "etc/nginx/backup")
        self.assertFalse((self.rootp / "etc/nginx/backup" / bdir / "targets").exists())

    def test_profile_is_bound_into_digest_shown_in_status_and_applied(self):
        digest = self.propose_with("long-timeout")
        self.assertNotEqual(digest, old_digest("card-decision", "POST", GOOD_REGEX, "owner-b"))
        st = self.gate("member-x", "status").stdout
        self.assertIn(f"POST {GOOD_REGEX} / 확인값 {digest} / 프로필 long-timeout", st)
        self.assertIn("location 프로필: big-body, long-timeout", st)
        self.assertIn(f"note=digest:{digest},profile:long-timeout\tlayer=root\tresult=proposed", self.audit_text())
        p = self.gate("owner-c", f"nginx-approve-location card-decision {digest} --announced")
        self.assertRc(p, 0)
        conf = self.conf.read_text(encoding="utf-8")
        self.assertIn("# ax-gate: name=card-decision user=owner-b added=", conf)
        self.assertIn(" profile=long-timeout\n", conf)
        self.assertIn("include snippets/example-auth.conf; proxy_read_timeout 300s; proxy_send_timeout 300s; }",
                      conf)
        self.assertEqual(conf.count("proxy_pass "), 1)
        self.assertIn(f"note=proposer:owner-b,digest:{digest},self-approve:no,profile:long-timeout\tlayer=root\t"
                      "result=applied:", self.audit_text())
        (rec,) = os.listdir(self.rootp / "var/lib/ax-gate/approved")
        self.assertIn("profile=long-timeout\n",
                      (self.rootp / "var/lib/ax-gate/approved" / rec).read_text(encoding="utf-8"))

    def test_unknown_or_malformed_profile_name(self):
        p = self.gate("owner-b", f"nginx-add-location card-decision POST {GOOD_REGEX} huge")
        self.assertRc(p, 2)
        self.assertIn("쓸 수 있는 프로필: big-body, long-timeout", p.stderr)
        self.assertIn("result=fail:profile-unknown", self.audit_text())
        for name in ("Long", "a/b", "../x", "proxy_pass", "x;y"):
            with self.subTest(name=name):
                self.assertRc(self.gate("owner-b", f"nginx-add-location card-decision POST {GOOD_REGEX} {name}"), 2)
        self.assertRc(self.gate("owner-b", f"nginx-add-location card-decision POST {GOOD_REGEX} big-body x"), 2)
        self.assertEqual(os.listdir(self.rootp / "var/lib/ax-gate/pending"), [])

    def test_profile_with_directive_outside_allow_list_is_refused(self):
        bad = [
            '["proxy_pass http://127.0.0.1:9999;"]',
            '["include snippets/other.conf;"]',
            '["add_header X-Test 1;"]',
            '["proxy_read_timeout 300s;", "rewrite ^/(.*)$ /$1 break;"]',
            '["proxy_read_timeout 300s; proxy_pass http://127.0.0.1:9999;"]',
            '["proxy_read_timeout 99999s;"]',
            '["client_max_body_size 0;"]',
            '["limit_except GET { deny all; }"]',
            '[]',                       # 빈 프로필
            '"proxy_read_timeout 300s;"',   # 목록이 아님
        ]
        for body in bad:
            with self.subTest(body=body):
                write(self.policy, policy_text(self.root) + f"\n[location_profiles]\nrisky = {body}\n")
                self.assertRc(self.gate("owner-b", f"nginx-add-location n1 POST {GOOD_REGEX} risky"), 2)
        self.assertEqual(os.listdir(self.rootp / "var/lib/ax-gate/pending"), [])
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertNotIn("nginx", self.calls())

    def test_profile_removed_or_made_unsafe_after_proposal_stops_approval(self):
        digest = self.propose_with("long-timeout")
        approve = f"nginx-approve-location card-decision {digest} --announced"
        write(self.policy, policy_text(self.root))                       # 프로필이 정책에서 사라짐
        self.assertRc(self.gate("owner-c", approve), 2)
        self.assertIn("result=fail:profile-unknown", self.audit_text())
        write(self.policy, policy_text(self.root)
              + '\n[location_profiles]\nlong-timeout = ["proxy_pass http://127.0.0.1:9999;"]\n')
        self.assertRc(self.gate("owner-c", approve), 2)
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)
        self.assertEqual(os.listdir(self.rootp / "etc/nginx/backup"), [])
        self.assertNotIn("nginx", self.calls())

    def test_changing_the_profile_in_the_proposal_file_breaks_the_digest(self):
        digest = self.propose_with("big-body")
        req = self.rootp / "var/lib/ax-gate/pending/card-decision.req"
        original = req.read_text(encoding="utf-8")
        approve = f"nginx-approve-location card-decision {digest} --announced"
        for changed in (original.replace("profile=big-body", "profile=long-timeout"),
                        original.replace("profile=big-body\n", "")):
            with self.subTest(changed=changed[-40:]):
                write(req, changed)
                self.assertRc(self.gate("owner-c", approve), 2)
        self.assertEqual(self.audit_text().count("result=fail:digest-mismatch"), 2)
        write(req, original.replace("profile=big-body", "profile=Bad Name"))
        self.assertRc(self.gate("owner-c", approve), 1)                  # 깨진 제안 파일
        self.assertEqual(self.conf.read_text(encoding="utf-8"), ORIG_CONF)

    def test_profile_and_multiple_targets_together(self):
        conf_b = self.rootp / "etc/nginx/conf.d/site-b.conf"
        write(conf_b, CONF_B)
        write(self.policy, policy_text(self.root) + PROFILES
              + f'\n[nginx_targets]\nsite-b = "{self.root}/etc/nginx/conf.d/site-b.conf"\n')
        digest = self.propose_with("big-body")
        self.assertRc(self.gate("owner-c", f"nginx-approve-location card-decision {digest} --announced"), 0)
        for path in (self.conf, conf_b):
            self.assertIn("include snippets/example-auth.conf; client_max_body_size 50m; }",
                          path.read_text(encoding="utf-8"))
        self.assertEqual(self.calls().count("nginx -t"), 1)

    def test_profile_argument_shows_in_help_and_bad_args(self):
        p = self.gate("owner-b", "help")
        self.assertRc(p, 0)
        self.assertIn("  nginx-add-location <이름> <POST|PUT|PATCH|DELETE> <정규식> [프로필]\n", p.stdout)
        self.assertIn("프로필은 정책에 있는 이름만", p.stdout)
        self.assertIn("형식: nginx-add-location <이름> <POST|PUT|PATCH|DELETE> <정규식> [프로필]",
                      self.gate("member-x", "help nginx-add-location").stdout)
        p = self.gate("owner-b", "nginx-add-location card-decision POST")
        self.assertRc(p, 2)
        self.assertIn("<정규식> [프로필]", p.stderr)
        # 프로필 인자가 붙어도 플래그는 어디에든 올 수 있다
        p = self.gate("owner-b", f"nginx-add-location --dry-run card-decision POST {GOOD_REGEX} big-body")
        self.assertRc(p, 0)
        self.assertIn("프로필 big-body", p.stdout)


class UsabilityTests(GateCase):
    """사용법 오류를 줄이는 장치: help, 플래그 순서 무관, 오류 안내. 안전 경계는 그대로다."""

    def test_help_lists_only_my_actions_without_sudo(self):
        p = self.gate("member-x", "help")
        self.assertRc(p, 0)
        self.assertIn("계정 member-x 에 허용된 동작:", p.stdout)
        for line in ("  status", "  deploy-bundle <SHA>", "  record-pr <참조: 백업폴더명 또는 SHA> <PR번호>"):
            self.assertIn(line + "\n", p.stdout)
        self.assertNotIn("  restart-backend <SHA>", p.stdout)
        self.assertIn("이 계정에 없는 동작(권한자에게 요청한다): lock unlock nginx-restore nginx-add-location "
                      "nginx-approve-location gateway-allow-propose gateway-allow-approve gateway-allow-restore "
                      "restart-backend rollback-bundle", p.stdout)
        self.assertIn("--dry-run", p.stdout)
        self.assertIn("ax-gate 0.4.0", p.stdout)
        self.assertEqual(self.calls(), "")                       # sudo 를 부르지 않는다(앞단에서 끝난다)
        self.assertIn("user=member-x\taction=help\targs=-\tmode=real\tannounced=no\tpr=-\tnote=-\t"
                      "layer=front\tresult=help", self.audit_text())
        self.assertNotIn("layer=root", self.audit_text())

    def test_help_for_owner_and_for_one_action(self):
        p = self.gate("owner-a", "help")
        self.assertRc(p, 0)
        self.assertIn("  restart-backend <SHA> --ack-data-loss\n", p.stdout)
        # 이 테스트 정책의 권한자에게 없는 것은 게이트웨이 허용 3개와 자동 배포 정지·재개뿐이다(따로 적어야 열린다)
        self.assertIn("이 계정에 없는 동작(권한자에게 요청한다): gateway-allow-propose gateway-allow-approve "
                      "gateway-allow-restore autodeploy-pause autodeploy-resume\n", p.stdout)
        p = self.gate("member-x", "help restart-backend")
        self.assertRc(p, 0)
        self.assertIn("형식: restart-backend <SHA> --ack-data-loss", p.stdout)
        self.assertIn("이 동작 권한이 없다", p.stdout)
        self.assertIn("action=help\targs=restart-backend\t", self.audit_text())
        self.assertIn("이 동작을 쓸 수 있다", self.gate("owner-a", "help restart-backend").stdout)
        for alias in ("--help", "-h"):
            self.assertRc(self.gate("member-x", alias), 0)
        self.assertEqual(self.calls(), "")

    def test_help_rejects_extra_or_unknown_words_and_never_echoes_them(self):
        p = self.gate("member-x", "help /etc/shadow")
        self.assertRc(p, 2)
        self.assertNotIn("/etc/shadow", p.stderr + p.stdout + self.audit_text())
        self.assertRc(self.gate("member-x", "help status extra"), 2)
        p = self.gate("member-x", "help deploy")
        self.assertRc(p, 2)
        self.assertIn("비슷한 동작: deploy-bundle", p.stderr)
        self.assertEqual(self.calls(), "")

    def test_help_works_while_locked_and_is_front_only(self):
        write(self.lock, "garbage\n")
        self.assertRc(self.gate("member-x", "help"), 0)
        # 뒷단은 help 를 모른다(앞단 전용). root 로 도는 코드가 늘지 않는다.
        self.assertRc(self.root_step(["member-x", "0", "0", "-", "help"]), 2)
        self.assertIn("layer=root\tresult=denied:unknown-action", self.audit_text())

    def test_flags_are_recognized_anywhere_after_the_action(self):
        self.assertRc(self.gate("owner-b", f"deploy-bundle --announced {SHA} pr=963"), 0)
        self.assertRc(self.gate("owner-b", f"deploy-bundle pr=964 {SHA} --announced"), 0)
        audit = self.audit_text()
        for pr in ("963", "964"):
            self.assertIn(f"action=deploy-bundle\targs={SHA}\tmode=real\tannounced=yes\tpr={pr}\tnote=-\t"
                          "layer=root\tresult=start", audit)
        p = self.gate("owner-b", f"deploy-bundle --dry-run {SHA}")
        self.assertRc(p, 0)
        self.assertIn("DRY_RUN:", p.stdout)
        p = self.gate("owner-a", f"nginx-add-location --dry-run other POST {GOOD_REGEX}")
        self.assertRc(p, 0)
        self.assertEqual(os.listdir(self.rootp / "var/lib/ax-gate/pending"), [])
        # 동작은 여전히 맨 앞이어야 한다
        self.assertRc(self.gate("owner-b", f"--announced deploy-bundle {SHA}"), 2)
        self.assertRc(self.gate("owner-b", f"pr=1 deploy-bundle {SHA} --announced"), 2)
        # 알림 확인이 빠지면 순서와 상관없이 멈춘다
        self.assertRc(self.gate("owner-b", f"deploy-bundle pr=965 {SHA}"), 2)
        self.assertIn("pr=965\tnote=-\tlayer=front\tresult=denied:not-announced", self.audit_text())

    def test_restart_accepts_ack_before_sha_and_root_layer_stays_strict(self):
        for cmd in (f"restart-backend --ack-data-loss {SHA} --announced",
                    f"restart-backend --announced --ack-data-loss {SHA}",
                    f"restart-backend {SHA} --announced --ack-data-loss"):
            with self.subTest(cmd=cmd):
                self.assertRc(self.gate("owner-a", cmd), 0)
        audit = self.audit_text()
        self.assertEqual(audit.count(f"action=restart-backend\targs={SHA} --ack-data-loss\tmode=real\t"
                                     "announced=yes\tpr=-\tnote=-\tlayer=root\tresult=start"), 3)
        self.assertEqual(self.calls().count("systemctl restart example-backend"), 3)
        # 뒷단 검증은 그대로다: 순서가 바뀐 요청을 직접 넣으면 거부한다
        before = self.calls().count("systemctl restart")
        self.assertRc(self.root_step(["owner-a", "0", "1", "-", "restart-backend", "--ack-data-loss", SHA]), 2)
        self.assertEqual(self.calls().count("systemctl restart"), before)
        # 확인 플래그 자체는 여전히 필수다
        self.assertRc(self.gate("owner-a", f"restart-backend {SHA} --announced"), 2)
        self.assertRc(self.gate("owner-a", f"restart-backend --ack-data-loss --ack-data-loss --announced"), 2)

    def test_bad_args_shows_the_format_of_that_action(self):
        cases = {
            "deploy-bundle": "형식: deploy-bundle <SHA>",
            "lock": "형식: lock <사유> [해제시각 YYYY-MM-DDTHH:MM]",
            "nginx-add-location n POST": "형식: nginx-add-location <이름> <POST|PUT|PATCH|DELETE> <정규식>",
            "nginx-approve-location n": "형식: nginx-approve-location <이름> <확인값 12자>",
            f"restart-backend {SHA}": "형식: restart-backend <SHA> --ack-data-loss",
            "record-pr x": "형식: record-pr <참조: 백업폴더명 또는 SHA> <PR번호>",
            "status now": "형식: status",
        }
        for cmd, want in cases.items():
            with self.subTest(cmd=cmd):
                p = self.gate("owner-a", cmd)
                self.assertRc(p, 2)
                self.assertIn(want, p.stderr)
        p = self.gate("owner-a", f"deploy-bundle {SHA} --anounced-zzqq")
        self.assertRc(p, 2)
        self.assertIn("모르는 옵션이 있다", p.stderr)
        self.assertNotIn("zzqq", p.stderr + self.audit_text())
        self.assertEqual(self.audit_text().count("result=denied:bad-args"), len(cases) + 1)
        self.assertEqual(self.calls(), "")

    def test_unknown_action_suggests_known_actions_only(self):
        for cmd, want in ((f"deploy {SHA}", "비슷한 동작: deploy-bundle"),
                          ("restart", "비슷한 동작: restart-backend"),
                          ("rollback", "비슷한 동작: rollback-bundle"),
                          ("deploy_bundle", "비슷한 동작: deploy-bundle"),
                          ("nginx-add", "nginx-add-location"),
                          ("usage", "비슷한 동작: help")):
            with self.subTest(cmd=cmd):
                p = self.gate("owner-a", cmd)
                self.assertRc(p, 2)
                self.assertIn(want, p.stderr)
        p = self.gate("owner-a", "cat /etc/shadow")
        self.assertRc(p, 2)
        self.assertNotIn("비슷한 동작", p.stderr)
        self.assertIn("help 로 내 계정에 허용된 동작을 본다", p.stderr)
        self.assertNotIn("/etc/shadow", p.stderr + self.audit_text())
        self.assertEqual(self.calls(), "")

    def test_bad_regex_explains_braces_quotes_and_wildcards(self):
        base = "nginx-add-location zzqq-name POST "
        p = self.gate("owner-a", base + "^/example-api/api/items/zzqq/[0-9a-f]{8}$")
        self.assertRc(p, 2)
        self.assertIn("중괄호", p.stderr)
        self.assertIn("[0-9a-f]+", p.stderr)
        p = self.gate("owner-a", base + "'^/example-api/api/items/zzqq/[^/]+$'")
        self.assertRc(p, 2)
        self.assertIn("명령 전체를 작은따옴표", p.stderr)
        p = self.gate("owner-a", base + "^/example-api/api/items/zzqq/.*$")
        self.assertRc(p, 2)
        self.assertIn("[^/]+ 로 적는다", p.stderr)
        p = self.gate("owner-a", base + "^/example-api/api/items/zzqq/x?$")
        self.assertRc(p, 2)
        self.assertEqual(self.audit_text().count("result=denied:bad-regex"), 4)
        # 받은 정규식은 출력·기록하지 않는다
        self.assertNotIn("zzqq/", p.stderr + self.audit_text())
        self.assertEqual(self.calls(), "")
        # 안내대로 허용 묶음으로 바꾸면 통과한다
        self.assertRc(self.gate("owner-a", base + "^/example-api/api/items/zzqq/[0-9a-f]+$"), 0)

    def test_not_permitted_lists_what_this_account_can_do(self):
        p = self.gate("member-x", "lock demo")
        self.assertRc(p, 3)
        self.assertIn("이 계정에 허용된 동작: status deploy-bundle record-pr", p.stderr)
        p = self.gate("restore-only", f"deploy-bundle {SHA} --announced")
        self.assertRc(p, 3)
        self.assertIn("이 계정에 허용된 동작: status nginx-restore nginx-approve-location", p.stderr)

    def test_version_has_no_draft_suffix(self):
        p = self.gate("member-x", "status")
        self.assertIn("ax-gate 0.4.0 — status", p.stdout)
        self.assertNotIn("draft", p.stdout)


GW_ACTIONS = ', "gateway-allow-propose", "gateway-allow-approve", "gateway-allow-restore"'
GW_CARD = {
    "name": "example-agent",
    "grade": "read",
    "allowed_requesters": ["gateway-x"],
    "skills": [{"id": "list_items", "grade": "read"}, {"id": "get_item"}, {"id": "write_item", "grade": "write"}],
}
GW_LIST = "# allowed skills\nexisting_skill\n"


class GatewayAllowTests(GateCase):
    """gateway-allow-propose / approve / restore: 임시 폴더와 가짜 reload 명령으로만 돈다."""

    def setUp(self):
        super().setUp()
        r = self.rootp
        (r / "gw/backups").mkdir(parents=True)
        self.list = r / "gw/allowed.txt"
        self.card = r / "gw/card.json"
        write(self.list, GW_LIST)
        self.write_card(GW_CARD)
        self.set_policy()

    def write_card(self, card):
        write(self.card, json.dumps(card))

    def gw_body(self, reload_cmd="gw-reload now", extra=""):
        return (f'list_file = "{self.root}/gw/allowed.txt"\n'
                f'card_path = "{self.root}/gw/card.json"\n'
                f'reload_cmd = "{reload_cmd}"\n'
                f'backup_dir = "{self.root}/gw/backups"\n' + extra)

    def set_policy(self, gw_body=None, gw_extra="", self_approve=""):
        base = policy_text(self.root).replace(f"[{ALL}]", f"[{ALL}{GW_ACTIONS}]")
        body = gw_body if gw_body is not None else self.gw_body(extra='requester = "gateway-x"\n')
        write(self.policy, base + "\n[gateway_allow]\n" + body + gw_extra + "\n" + self_approve)

    def propose_gw(self, skill="list_items", key="owner-a"):
        p = self.gate(key, f"gateway-allow-propose {skill}")
        self.assertRc(p, 0)
        return [ln.split()[-1] for ln in p.stdout.splitlines() if ln.strip().startswith("확인값")][0]

    def approve_gw(self, digest, key="owner-b", skill="list_items"):
        return self.gate(key, f"gateway-allow-approve {skill} {digest} --announced")

    def list_text(self):
        return self.list.read_text(encoding="utf-8")

    def backups(self):
        return sorted(os.listdir(self.rootp / "gw/backups"))

    def pending_gw(self):
        return [n for n in sorted(os.listdir(self.rootp / "var/lib/ax-gate/pending")) if n.startswith("gateway-allow.")]

    def test_proposal_alone_changes_nothing(self):
        digest = self.propose_gw()
        self.assertEqual(self.list_text(), GW_LIST)
        self.assertEqual(self.backups(), [])
        self.assertNotIn("gw-reload", self.calls())
        self.assertEqual(self.pending_gw(), ["gateway-allow.list_items.req"])
        req = (self.rootp / "var/lib/ax-gate/pending/gateway-allow.list_items.req").read_text(encoding="utf-8")
        self.assertIn("proposer=owner-a\n", req)
        self.assertIn("created_epoch=", req)
        self.assertIn(f"digest={digest}\n", req)
        p = self.gate("member-x", "status")
        self.assertIn(f"게이트웨이 허용 제안(승인 대기): skill list_items / 제안자 owner-a / 확인값 {digest}", p.stdout)
        self.assertIn(f"note=digest:{digest}\tlayer=root\tresult=proposed", self.audit_text())

    def test_second_owner_approval_applies_backs_up_reloads_and_records(self):
        digest = self.propose_gw()
        p = self.approve_gw(digest)
        self.assertRc(p, 0)
        self.assertEqual(self.list_text(), GW_LIST + "list_items\n")
        self.assertIn("gw-reload now", self.calls())
        (bdir,) = self.backups()
        self.assertTrue(bdir.startswith("gwa-"))
        saved = self.rootp / "gw/backups" / bdir
        self.assertEqual((saved / "allowed.txt").read_text(encoding="utf-8"), GW_LIST)
        self.assertTrue((saved / "allowed.txt.sha256").exists())
        self.assertIn(f"gateway-allow-restore {bdir}", p.stdout)
        self.assertEqual(self.pending_gw(), [])
        (rec,) = os.listdir(self.rootp / "var/lib/ax-gate/approved")
        text = (self.rootp / "var/lib/ax-gate/approved" / rec).read_text(encoding="utf-8")
        self.assertIn("proposer=owner-a\n", text)
        self.assertIn("approver=owner-b\n", text)
        self.assertIn(f"backup={bdir}\n", text)
        self.assertIn(f"user=owner-b\taction=gateway-allow-approve\targs=list_items {digest}\tmode=real\t"
                      f"announced=yes\tpr=-\tnote=proposer:owner-a,digest:{digest},self-approve:no\tlayer=root\t"
                      f"result=applied:{bdir}", self.audit_text())
        self.assertNotIn("nginx", self.calls())
        # 이미 들어 있으면 다시 제안할 수 없다(중복)
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 2)

    def test_skill_without_skill_level_grade_uses_card_grade(self):
        digest = self.propose_gw("get_item")
        self.assertRc(self.approve_gw(digest, skill="get_item"), 0)
        self.assertIn("get_item\n", self.list_text())

    def test_proposer_cannot_approve_own_proposal(self):
        digest = self.propose_gw(key="owner-a")
        p = self.approve_gw(digest, key="owner-a")
        self.assertRc(p, 3)
        self.assertIn("result=fail:self-approval", self.audit_text())
        self.assertEqual(self.list_text(), GW_LIST)
        self.assertEqual(self.pending_gw(), ["gateway-allow.list_items.req"])
        self.assertNotIn("gw-reload", self.calls())

    def test_self_approve_only_for_listed_accounts(self):
        self.set_policy(self_approve='[self_approve]\naccounts = ["owner-c"]\n')
        digest = self.propose_gw(key="owner-a")
        self.assertRc(self.approve_gw(digest, key="owner-a"), 3)
        self.assertEqual(self.list_text(), GW_LIST)
        self.set_policy(self_approve='[self_approve]\naccounts = ["owner-a"]\n')
        p = self.approve_gw(digest, key="owner-a")
        self.assertRc(p, 0)
        self.assertIn("셀프 승인(정책 허용 계정)", p.stdout)
        self.assertIn(f"note=proposer:owner-a,digest:{digest},self-approve:yes", self.audit_text())
        self.assertIn("list_items\n", self.list_text())

    def test_proposal_expiry_wrong_digest_and_missing(self):
        digest = self.propose_gw()
        wrong = "0" * 12 if digest != "0" * 12 else "1" * 12
        self.assertRc(self.approve_gw(wrong), 2)
        self.assertIn("result=fail:digest-mismatch", self.audit_text())
        self.assertRc(self.approve_gw(digest, skill="get_item"), 2)
        self.assertIn("result=fail:no-proposal", self.audit_text())
        req = self.rootp / "var/lib/ax-gate/pending/gateway-allow.list_items.req"
        original = req.read_text(encoding="utf-8")
        # 제안 파일의 제안자를 바꾸면 확인값이 달라진다
        write(req, original.replace("proposer=owner-a", "proposer=owner-c"))
        self.assertRc(self.approve_gw(digest), 2)
        self.assertIn("result=fail:digest-mismatch", self.audit_text())
        # TTL 만료
        lines = [("created_epoch=1" if ln.startswith("created_epoch=") else ln) for ln in original.splitlines()]
        write(req, "\n".join(lines) + "\n")
        self.assertRc(self.approve_gw(digest), 2)
        self.assertIn("result=fail:proposal-expired", self.audit_text())
        self.assertIn("게이트웨이 허용 제안(만료): list_items", self.gate("member-x", "status").stdout)
        self.assertEqual(self.list_text(), GW_LIST)
        self.assertNotIn("gw-reload", self.calls())

    def test_write_grade_is_refused_at_proposal_and_at_approval(self):
        p = self.gate("owner-a", "gateway-allow-propose write_item")
        self.assertRc(p, 2)
        self.assertIn("grade-not-read", p.stderr)
        self.assertEqual(self.pending_gw(), [])
        # 제안 뒤 카드가 write 로 바뀌면 승인 시점 재검증에서 거부된다(카드 수준·skill 수준 둘 다)
        digest = self.propose_gw()
        for card in (dict(GW_CARD, grade="write"),
                     dict(GW_CARD, skills=[{"id": "list_items", "grade": "write"}])):
            self.write_card(card)
            self.assertRc(self.approve_gw(digest), 2)
        self.assertEqual(self.list_text(), GW_LIST)
        self.assertEqual(self.backups(), [])
        self.assertNotIn("gw-reload", self.calls())

    def test_card_without_any_grade_is_refused(self):
        self.write_card({"allowed_requesters": ["gateway-x"], "skills": [{"id": "list_items"}]})
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 2)

    def test_skill_not_in_card_and_duplicate(self):
        p = self.gate("owner-a", "gateway-allow-propose unknown_skill")
        self.assertRc(p, 2)
        self.assertIn("skill-not-in-card", p.stderr)
        digest = self.propose_gw()
        # 그 사이 다른 경로로 이미 허용됐다면 승인 시점에 중복으로 거부된다
        write(self.list, GW_LIST + "list_items\n")
        p = self.approve_gw(digest)
        self.assertRc(p, 2)
        self.assertIn("duplicate", p.stderr)
        self.assertEqual(self.list_text(), GW_LIST + "list_items\n")
        self.assertRc(self.gate("owner-a", "gateway-allow-propose existing_skill"), 2)

    def test_requester_must_be_in_card_when_configured(self):
        self.write_card(dict(GW_CARD, allowed_requesters=["someone-else"]))
        p = self.gate("owner-a", "gateway-allow-propose list_items")
        self.assertRc(p, 2)
        self.assertIn("requester-not-allowed", p.stderr)
        self.write_card({k: v for k, v in GW_CARD.items() if k != "allowed_requesters"})
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 2)
        # requester 를 정책에 적지 않으면 그 검사는 하지 않는다
        self.set_policy(gw_body=self.gw_body())
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 0)

    def test_bad_names_never_reach_sudo(self):
        for bad in ("List_items", "1abc", "a", "a-b", "a" * 65, "a;b"):
            with self.subTest(skill=bad):
                self.assertRc(self.gate("owner-a", f"gateway-allow-propose {bad}"), 2)
        self.assertNotIn("sudo", self.calls())

    def test_reload_failure_rolls_back_and_keeps_proposal(self):
        digest = self.propose_gw()
        write(self.state / "gw_reload_fail_once", "1\n")
        p = self.approve_gw(digest)
        self.assertRc(p, 1)
        self.assertEqual(self.list_text(), GW_LIST)
        self.assertEqual(self.calls().count("gw-reload now"), 2)  # 반영 시도 + 되돌린 뒤 다시 반영
        self.assertIn("result=fail:gateway-reload-failed", self.audit_text())
        self.assertEqual(self.pending_gw(), ["gateway-allow.list_items.req"])
        self.assertEqual(os.listdir(self.rootp / "var/lib/ax-gate/approved"), [])
        self.assertFalse([n for n in os.listdir(self.rootp / "gw") if ".gwa-new." in n])
        # 반영이 되돌린 뒤에도 계속 실패하면 경고가 나온다
        write(self.state / "gw_reload_rc", "1\n")
        time.sleep(1.1)
        p = self.approve_gw(digest)
        self.assertRc(p, 1)
        self.assertIn("경고: 되돌린 뒤에도 게이트웨이 반영 명령이 실패한다", p.stdout)
        self.assertEqual(self.list_text(), GW_LIST)

    def test_restore_returns_to_backup_and_is_undoable(self):
        digest = self.propose_gw()
        self.assertRc(self.approve_gw(digest), 0)
        (bdir,) = self.backups()
        time.sleep(1.1)
        p = self.gate("owner-c", f"gateway-allow-restore {bdir} --announced")
        self.assertRc(p, 0)
        self.assertEqual(self.list_text(), GW_LIST)
        pre = [b for b in self.backups() if b.startswith("gwa-pre-restore-")]
        self.assertEqual(len(pre), 1)
        self.assertIn(f"gateway-allow-restore {pre[0]}", p.stdout)
        self.assertIn(f"result=restored:{bdir}", self.audit_text())
        time.sleep(1.1)
        self.assertRc(self.gate("owner-c", f"gateway-allow-restore {pre[0]} --announced"), 0)
        self.assertEqual(self.list_text(), GW_LIST + "list_items\n")

    def test_restore_checks_folder_name_checksum_and_needs_announce(self):
        self.assertRc(self.gate("owner-c", "gateway-allow-restore pre-gate-add-x --announced"), 2)
        self.assertRc(self.gate("owner-c", "gateway-allow-restore gwa-nothing --announced"), 2)
        self.assertRc(self.gate("owner-c", "gateway-allow-restore gwa-nothing"), 2)
        digest = self.propose_gw()
        self.assertRc(self.approve_gw(digest), 0)
        (bdir,) = self.backups()
        write(self.rootp / "gw/backups" / bdir / "allowed.txt", "tampered\n")
        p = self.gate("owner-c", f"gateway-allow-restore {bdir} --announced")
        self.assertRc(p, 1)
        self.assertIn("result=fail:backup-checksum", self.audit_text())
        self.assertEqual(self.list_text(), GW_LIST + "list_items\n")

    def test_restore_reload_failure_reverts(self):
        digest = self.propose_gw()
        self.assertRc(self.approve_gw(digest), 0)
        (bdir,) = self.backups()
        time.sleep(1.1)
        write(self.state / "gw_reload_fail_once", "1\n")
        self.assertRc(self.gate("owner-c", f"gateway-allow-restore {bdir} --announced"), 1)
        self.assertEqual(self.list_text(), GW_LIST + "list_items\n")

    def test_announce_permission_lock_and_default_account(self):
        digest = self.propose_gw()
        p = self.gate("owner-b", f"gateway-allow-approve list_items {digest}")
        self.assertRc(p, 2)
        self.assertIn("gateway-allow-approve list_items", p.stderr)
        self.assertRc(self.gate("member-x", "gateway-allow-propose list_items"), 3)
        self.assertRc(self.gate("restore-only", f"gateway-allow-approve list_items {digest} --announced"), 3)
        self.gate("owner-a", "lock demo")
        self.assertRc(self.gate("owner-a", "gateway-allow-propose get_item"), 4)
        self.assertRc(self.approve_gw(digest), 4)
        self.assertEqual(self.list_text(), GW_LIST)
        # _default 에는 둘 수 없다
        write(self.policy, self.policy.read_text(encoding="utf-8").replace(
            '_default = ["status",', '_default = ["gateway-allow-propose", "status",'))
        self.assertRc(self.gate("owner-a", "status"), 5)

    def test_proposer_permission_is_rechecked_at_approval(self):
        digest = self.propose_gw(key="owner-a")
        text = self.policy.read_text(encoding="utf-8")
        write(self.policy, text.replace(f"owner-a = [{ALL}{GW_ACTIONS}]", f"owner-a = [{ALL}]"))
        p = self.approve_gw(digest)
        self.assertRc(p, 3)
        self.assertIn("result=fail:proposer-not-permitted", self.audit_text())
        self.assertEqual(self.list_text(), GW_LIST)

    def test_dry_run_changes_nothing(self):
        p = self.gate("owner-a", "gateway-allow-propose list_items --dry-run")
        self.assertRc(p, 0)
        self.assertEqual(self.pending_gw(), [])
        digest = self.propose_gw()
        p = self.gate("owner-b", f"gateway-allow-approve list_items {digest} --announced --dry-run")
        self.assertRc(p, 0)
        self.assertIn("DRY_RUN", p.stdout)
        self.assertEqual(self.list_text(), GW_LIST)
        self.assertEqual(self.backups(), [])
        self.assertNotIn("gw-reload", self.calls())
        self.assertEqual(self.pending_gw(), ["gateway-allow.list_items.req"])
        self.assertFalse([n for n in os.listdir(self.rootp / "var/lib/ax-gate/pending") if n.startswith(".gwa")])

    def test_policy_values_come_from_policy_and_are_validated(self):
        # 구역이 없으면 설정 오류
        write(self.policy, policy_text(self.root).replace(f"[{ALL}]", f"[{ALL}{GW_ACTIONS}]"))
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 5)
        self.assertIn("result=fail:config-path", self.audit_text())
        # 자리표시자는 쓸 수 없다
        self.set_policy(gw_body='list_file = "TODO"\ncard_path = "TODO"\nreload_cmd = "TODO"\nbackup_dir = "TODO"\n')
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 5)
        # 반영 명령이 TODO 이면 거부
        self.set_policy(gw_body=self.gw_body(reload_cmd="TODO"))
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 5)
        # 반영 명령에 셸 문자가 있으면 거부한다
        self.set_policy(gw_body=self.gw_body(reload_cmd="gw-reload; touch x"))
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 5)
        self.assertNotIn("gw-reload", self.calls())
        # 알 수 없는 키는 정책 전체를 거부한다
        self.set_policy(gw_extra='typo_key = "x"\n')
        self.assertRc(self.gate("member-x", "status"), 5)

    def test_unknown_list_format_stops_instead_of_writing(self):
        write(self.list, 'ALLOWED = ["a", "b"]\n')
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 5)
        self.assertEqual(self.list_text(), 'ALLOWED = ["a", "b"]\n')

    def test_missing_card_is_a_config_error(self):
        self.card.unlink()
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 5)

    def test_expected_card_sha(self):
        repo = self.rootp / "opt/app/repo"
        card = repo / "agents/card.json"
        write(card, json.dumps(GW_CARD))
        body = self.gw_body(extra=f'card_repo = "{repo.as_posix()}"\nexpected_card_sha = "{SHA}"\n')
        body = body.replace(f'{self.root}/gw/card.json', card.as_posix())
        self.set_policy(gw_body=body)
        # HEAD 가 승인된 SHA 이면 통과
        digest = self.propose_gw()
        self.assertRc(self.approve_gw(digest), 0)
        # HEAD 가 다르면 승인 시점에 거부
        write(self.list, GW_LIST)
        digest = self.propose_gw("get_item")
        write(self.state / "git_head", f"{SHA_LOCAL}\n")
        p = self.approve_gw(digest, skill="get_item")
        self.assertRc(p, 1)
        self.assertIn("result=fail:card-sha-mismatch", self.audit_text())
        self.assertEqual(self.list_text(), GW_LIST)
        # 카드 파일에 고친 흔적이 있으면 거부
        write(self.state / "git_head", f"{SHA}\n")
        write(self.state / "git_dirty", " M agents/card.json\n")
        self.assertRc(self.approve_gw(digest, skill="get_item"), 1)
        self.assertIn("result=fail:card-dirty", self.audit_text())
        # SHA 형식이 틀리면 설정 오류
        self.set_policy(gw_body=body.replace(SHA, "abc"))
        self.assertRc(self.gate("owner-a", "gateway-allow-propose list_items"), 5)

    def test_help_and_status_show_new_actions(self):
        p = self.gate("owner-a", "help")
        self.assertIn("gateway-allow-propose <skill>", p.stdout)
        self.assertIn("gateway-allow-approve <skill> <확인값 12자>", p.stdout)
        p = self.gate("member-x", "help")
        self.assertNotIn("gateway-allow-propose <skill>", p.stdout.split("이 계정에 없는 동작")[0])
        self.assertIn("게이트웨이 허용 제안: 없음", self.gate("member-x", "status").stdout)

    def test_nginx_proposals_and_gateway_proposals_do_not_mix(self):
        self.propose_gw()
        nginx_digest = self.propose(key="owner-b", name="card-decision")
        self.assertRc(self.gate("owner-c", f"nginx-approve-location card-decision {nginx_digest} --announced"), 0)
        self.assertEqual(self.pending_gw(), ["gateway-allow.list_items.req"])
        self.assertEqual(self.list_text(), GW_LIST)
        out = self.gate("member-x", "status").stdout
        self.assertIn("게이트웨이 허용 제안(승인 대기)", out)
        self.assertNotIn("location 제안(승인 대기): 이름 list_items", out)


class ExamplePolicyTests(GateCase):
    def test_gate_can_load_the_example_policy(self):
        shutil.copyfile(EXAMPLE_POLICY, self.policy)
        # 예시 정책은 게이트가 읽을 수 있는 문법이고, 표에 없는 계정은 _default 를 받는다
        self.assertRc(self.gate("member-x", "status"), 0)
        self.assertRc(self.gate("member-x", f"restart-backend {SHA} --ack-data-loss --announced"), 3)
        self.assertRc(self.gate("authorized-1", "lock demo --dry-run"), 0)
        # 예시의 경로는 전부 자리표시자("TODO")라서 채우기 전까지 그 경로가 필요한 동작은 실패한다
        self.assertRc(self.gate("authorized-1", f"restart-backend {SHA} --ack-data-loss --announced"), 5)
        self.assertRc(self.gate("member-x", f"deploy-bundle {SHA} --dry-run"), 5)
        self.assertRc(self.gate("authorized-1", "nginx-restore pre-x --announced"), 5)
        self.assertEqual(self.audit_text().count("result=fail:config-path"), 3)
        self.assertNotIn("systemctl restart", self.calls())
        self.assertNotIn("deploy.sh", self.calls())

    def test_example_policy_is_valid_toml_with_agreed_table(self):
        try:
            import tomllib
        except ImportError:  # Python 3.10 이하
            self.skipTest("tomllib 없음")
        with open(EXAMPLE_POLICY, "rb") as fh:
            data = tomllib.load(fh)
        perms = data["permissions"]
        everything = {"status", "lock", "unlock", "nginx-restore", "nginx-add-location", "nginx-approve-location",
                      "deploy-bundle", "restart-backend", "rollback-bundle", "record-pr"}
        for owner in ("authorized-1", "authorized-2", "authorized-3"):
            self.assertEqual(set(perms[owner]), everything)
            self.assertIn(owner, data["location_prefixes"])
        self.assertEqual(set(perms["_default"]), {"status", "deploy-bundle", "record-pr"})
        self.assertEqual(data["location"]["allowed_methods"], ["POST"])
        self.assertEqual(data["deploy"]["allowed_branches"], ["dev"])
        # 자동 배포 정지·재개는 예시에서 누구에게도 열려 있지 않고, 선행 배포 연계는 꺼져 있다
        for actions in perms.values():
            self.assertFalse({"autodeploy-pause", "autodeploy-resume"} & set(actions))
        self.assertNotIn("autodeploy", data)
        self.assertIs(data["restart"]["deploy_first"], False)
        # 예시에는 가짜 값과 자리표시자만 있다: 경로는 전부 "TODO"
        self.assertTrue(data["paths"])
        self.assertEqual(set(data["paths"].values()), {"TODO"})
        for key in ("secret_env_files",):
            self.assertEqual(data["deploy"][key], ["TODO"])
        # 예시의 블록 줄은 검증기가 받는 모양이다
        import importlib.util
        spec = importlib.util.spec_from_file_location("chk", HERE.parent / "nginx_location_check.py")
        chk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(chk)
        chk.check_block_lines(data["location"]["block_lines"])
        prefix = data["location_prefixes"]["authorized-1"][0]
        seg = prefix.strip("/").split("/")[0]
        self.assertIn(seg, data["location_upstreams"])
        chk.validate("POST", "^" + prefix + "x/[^/]+/approve$", data["location"]["allowed_methods"],
                     data["location_prefixes"]["authorized-1"], data["location_upstreams"],
                     data["location"]["block_lines"])
        # 예시의 프로필은 검증기 허용 목록 안의 지시어뿐이다. 추가 대상은 예시에서 켜 두지 않는다
        self.assertTrue(data["location_profiles"])
        for lines in data["location_profiles"].values():
            chk.check_profile_lines(lines, data["location"]["block_lines"])
        self.assertNotIn("nginx_targets", data)
        self.assertNotIn("nginx_target_block_lines", data)
        # 게이트웨이 허용 구역은 자리표시자뿐이고, 새 동작 3개는 예시 표의 어느 계정에도 열려 있지 않다
        self.assertEqual(set(data["gateway_allow"].values()), {"TODO"})
        for actions in perms.values():
            self.assertFalse({"gateway-allow-propose", "gateway-allow-approve", "gateway-allow-restore"} & set(actions))


if __name__ == "__main__":
    unittest.main()
