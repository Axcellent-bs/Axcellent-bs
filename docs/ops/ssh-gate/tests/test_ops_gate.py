"""ops-gate.sh 로컬 테스트: 인자 파싱·신원·허용표·잠금·DRY_RUN·감사 로그 (서버 없이).

값은 전부 가짜 예시다(계정 owner-a, 서비스 example-backend, 포트 8201 등). 실제 서버 값은 여기 없다.

실제 systemctl·nginx·sudo·git·curl 은 부르지 않는다. 임시 폴더의 가짜 명령(fakebin)이 대신
불리고, 불린 내용은 기록 파일에 남는다. 게이트는 OPS_GATE_TEST_ROOT 로 모든 경로를 임시 폴더로 돌린다.

실행: python -m unittest discover -s docs/ops/ssh-gate/tests -v   (bash 가 있어야 한다)
"""

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
    if [[ "$4" != origin/* ]]; then   # HEAD 가 그 커밋의 조상인가(앞으로 감기 가능한가)
      grep -qx "$4" "$FAKE_STATE/git_behind_head" 2>/dev/null && exit 1
      exit 0
    fi
    grep -qx "$3" "$FAKE_STATE/git_on_${4#origin/}"; exit $? ;;
  status) cat "$FAKE_STATE/git_dirty" 2>/dev/null; exit 0 ;;
esac
exit 1
""",
    "systemctl": 'echo "systemctl $*" >> "$FAKE_LOG"\nexit 0\n',
    "nginx": 'echo "nginx $*" >> "$FAKE_LOG"\nexit "$(cat "$FAKE_STATE/nginx_rc" 2>/dev/null || echo 0)"\n',
    # 주소별 응답 코드: state/curl_code_<포트> 가 있으면 그 값, 없으면 state/curl_code, 그것도 없으면 200
    "curl": ('url="${@: -1}"\nport="${url##*:}"\nport="${port%%/*}"\necho "curl $url" >> "$FAKE_LOG"\n'
             'if [[ -f "$FAKE_STATE/curl_code_$port" ]]; then printf "%s" "$(cat "$FAKE_STATE/curl_code_$port")"\n'
             'else printf "%s" "$(cat "$FAKE_STATE/curl_code" 2>/dev/null || echo 200)"\nfi\n'),
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
        write(r / "data/data-dir/x.db", "x")
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
        self.assertIn("health: 3/3", p.stdout)
        for text in (p.stdout, p.stderr, self.audit_text()):
            self.assertNotIn(SECRET, text)

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
                      f"announced=yes\tpr=975\tnote=proposer:owner-b,digest:{digest}\tlayer=root\t"
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


if __name__ == "__main__":
    unittest.main()
