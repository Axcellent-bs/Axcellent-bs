"""ax-autodeploy 백엔드 단계(dry-run) 판정 분기 시험. 공용 도구(Harness)는 test_backend_on.py 도 쓴다.

서버·네트워크 없이 돈다: 임시 git 저장소(origin/운영 트리/빌드 트리)와 가짜 curl(GitHub API 응답을 파일로 대신함)을 쓴다.
실행: python -m unittest discover -s docs/ops/autodeploy/tests -v   (bash, git, python3 필요)
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "ax-autodeploy"
KST = datetime.timezone(datetime.timedelta(hours=9))
# 2026-11-24 11:00 KST 에 dev 최신 커밋이 만들어졌다고 본다
COMMIT_AT = int(datetime.datetime(2026, 11, 24, 11, 0, tzinfo=KST).timestamp())

# BASH_ENV 로 불러오는 가짜 명령(함수라 실행 권한·PATH 에 상관없이 Windows 에서도 돈다).
# flock/pgrep 은 항상 "잠금 얻음/다른 배포 없음", curl 은 마지막 인자(URL)로 픽스처 파일을 고르고 없으면 실패(22)처럼 군다.
FAKE_SHIMS = r"""
flock() { [[ -z "${FAKE_FLOCK_BUSY:-}" ]]; }
pgrep() { return 1; }
curl() {
  local url="${@: -1}" d="${FAKE_API_DIR}" sha f
  case "${url}" in
    https://hooks.fake.invalid/*)   # 가짜 슬랙 웹훅: 본문(JSON)을 파일에 쌓는다. FAKE_SLACK_FAIL 파일이 있으면 실패
      [[ -e "${FAKE_SLACK_FAIL:-/nonexistent}" ]] && return 22
      cat >> "${FAKE_SLACK_OUT}"; printf '
' >> "${FAKE_SLACK_OUT}"; return 0 ;;
    */fake-busy/*) f="${d}/busy-${url##*/}.json"; f="${f%%\?*}"; [[ -f "${f}" ]] && cat "${f}" && return 0; return 22 ;;
    */commits/*/check-runs*) sha="${url#*/commits/}"; sha="${sha%%/*}"; f="${d}/checks-${sha}.json"; [[ -f "${f}" ]] || f="${d}/checks.json" ;;
    */commits/*/pulls*)      sha="${url#*/commits/}"; sha="${sha%%/*}"; f="${d}/pulls-${sha}.json"; [[ -f "${f}" ]] || f="${d}/pulls.json" ;;
    *) return 22 ;;
  esac
  [[ -f "${f}" ]] || return 22
  cat "${f}"
}
"""


def _kst_tz():
    """이 머신의 date 가 Asia/Seoul 을 모르면(Windows Git Bash 등) 같은 뜻의 POSIX 표기를 쓴다."""
    out = subprocess.run(["bash", "-c", "TZ=Asia/Seoul date -d @0 +%H"], capture_output=True, text=True).stdout.strip()
    return "Asia/Seoul" if out == "09" else "KST-9"


def run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="axad-"))
        t = self.tmp
        self.origin, self.prod, self.src = t / "origin", t / "prod", t / "src"
        self.state, self.api = t / "state", t / "api"
        for d in (self.state, self.api):
            d.mkdir()
        genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        self.genv = genv
        run(["git", "init", "-q", "-b", "dev", str(self.origin)], env=genv)
        (self.origin / "backend").mkdir()
        (self.origin / "backend" / "a.py").write_text("v1\n")
        run(["git", "add", "-A"], cwd=self.origin, env=genv)
        run(["git", "commit", "-q", "-m", "base"], cwd=self.origin, env=genv)
        self.base = run(["git", "rev-parse", "HEAD"], cwd=self.origin, env=genv)
        run(["git", "clone", "-q", str(self.origin), str(self.prod)], env=genv)  # 운영 트리 = base
        (self.origin / "backend" / "a.py").write_text("v2\n")
        run(["git", "add", "-A"], cwd=self.origin, env=genv)
        cenv = dict(genv, GIT_COMMITTER_DATE=f"{COMMIT_AT} +0900", GIT_AUTHOR_DATE=f"{COMMIT_AT} +0900")
        run(["git", "commit", "-q", "-m", "backend change"], cwd=self.origin, env=cenv)
        self.target = run(["git", "rev-parse", "HEAD"], cwd=self.origin, env=genv)
        run(["git", "clone", "-q", str(self.origin), str(self.src)], env=genv)  # 빌드 트리
        (self.state / "last-deployed-ref").write_text(self.base + "\n")
        (self.state / "token").write_text("not-a-real-token\n")  # 시험용 더미 값
        self.shims = t / "shims.sh"
        self.shims.write_text(FAKE_SHIMS)
        self.ok_checks()
        self.ok_pulls()
        self.log = self.state / "run.log"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 픽스처 ----
    def checks(self, ci=("completed", "success"), be=("completed", "success"), de=("completed", "success")):
        runs = [{"id": 1, "name": "ci", "status": ci[0], "conclusion": ci[1]},
                {"id": 2, "name": "backend", "status": be[0], "conclusion": be[1]}]
        if de is not None:
            runs.append({"id": 3, "name": "data-eval", "status": de[0], "conclusion": de[1]})
        (self.api / "checks.json").write_text(json.dumps({"check_runs": runs}))

    def ok_checks(self):
        self.checks()

    def pulls(self, body="## 배포 메모\n<!-- 설명 -->\n없음\n", labels=(), number=1700):
        data = [{"number": number, "body": body, "labels": [{"name": n} for n in labels], "merged_at": "2026-11-24T01:00:00Z"}]
        (self.api / "pulls.json").write_text(json.dumps(data))

    def ok_pulls(self):
        self.pulls()

    # ---- 실행 ----
    def run_script(self, now=COMMIT_AT + 3600, stage="dry-run", extra_env=None, args=()):
        env = dict(os.environ)
        env.update({
            "BASH_ENV": self.shims.as_posix(),
            "FAKE_API_DIR": str(self.api),
            "AX_PYTHON": Path(sys.executable).as_posix(),
            "AX_AUTODEPLOY_CONF": str(self.tmp / "no-conf"),
            "AX_PROD_REPO": str(self.prod), "AX_SRC": str(self.src),
            "AX_STATE_DIR": str(self.state), "AX_LOG_FILE": str(self.log), "AX_BUILD_LOG": str(self.state / "build.log"),
            "AX_CI_TOKEN_FILE": str(self.state / "token"),
            "AX_DEPLOY_LOCK_FILE": str(self.tmp / "deploy.lock"),
            "AX_BACKEND_TZ": _kst_tz(), "AX_BACKEND_STAGE": stage, "AX_BACKEND_NOW": str(now),
            "AX_SUDO": "", "AX_GATE_BIN": str(self.tmp / "none"),
            "AX_PAUSE_FILE": str(self.tmp / "autodeploy.pause"),
            "AX_SLACK_WEBHOOK_FILE": str(self.tmp / "slack-webhook"), "FAKE_SLACK_OUT": str(self.tmp / "slack.out"),
            "FAKE_SLACK_FAIL": str(self.tmp / "slack-fail"),
        })
        env.update(self.genv)
        env.update(extra_env or {})
        r = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)
        return r

    def add_commit(self, files, at=COMMIT_AT, msg="more"):
        """origin 에 커밋을 하나 더하고 빌드 트리(src)를 따라가게 한다. 새 대상 SHA 를 돌려준다."""
        for rel, text in files.items():
            f = self.origin / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        run(["git", "add", "-A"], cwd=self.origin, env=self.genv)
        cenv = dict(self.genv, GIT_COMMITTER_DATE=f"{at} +0900", GIT_AUTHOR_DATE=f"{at} +0900")
        run(["git", "commit", "-q", "-m", msg], cwd=self.origin, env=cenv)
        self.target = run(["git", "rev-parse", "HEAD"], cwd=self.origin, env=self.genv)
        run(["git", "fetch", "-q", "origin", "dev"], cwd=self.src, env=self.genv)
        return self.target

    def log_text(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""



class BackendStageTest(Harness):
    # ---- 시험 ----
    def test_off_by_default_does_nothing(self):
        r = self.run_script(stage="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("backend-dry-run", self.log_text())

    def test_unknown_stage_is_treated_as_off(self):
        r = self.run_script(stage="yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("알 수 없는 값", self.log_text())
        self.assertNotIn("backend-dry-run", self.log_text())

    def test_pass_would_deploy_and_notice_once(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self.log_text()
        self.assertIn("backend-dry-run: would-deploy", t)
        self.assertIn("지금이면 배포하고 재시작했을 것입니다", t)
        self.assertIn("포함 PR: #1700", t)
        self.assertEqual(t.count("backend-notice-draft(would-deploy"), 3)  # 문안 3줄, 한 번만
        # 같은 SHA 로 다시 돌려도 공지 문안·판정 로그가 늘지 않는다
        (self.state / "last-deployed-ref").write_text(self.base + "\n")
        self.run_script()
        t2 = self.log_text()
        self.assertEqual(t2.count("backend-notice-draft(would-deploy"), 3)
        self.assertEqual(t2.count("backend-dry-run: would-deploy"), 1)

    def test_ci_pending(self):
        self.checks(be=("in_progress", None))
        self.run_script()
        self.assertIn("backend-dry-run: ci-pending", self.log_text())
        self.assertNotIn("would-deploy", self.log_text())

    def test_ci_missing_check_is_pending_and_other_names_ignored(self):
        (self.api / "checks.json").write_text(json.dumps({"check_runs": [
            {"id": 1, "name": "ci", "status": "completed", "conclusion": "success"},
            {"id": 3, "name": "data-eval", "status": "completed", "conclusion": "success"}]}))
        self.run_script()
        self.assertIn("ci-pending", self.log_text())

    def test_ci_failure_and_cancelled(self):
        self.checks(ci=("completed", "failure"))
        self.run_script()
        self.assertIn("backend-dry-run: ci-fail", self.log_text())
        (self.state / "backend-verdict").unlink()
        self.checks(ci=("completed", "cancelled"))
        self.run_script()
        self.assertIn("backend-dry-run: ci-pending", self.log_text())

    def test_data_eval_in_progress_or_failed_holds_cancelled_or_missing_waits(self):
        cases = [(("in_progress", None), "ci-hold"), (("completed", "failure"), "ci-fail"),
                 (("completed", "cancelled"), "ci-pending"), (None, "ci-pending"), (("completed", "success"), "would-deploy")]
        for de, key in cases:
            with self.subTest(de=de):
                self.checks(de=de)
                vf = self.state / "backend-verdict"
                if vf.exists():
                    vf.unlink()
                if self.log.exists():
                    self.log.unlink()
                self.run_script()
                self.assertIn(f"backend-dry-run: {key}", self.log_text())

    def test_memo_section_holds_with_notice_once(self):
        self.pulls(body="## 배포 메모\nDB 이관 필요\n")
        self.run_script()
        t = self.log_text()
        self.assertIn("backend-dry-run: memo", t)
        self.assertIn("자동 배포 보류(dry-run): #1700 배포 메모 있음", t)
        self.run_script()
        self.assertEqual(self.log_text().count("backend-notice-draft(hold-memo"), 1)

    def test_memo_label_holds(self):
        self.pulls(labels=("manual-deploy",))
        self.run_script()
        self.assertIn("#1700 라벨 manual-deploy", self.log_text())

    def test_missing_section_ok_until_template_date(self):
        self.pulls(body="설명만 있음")
        self.run_script()
        self.assertIn("backend-dry-run: would-deploy", self.log_text())
        (self.state / "backend-verdict").unlink()
        self.run_script(extra_env={"AX_MEMO_REQUIRED_SINCE": "2026-11-20"})
        self.assertIn("본문에 배포 메모 섹션 없음", self.log_text())

    def test_no_pr_found_holds(self):
        (self.api / "pulls.json").write_text("[]")
        self.run_script()
        self.assertIn("backend-dry-run: no-pr", self.log_text())

    def test_pr_api_failure_skips_cycle(self):
        (self.api / "pulls.json").unlink()
        self.run_script()
        t = self.log_text()
        self.assertIn("skip-cycle PR 조회 API 실패", t)
        self.assertFalse((self.state / "backend-verdict").exists())  # 상태를 남기지 않는다

    def test_check_api_failure_skips_cycle(self):
        (self.api / "checks.json").unlink()
        self.run_script()
        self.assertIn("skip-cycle check-runs API 실패", self.log_text())

    def test_outside_hours(self):
        late = int(datetime.datetime(2026, 11, 24, 23, 30, tzinfo=KST).timestamp())
        self.run_script(now=late, extra_env={"AX_ALLOW_HOURS": "9-22"})
        self.assertIn("backend-dry-run: outside-hours", self.log_text())
        early = int(datetime.datetime(2026, 11, 25, 8, 59, tzinfo=KST).timestamp())
        (self.state / "backend-verdict").unlink()
        self.run_script(now=early, extra_env={"AX_ALLOW_HOURS": "9-22"})
        self.assertEqual(self.log_text().count("outside-hours"), 2)

    def test_not_quiet(self):
        self.run_script(now=COMMIT_AT + 120)
        self.assertIn("backend-dry-run: not-quiet", self.log_text())

    def test_min_gap_since_last_restart(self):
        now = COMMIT_AT + 3600
        (self.state / "backend-last-restart").write_text(f"{now - 600}\n")
        self.run_script(now=now)
        self.assertIn("backend-dry-run: min-gap", self.log_text())

    def test_deploy_lock_and_in_progress(self):
        (self.tmp / "deploy.lock").write_text("locked\n")
        self.run_script()
        self.assertIn("backend-dry-run: deploy-lock", self.log_text())
        (self.tmp / "deploy.lock").unlink()
        (self.state / "backend-in-progress").write_text("restart abc\n")
        self.run_script()
        self.assertIn("backend-dry-run: in-progress-file", self.log_text())

    def test_no_backend_change_is_silent(self):
        run(["git", "fetch", "-q", "origin", "dev"], cwd=self.prod, env=self.genv)
        run(["git", "reset", "-q", "--hard", self.target], cwd=self.prod, env=self.genv)  # 운영 트리가 이미 최신
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("backend-dry-run", self.log_text())

    def test_script_dry_run_flag_writes_no_state(self):
        r = self.run_script(args=("--dry-run",))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would-deploy", r.stdout)
        self.assertFalse((self.state / "backend-verdict").exists())
        self.assertFalse((self.state / "backend-notified").exists())

    def test_never_calls_slack_or_curl_post(self):
        # 웹훅 파일이 있어도 dry-run 은 읽지도 보내지도 않는다
        hook = self.tmp / "hook"
        hook.write_text("https://example.invalid/hook-placeholder\n")
        self.run_script(extra_env={"AX_SLACK_WEBHOOK_FILE": str(hook)})
        self.assertIn("would-deploy", self.log_text())
        self.assertNotIn("hook-placeholder", self.log_text())


if __name__ == "__main__":
    unittest.main()
