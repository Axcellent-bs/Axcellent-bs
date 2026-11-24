"""ax-autodeploy 백엔드 단계 on(실배포) 시험.

서버·네트워크 없이 돈다. 가짜 게이트(bash 스크립트: 호출을 기록하고 임시 운영 트리를 실제로 옮긴다),
가짜 gh/curl(GitHub API 응답을 파일로 대신함), 가짜 Slack 웹훅(curl 이 본문을 로컬 파일에 쌓는다)을 쓴다.
실행: python -m unittest discover -s docs/ops/autodeploy/tests -v   (bash, git, python3 필요)
"""
import datetime
import json
import os
import stat
import unittest

from test_backend_stage import COMMIT_AT, KST, Harness, run

# 가짜 게이트: ax-gate 0.5.0 의 동작 규칙 중 자동 배포가 기대는 것만 흉내 낸다.
#  - 모든 동작은 --announced 필요, 호출은 FAKE_GATE_LOG 에 "<계정>|<SSH_ORIGINAL_COMMAND>" 로 기록
#  - deploy-bundle 은 운영 트리를 그 SHA 로 옮기고 직전 HEAD 를 기록, rollback-code 는 그 기록으로만 되돌림
#  - uv-sync·restart-backend 는 운영 트리 HEAD 가 그 SHA 일 때만 성공
#  - FAKE_GATE_FAIL="동작:SHA앞부분(또는 *) ..." 인 호출은 끝 코드 FAKE_GATE_RC(기본 1)로 실패
FAKE_GATE = r"""#!/usr/bin/env bash
cmd="${SSH_ORIGINAL_COMMAND:-}"
printf '%s|%s\n' "${1:-}" "${cmd}" >> "${FAKE_GATE_LOG}"
read -r action sha rest <<< "${cmd}"
[[ "${cmd}" == *" --announced" ]] || { echo "announce 필요" >&2; exit 2; }
for spec in ${FAKE_GATE_FAIL:-}; do
  a="${spec%%:*}"; p="${spec#*:}"
  if [[ "${a}" == "${action}" && ( "${p}" == "*" || "${sha}" == "${p}"* ) ]]; then exit "${FAKE_GATE_RC:-1}"; fi
done
prod="${FAKE_GATE_PROD}"; head="$(git -C "${prod}" rev-parse HEAD)"
case "${action}" in
  deploy-bundle)
    git -C "${prod}" fetch -q origin dev && echo "${head}" > "${FAKE_GATE_DIR}/prev-${sha:0:8}" &&
      git -C "${prod}" checkout -q --detach "${sha}" ;;
  rollback-code)
    [[ -f "${FAKE_GATE_DIR}/prev-${sha:0:8}" ]] || exit 1
    git -C "${prod}" checkout -q --detach "$(cat "${FAKE_GATE_DIR}/prev-${sha:0:8}")" ;;
  uv-sync|restart-backend) [[ "${head}" == "${sha}" ]] || exit 1 ;;
  rollback-bundle) ;;
  *) exit 2 ;;
esac
"""


class OnStageTest(Harness):
    def setUp(self):
        super().setUp()
        self.gdir = self.tmp / "gate"
        self.gdir.mkdir()
        self.gate = self.gdir / "ax-gate"
        self.gate.write_text(FAKE_GATE, newline="\n")
        self.gate.chmod(self.gate.stat().st_mode | stat.S_IXUSR)
        self.gate_log = self.gdir / "calls.log"
        self.slack_out = self.tmp / "slack.out"
        self.hook = self.tmp / "slack-webhook"
        self.hook.write_text("https://hooks.fake.invalid/T000/B000/placeholder\n")  # 시험용 가짜 주소
        self.now = COMMIT_AT + 3600

    def go(self, **kw):
        env = {"FAKE_GATE_LOG": str(self.gate_log), "FAKE_GATE_DIR": str(self.gdir), "FAKE_GATE_PROD": str(self.prod),
               "AX_GATE_SUDO": "", "AX_GATE_EXEC": self.gate.as_posix()}
        env.update(kw.pop("extra_env", None) or {})
        kw.setdefault("stage", "on")
        kw.setdefault("now", self.now)
        return self.run_script(extra_env=env, **kw)

    def calls(self):
        """게이트 호출을 "<동작> <SHA 앞 8자> 나머지" 로 줄여 돌려준다."""
        if not self.gate_log.exists():
            return []
        out = []
        for ln in self.gate_log.read_text(encoding="utf-8").splitlines():
            acct, cmd = ln.split("|", 1)
            self.assertEqual(acct, "axautodeploy")
            parts = cmd.split()
            out.append(" ".join([parts[0], parts[1][:8]] + parts[2:]))
        return out

    def slack(self):
        if not self.slack_out.exists():
            return []
        return [json.loads(ln)["text"] for ln in self.slack_out.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def head(self, repo):
        return run(["git", "rev-parse", "HEAD"], cwd=repo, env=self.genv)

    @property
    def t8(self):
        return self.target[:8]

    @property
    def b8(self):
        return self.base[:8]

    # ---- 정상 배포 ----
    def test_success_order_and_state(self):
        r = self.go()
        self.assertEqual(r.returncode, 0, r.stderr + self.log_text())
        self.assertEqual(self.calls(), [f"deploy-bundle {self.t8} --announced",
                                        f"restart-backend {self.t8} --ack-data-loss --announced"])
        s = self.slack()
        self.assertEqual(len(s), 2)
        self.assertIn("곧 배포하고 재시작", s[0])
        self.assertIn(f"{self.b8} → {self.t8}", s[0])
        self.assertIn("#1700", s[0])
        self.assertIn("자동 배포 끝", s[1])
        self.assertEqual(self.head(self.prod), self.target)
        self.assertEqual((self.state / "last-deployed-ref").read_text().strip(), self.target)  # 프론트 기준도 따라간다
        self.assertEqual((self.state / "backend-last-restart").read_text().strip(), str(self.now))
        self.assertFalse((self.state / "backend-in-progress").exists())
        self.assertNotIn("hooks.fake.invalid", self.log_text())  # 웹훅 주소는 로그에 없다
        # 다음 주기: 운영 트리가 이미 최신이라 아무것도 안 한다
        self.go()
        self.assertEqual(len(self.calls()), 2)

    def test_uv_sync_inserted_between_bundle_and_restart(self):
        self.add_commit({"backend/uv.lock": "lock\n"}, at=COMMIT_AT)
        r = self.go()
        self.assertEqual(r.returncode, 0, r.stderr + self.log_text())
        self.assertEqual(self.calls(), [f"deploy-bundle {self.t8} --announced", f"uv-sync {self.t8} --announced",
                                        f"restart-backend {self.t8} --ack-data-loss --announced"])
        self.assertIn("uv sync 포함", self.slack()[0])

    def test_pyproject_in_subdir_also_triggers_uv_sync(self):
        self.add_commit({"backend/shared/x/pyproject.toml": "[project]\n"})
        self.go()
        self.assertIn(f"uv-sync {self.t8} --announced", self.calls())

    def test_no_uv_sync_when_only_code_changed(self):
        self.go()
        self.assertFalse([c for c in self.calls() if c.startswith("uv-sync")])

    # ---- 대기·보류 ----
    def test_not_quiet_waits_without_gate_or_slack(self):
        r = self.go(now=COMMIT_AT + 120)
        self.assertEqual(r.returncode, 0)
        self.assertIn("backend: not-quiet", self.log_text())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.slack(), [])

    def test_min_gap_is_60_minutes_by_default(self):
        (self.state / "backend-last-restart").write_text(f"{self.now - 1900}\n")
        self.go()
        self.assertIn("backend: min-gap", self.log_text())
        self.assertEqual(self.calls(), [])
        (self.state / "backend-verdict").unlink()
        (self.state / "backend-last-restart").write_text(f"{self.now - 3700}\n")
        self.go()
        self.assertEqual(len(self.calls()), 2)

    def test_min_gap_and_quiet_are_configurable(self):
        (self.state / "backend-last-restart").write_text(f"{self.now - 1900}\n")
        self.go(extra_env={"AX_BACKEND_MIN_GAP_SEC": "600"})
        self.assertEqual(len(self.calls()), 2)

    def test_ci_rules_and_data_eval(self):
        cases = [(dict(ci=("completed", "failure")), "ci-fail"), (dict(be=("in_progress", None)), "ci-pending"),
                 (dict(be=("completed", "cancelled")), "ci-pending"), (dict(ci=None), "ci-pending"),
                 (dict(de=("in_progress", None)), "ci-hold"), (dict(de=("completed", "failure")), "ci-fail"),
                 (dict(de=("completed", "cancelled")), "ci-pending"), (dict(de=None), "ci-pending")]
        for kw, key in cases:
            with self.subTest(kw=kw):
                if kw.get("ci", 1) is None:
                    (self.api / "checks.json").write_text(json.dumps({"check_runs": [
                        {"id": 2, "name": "backend", "status": "completed", "conclusion": "success"}]}))
                else:
                    self.checks(**kw)
                vf = self.state / "backend-verdict"
                if vf.exists():
                    vf.unlink()
                if self.log.exists():
                    self.log.unlink()
                self.go()
                self.assertIn(f"backend: {key}", self.log_text())
                self.assertEqual(self.calls(), [])

    def test_missing_token_file_holds_as_ci_off(self):
        (self.state / "token").unlink()
        self.go()
        self.assertIn("backend: ci-off", self.log_text())
        self.assertEqual(self.calls(), [])

    def test_memo_label_and_section_hold_with_one_notice(self):
        self.pulls(labels=("manual-deploy",))
        self.go()
        self.go()
        self.assertEqual(self.calls(), [])
        s = self.slack()
        self.assertEqual(len(s), 1)  # SHA 당 한 번
        self.assertIn("자동 배포 보류", s[0])
        self.assertIn("#1700 라벨 manual-deploy", s[0])
        self.assertNotIn("dry-run", s[0])
        # 본문 메모(없음이 아닌 내용)
        (self.state / "backend-verdict").unlink()
        (self.state / "backend-notified").unlink()
        self.pulls(body="## 배포 메모\n.env 에 KEY 추가\n")
        self.go()
        self.assertEqual(self.calls(), [])
        self.assertIn("배포 메모 있음", self.slack()[-1])

    def test_memo_none_section_deploys_and_missing_section_depends_on_date(self):
        self.pulls(body="## 배포 메모\n없음\n")
        self.go()
        self.assertEqual(len(self.calls()), 2)

    def test_missing_section_holds_only_after_required_since(self):
        self.pulls(body="설명만 있음")
        self.go(extra_env={"AX_MEMO_REQUIRED_SINCE": "2026-11-20"})
        self.assertEqual(self.calls(), [])
        self.assertIn("본문에 배포 메모 섹션 없음", self.log_text())
        (self.state / "backend-verdict").unlink()
        self.go()  # 날짜를 안 주면 섹션 없음은 메모 없음
        self.assertEqual(len(self.calls()), 2)

    def test_pr_not_found_or_api_failure_never_deploys(self):
        (self.api / "pulls.json").write_text("[]")
        self.go()
        (self.api / "pulls.json").unlink()
        self.go()
        self.assertEqual(self.calls(), [])

    # ---- 시간 창 ----
    def test_hours_default_all_day_weekend_and_configurable(self):
        sun = int(datetime.datetime(2026, 11, 29, 3, 0, tzinfo=KST).timestamp())  # 일요일 새벽
        self.go(now=sun)
        self.assertEqual(len(self.calls()), 2)

    def test_allow_hours_setting_blocks_outside_window(self):
        late = int(datetime.datetime(2026, 11, 24, 23, 30, tzinfo=KST).timestamp())
        self.go(now=late, extra_env={"AX_ALLOW_HOURS": "9-22"})
        self.assertIn("backend: outside-hours", self.log_text())
        self.assertEqual(self.calls(), [])
        (self.state / "backend-verdict").unlink()
        self.go(now=late, extra_env={"AX_ALLOW_HOURS": "22-6"})  # 밤을 넘기는 창
        self.assertEqual(len(self.calls()), 2)

    def test_bad_allow_hours_value_does_not_deploy(self):
        self.go(extra_env={"AX_ALLOW_HOURS": "abc"})
        self.assertIn("AX_ALLOW_HOURS 형식 오류", self.log_text())
        self.assertEqual(self.calls(), [])

    # ---- 미루기 상한 ----
    def test_defer_cap_alerts_once_and_never_forces_restart(self):
        env = {"AX_BUSY_URLS": "http://127.0.0.1:8001/fake-busy/safety"}
        (self.api / "busy-safety.json").write_text(json.dumps({"items": [{"id": 1}, {"id": 2}]}))
        self.go(extra_env=env)
        self.assertIn("backend: busy", self.log_text())
        self.assertEqual(self.slack(), [])  # 4시간 전에는 조용하다
        later = self.now + 4 * 3600 + 60
        self.go(extra_env=env, now=later)
        self.go(extra_env=env, now=later + 600)  # SHA 당 한 번만
        s = self.slack()
        self.assertEqual(len(s), 1)
        self.assertIn("4시간 넘게 미뤄지고", s[0])
        self.assertIn("강제로 재시작하지 않", s[0])
        self.assertEqual(self.calls(), [])  # 상한을 넘겨도 강제 재시작은 없다
        # 한가해지면 그때 배포한다
        (self.api / "busy-safety.json").write_text("[]")
        self.go(extra_env=env, now=later + 1200)
        self.assertEqual(len(self.calls()), 2)

    def test_busy_probe_failure_defers(self):
        self.go(extra_env={"AX_BUSY_URLS": "http://127.0.0.1:8001/fake-busy/none"})  # 응답 파일 없음 = 조회 실패
        self.assertIn("조회 실패", self.log_text())
        self.assertEqual(self.calls(), [])

    def test_defer_cap_is_configurable(self):
        (self.state / "backend-last-restart").write_text(f"{self.now - 60}\n")
        env = {"AX_BACKEND_DEFER_MAX_SEC": "300"}
        self.go(extra_env=env)
        self.go(extra_env=env, now=self.now + 400)
        self.assertEqual(len(self.slack()), 1)

    # ---- 공지 ----
    def test_announce_failure_aborts_deploy_and_retries_next_cycle(self):
        (self.tmp / "slack-fail").write_text("x")
        r = self.go()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.head(self.prod), self.base)
        self.assertIn("announce-failed", self.log_text())
        self.assertFalse((self.state / "backend-in-progress").exists())
        (self.tmp / "slack-fail").unlink()
        self.go()
        self.assertEqual(len(self.calls()), 2)

    def test_missing_webhook_file_aborts_deploy(self):
        self.hook.unlink()
        self.go()
        self.assertEqual(self.calls(), [])

    def test_announce_is_not_repeated_for_same_sha(self):
        # 게이트가 바쁨(끝 코드 4)이라 이번 주기를 건너뛰어도 다음 주기에 같은 공지를 또 올리지 않는다
        self.go(extra_env={"FAKE_GATE_FAIL": "deploy-bundle:*", "FAKE_GATE_RC": "4"})
        self.assertEqual(self.calls(), [f"deploy-bundle {self.t8} --announced"])
        self.go()
        self.assertEqual(len([s for s in self.slack() if "곧 배포하고 재시작" in s]), 1)
        self.assertEqual(self.head(self.prod), self.target)

    # ---- 실패와 되돌림 ----
    def test_restart_failure_rolls_back_in_order(self):
        r = self.go(extra_env={"FAKE_GATE_FAIL": f"restart-backend:{self.target}"})
        self.assertEqual(r.returncode, 0, r.stderr + self.log_text())
        self.assertEqual(self.calls(), [
            f"deploy-bundle {self.t8} --announced",
            f"restart-backend {self.t8} --ack-data-loss --announced",
            f"rollback-bundle {self.t8} --announced",
            f"rollback-code {self.t8} --announced",
            f"restart-backend {self.b8} --ack-data-loss --announced"])
        self.assertEqual(self.head(self.prod), self.base)
        self.assertIn("되돌렸습니다", self.slack()[-1])
        self.assertEqual((self.state / "backend-skipped-ref").read_text().strip(), self.target)
        self.assertFalse((self.state / "backend-in-progress").exists())
        # 같은 SHA 는 다시 시도하지 않는다
        self.go()
        self.assertEqual(len(self.calls()), 5)
        self.assertIn("backend: skipped-ref", self.log_text())
        # 새 커밋이 오면 다시 시도한다(되돌림 재시작으로 최소 간격이 걸리므로 시각을 넉넉히 민다)
        self.add_commit({"backend/a.py": "v3\n"}, at=COMMIT_AT + 10)
        self.go(now=self.now + 7200)
        self.assertEqual(self.calls()[5], f"deploy-bundle {self.target[:8]} --announced")

    def test_restart_failure_with_uv_sync_restores_environment(self):
        self.add_commit({"backend/uv.lock": "lock\n"})
        self.go(extra_env={"FAKE_GATE_FAIL": f"restart-backend:{self.target}"})
        self.assertEqual(self.calls(), [
            f"deploy-bundle {self.t8} --announced", f"uv-sync {self.t8} --announced",
            f"restart-backend {self.t8} --ack-data-loss --announced",
            f"rollback-bundle {self.t8} --announced", f"rollback-code {self.t8} --announced",
            f"uv-sync {self.b8} --announced", f"restart-backend {self.b8} --ack-data-loss --announced"])

    def test_uv_sync_failure_rolls_back_without_restart(self):
        self.add_commit({"backend/uv.lock": "lock\n"})
        self.go(extra_env={"FAKE_GATE_FAIL": f"uv-sync:{self.target}"})
        self.assertEqual(self.calls(), [
            f"deploy-bundle {self.t8} --announced", f"uv-sync {self.t8} --announced",
            f"rollback-bundle {self.t8} --announced", f"rollback-code {self.t8} --announced",
            f"uv-sync {self.b8} --announced"])  # 서비스는 재시작한 적이 없어 다시 재시작하지 않는다
        self.assertEqual(self.head(self.prod), self.base)

    def test_deploy_bundle_failure_before_code_moved(self):
        self.go(extra_env={"FAKE_GATE_FAIL": "deploy-bundle:*"})
        self.assertEqual(self.calls(), [f"deploy-bundle {self.t8} --announced", f"rollback-bundle {self.t8} --announced",
                                        f"rollback-code {self.t8} --announced"])
        self.assertEqual(self.head(self.prod), self.base)
        self.assertFalse((self.state / "backend-in-progress").exists())
        self.assertIn("되돌렸습니다", self.slack()[-1])

    def test_rollback_failure_halts_and_calls_human(self):
        r = self.go(extra_env={"FAKE_GATE_FAIL": "restart-backend:*"})  # 직전 SHA 재시작도 실패
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls()[-1], f"restart-backend {self.b8} --ack-data-loss --announced")
        self.assertIn("되돌림도 실패", self.slack()[-1])
        self.assertIn("halted", (self.state / "backend-in-progress").read_text())
        n = len(self.calls())
        self.add_commit({"backend/a.py": "v3\n"})
        self.go(now=self.now + 86400)
        self.assertEqual(len(self.calls()), n)  # 사람이 지울 때까지 아무것도 안 한다
        self.assertIn("backend: in-progress-file", self.log_text())

    def test_rollback_code_failure_is_rollback_failure(self):
        r = self.go(extra_env={"FAKE_GATE_FAIL": f"restart-backend:{self.target} rollback-code:{self.target}"})
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(f"restart-backend {self.b8} --ack-data-loss --announced", self.calls())  # 코드가 안 돌아왔으면 재시작하지 않는다
        self.assertIn("되돌림도 실패", self.slack()[-1])

    def test_gate_busy_on_first_step_is_not_a_failure(self):
        self.go(extra_env={"FAKE_GATE_FAIL": "deploy-bundle:*", "FAKE_GATE_RC": "4"})
        self.assertEqual(self.calls(), [f"deploy-bundle {self.t8} --announced"])  # 롤백 호출 없음
        self.assertFalse((self.state / "backend-in-progress").exists())
        self.assertFalse((self.state / "backend-skipped-ref").exists())

    # ---- 정지·잠금·동시 실행 ----
    def test_pause_helper_stops_everything(self):
        helper = self.tmp / "paused-helper"
        helper.write_text("#!/usr/bin/env bash\necho 'test 정지' >&2\nexit 0\n", newline="\n")
        helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
        (self.tmp / "autodeploy.pause").write_text("until_epoch=99999999999\n")
        r = self.go(extra_env={"AX_GATE_BIN": str(helper)})
        self.assertEqual(r.returncode, 0)
        self.assertIn("paused:", self.log_text())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.slack(), [])

    def test_pause_file_without_helper_fails_safe(self):
        (self.tmp / "autodeploy.pause").write_text("until_epoch=99999999999\n")
        self.go()
        self.assertIn("paused-no-helper", self.log_text())
        self.assertEqual(self.calls(), [])

    def test_pause_not_connected_fails_safe(self):
        self.go(extra_env={"AX_PAUSE_FILE": ""})
        self.assertIn("pause-not-connected", self.log_text())
        self.assertEqual(self.calls(), [])

    def test_deploy_lock_and_in_progress_file_block(self):
        (self.tmp / "deploy.lock").write_text("x")
        self.go()
        self.assertIn("backend: deploy-lock", self.log_text())
        (self.tmp / "deploy.lock").unlink()
        (self.state / "backend-in-progress").write_text("deploy a b\n")
        self.go()
        self.assertIn("backend: in-progress-file", self.log_text())
        self.assertEqual(self.calls(), [])

    def test_concurrent_run_is_skipped_by_flock(self):
        r = self.go(extra_env={"FAKE_FLOCK_BUSY": "1"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("busy:", self.log_text())
        self.assertEqual(self.calls(), [])

    def test_dry_run_flag_with_stage_on_changes_nothing(self):
        r = self.go(args=("--dry-run",))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would-deploy", r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.slack(), [])
        self.assertEqual(self.head(self.prod), self.base)

    # ---- 단계 값 ----
    def test_invalid_stage_value_does_not_block_frontend(self):
        self.add_commit({"frontend/src/x.tsx": "export {}\n"})
        deploy_sh = self.tmp / "deploy.sh"
        deploy_sh.write_text("#!/usr/bin/env bash\nexit 1\n", newline="\n")  # 프론트 배포 시도 여부만 본다
        deploy_sh.chmod(deploy_sh.stat().st_mode | stat.S_IXUSR)
        r = self.go(stage="yes", extra_env={"AX_DEPLOY_SH": str(deploy_sh), "AX_WEB_ROOT_90": str(self.tmp / "w90"),
                                            "AX_WEB_ROOT_91": str(self.tmp / "w91")})
        t = self.log_text()
        self.assertIn("알 수 없는 값", t)
        self.assertIn("start:", t)  # 프론트 단계까지 갔다
        self.assertIn("deploy.sh 실패", t)
        self.assertNotIn("미구현", t)
        self.assertEqual(self.calls(), [])
        self.assertEqual(r.returncode, 1)  # 이 끝 코드는 가짜 deploy.sh 실패 때문이다(단계 값 오류 때문이 아님)

    def test_stage_off_never_touches_gate_or_slack(self):
        self.go(stage="off")
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.slack(), [])


if __name__ == "__main__":
    unittest.main()
