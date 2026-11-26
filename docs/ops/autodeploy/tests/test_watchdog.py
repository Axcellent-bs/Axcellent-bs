"""ax-autodeploy-watchdog 시험. 서버·네트워크·systemctl 없이 돈다.

임시 AX_STATE_DIR, 가짜 AX_WD_NOW, 환경 변수로 주입한 타이머·서비스 상태, 가짜 curl(보낸 본문을 파일에 쌓는다)을 쓴다.
실행: python -m unittest discover -s docs/ops/autodeploy/tests -v   (bash 필요)
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent / "ax-autodeploy-watchdog").as_posix()
NOW = 1_800_000_000
SHA = "3d41963707d1d6eacbfdaa318067bbf286b69889"
FAKE_CURL = """#!/usr/bin/env bash
# 가짜 curl: -K - 의 표준 입력은 버리고, --data-binary @파일 의 내용을 기록한다
cat > /dev/null
while (( $# )); do
  if [[ "$1" == --data-binary ]]; then f="${2#@}"; cat "$f" >> "${FAKE_CURL_OUT}"; echo >> "${FAKE_CURL_OUT}"; fi
  shift
done
[[ -n "${FAKE_CURL_FAIL:-}" ]] && exit 22
exit 0
"""


def ts(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S+0000", time.gmtime(epoch))


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wdtest-"))
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.log = self.tmp / "ax-autodeploy.log"
        self.pause = self.tmp / "pause"
        self.hook = self.tmp / "slack-webhook"
        self.hook.write_text("https://hooks.fake.invalid/T000/B000/placeholder\n")  # 시험용 가짜 주소
        self.curl = self.tmp / "curl"
        self.curl.write_text(FAKE_CURL, newline="\n")
        self.curl.chmod(self.curl.stat().st_mode | stat.S_IXUSR)
        self.out = self.tmp / "slack.out"
        self.touch("last-run", f"2026-11-25 16:01:03+0800 ci-wait {SHA[:8]}\n", 60)  # 정상: 1분 전

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, name, text="", age=0):
        p = self.state / name
        p.write_text(text)
        os.utime(p, (NOW - age, NOW - age))
        return p

    def run_wd(self, *args, **env):
        e = dict(os.environ)
        e.update({
            "AX_STATE_DIR": str(self.state), "AX_ENV_FILE": str(self.tmp / "no-env"), "AX_WD_LOG_FILE": str(self.log),
            "AX_WD_NOW": str(NOW), "AX_WD_TIMER_STATE": "active", "AX_WD_SERVICE_STATE": "inactive",
            "AX_WD_LOCK_HELD": "0", "AX_WD_SLACK_HOOK": str(self.hook), "AX_WD_CURL": self.curl.as_posix(),
            "AX_WD_HOST": "testhost", "FAKE_CURL_OUT": str(self.out), "AX_PAUSE_FILE": "",
        })
        e.update({k: str(v) for k, v in env.items()})
        return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=e, encoding="utf-8")

    def js(self, **env):
        r = self.run_wd("--check", "--json", **env)
        self.assertTrue(r.stdout.strip(), r.stderr)
        return r.returncode, json.loads(r.stdout)

    def sent(self):
        return self.out.read_text(encoding="utf-8").strip().splitlines() if self.out.exists() else []

    # ---- 판정 -------------------------------------------------------------------------------
    def test_ok(self):
        code, j = self.js()
        self.assertEqual((code, j["primary"], j["status"]), (0, "ok", "ok"))

    def test_last_run_stale(self):
        self.touch("last-run", "x\n", 1300)
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (1, "last-run-stale"))
        self.touch("last-run", "x\n", 4000)  # 3배 넘으면 심각
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (2, "last-run-stale"))

    def test_last_run_missing(self):
        (self.state / "last-run").unlink()
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (1, "last-run-stale"))

    def test_timer_inactive(self):
        code, j = self.js(AX_WD_TIMER_STATE="inactive")
        self.assertEqual((code, j["primary"]), (2, "timer-inactive"))

    def test_service_failed_and_start_limit(self):
        code, j = self.js(AX_WD_SERVICE_STATE="failed", AX_WD_SERVICE_RESULT="exit-code")
        self.assertEqual((code, j["primary"]), (1, "service-failed"))
        code, j = self.js(AX_WD_SERVICE_STATE="failed", AX_WD_SERVICE_RESULT="start-limit-hit")
        self.assertEqual((code, j["primary"]), (2, "service-start-limit"))

    def test_pause_before_expiry_is_not_a_problem(self):
        self.pause.write_text(f"until_epoch={NOW + 3600}\nuser=jaehun\n")
        code, j = self.js(AX_PAUSE_FILE=self.pause)
        self.assertEqual((code, j["primary"], j["paused"]), (0, "paused-approved", True))

    def test_pause_after_expiry_or_garbage_is_abnormal(self):
        self.pause.write_text(f"until_epoch={NOW - 10}\n")
        code, j = self.js(AX_PAUSE_FILE=self.pause)
        self.assertEqual((code, j["primary"]), (1, "pause-invalid"))
        self.pause.write_text("garbage\n")
        code, j = self.js(AX_PAUSE_FILE=self.pause)
        self.assertEqual((code, j["primary"]), (1, "pause-invalid"))

    def test_pause_path_from_env_file(self):
        self.pause.write_text(f"until_epoch={NOW + 100}\n")
        envf = self.tmp / "env"
        envf.write_text(f"AX_BACKEND_STAGE=on\nAX_PAUSE_FILE={self.pause.as_posix()}\n")
        code, j = self.js(AX_ENV_FILE=envf)
        self.assertEqual(j["primary"], "paused-approved")

    def test_in_progress_stuck_only_when_lock_free_or_too_old(self):
        self.touch("backend-in-progress", "deploy aaaa bbbb\n", 600)
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (2, "backend-in-progress-stuck"))
        # 실제 배포 중(잠금 점유, 오래되지 않음)은 정상
        self.touch("lock", "", 30)
        code, j = self.js(AX_WD_LOCK_HELD=1)
        self.assertEqual((code, j["primary"]), (0, "ok"))
        # 방금 생긴 것은 유예
        self.touch("backend-in-progress", "deploy aaaa bbbb\n", 10)
        code, j = self.js()
        self.assertEqual(j["primary"], "ok")

    def test_lock_held_long(self):
        self.touch("lock", "", 1600)
        code, j = self.js(AX_WD_LOCK_HELD=1)
        self.assertEqual((code, j["primary"]), (2, "lock-held-long"))
        self.touch("lock", "", 1000)
        code, j = self.js(AX_WD_LOCK_HELD=1)
        self.assertEqual(j["primary"], "ok")

    def _wait_setup(self, key, age_s, target=SHA):
        self.touch("backend-verdict", f"{target} {key}\n", 5)  # 매 주기 다시 써서 늘 새롭다
        self.log.write_text(f"{ts(NOW - age_s)} [1] backend: {key} 1fd99cee..{target[:8]} CI 대기\n", encoding="utf-8")

    def test_long_wait(self):
        self._wait_setup("ci-pending", 2000)
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (1, "long-wait"))
        self._wait_setup("ci-hold", 2000)
        self.assertEqual(self.js()[1]["primary"], "long-wait")

    def test_short_wait_and_other_verdicts_are_ok(self):
        self._wait_setup("ci-pending", 600)
        self.assertEqual(self.js()[1]["primary"], "ok")
        self._wait_setup("ci-fail", 5000)
        self.assertEqual(self.js()[1]["primary"], "ok")

    def test_wait_ended_by_deploy(self):
        self._wait_setup("ci-pending", 2400)
        with self.log.open("a", encoding="utf-8") as f:
            f.write(f"{ts(NOW - 100)} [2] done: 백엔드 배포 완료, 기준 ref -> 3d419637\n")
        self.assertEqual(self.js()[1]["primary"], "ok")

    def test_ci_off_stuck(self):
        self._wait_setup("ci-off", 700)  # 기준(600초) 이상: 경고
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (1, "ci-off-stuck"))
        self.assertIn("토큰 파일", j["detail"])
        self.assertIn("/etc/ax-autodeploy/gh-readonly-token", j["detail"])

    def test_ci_off_short_is_ok(self):
        self._wait_setup("ci-off", 300)
        self.assertEqual(self.js()[1]["primary"], "ok")

    def test_ci_off_three_times_is_critical(self):
        self._wait_setup("ci-off", 1900)
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (2, "ci-off-stuck"))
        self._wait_setup("ci-off", 1700)  # 3배(1800초) 미만은 경고
        self.assertEqual(self.js()[0], 1)

    def test_ci_off_threshold_env_and_not_long_wait(self):
        self._wait_setup("ci-off", 700)
        self.assertEqual(self.js(AX_WD_CIOFF_S=1000)[1]["primary"], "ok")
        # ci-off 는 long-wait(1800초 기준)가 아니라 자기 기준으로만 본다
        self._wait_setup("ci-pending", 700)
        self.assertEqual(self.js()[1]["primary"], "ok")

    def test_ci_off_not_reported_while_paused(self):
        self._wait_setup("ci-off", 2000)
        self.touch("pause", f"until_epoch={NOW + 3600}\n")
        self.assertEqual(self.js(AX_PAUSE_FILE=str(self.state / "pause"))[1]["primary"], "paused-approved")

    def test_ci_off_other_verdicts_do_not_trigger(self):
        for key in ("ci-fail", "outside-hours", "no-pr"):
            self._wait_setup(key, 5000)
            self.assertEqual(self.js()[1]["primary"], "ok", key)

    def test_ci_off_notify_dedupes(self):
        self._wait_setup("ci-off", 700)
        self.run_wd("--notify")
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("ci-off-stuck", self.sent()[0])
        self.assertNotIn("hooks.fake.invalid", self.sent()[0])
        self.run_wd("--notify", AX_WD_NOW=NOW + 300)
        self.assertEqual(len(self.sent()), 1)

    # ---- 같은 사유로 주기를 계속 건너뜀 · fetch 실패 ---------------------------------------------
    def _streak(self, text, minutes, last_ago=30, step=65):
        """minutes 분 길이로 step 초마다 이어지는 로그 줄을 쓴다(마지막 줄은 last_ago 초 전)"""
        n = minutes * 60 // step
        lines = [f"{ts(NOW - last_ago - (n - 1 - i) * step)} [{i}] {text}\n" for i in range(n)]
        self.log.write_text("".join(lines), encoding="utf-8")

    def test_skip_cycle_repeat(self):
        self._streak("backend: skip-cycle PR 조회 API 실패(e47786b7; 보류, 이번 주기 건너뜀)", 20)
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (1, "skip-cycle-repeat"))
        self.assertIn("PR 조회 API 실패", j["detail"])
        self._streak("backend: skip-cycle PR 조회 API 실패(e47786b7)", 40)
        self.assertEqual(self.js()[0], 2)

    def test_skip_cycle_short_or_ended_is_ok(self):
        self._streak("backend: skip-cycle x", 5)
        self.assertEqual(self.js()[1]["primary"], "ok")
        self._streak("backend: skip-cycle x", 40, last_ago=900)   # 이미 끝난 구간
        self.assertEqual(self.js()[1]["primary"], "ok")

    def test_skip_cycle_gap_resets_streak(self):
        # 30분 전 구간 + 10분 비움 + 최근 3분: 이어진 구간은 3분뿐
        old = [f"{ts(NOW - 2400 - i * 65)} [1] backend: skip-cycle x\n" for i in range(20)]
        new = [f"{ts(NOW - 30 - i * 65)} [2] backend: skip-cycle x\n" for i in range(3)]
        self.log.write_text("".join(old + new), encoding="utf-8")
        self.assertEqual(self.js()[1]["primary"], "ok")

    def test_fetch_failed_repeat(self):
        self._streak("error: git fetch origin dev 실패(다음 주기에 다시)", 20)
        code, j = self.js()
        self.assertEqual((code, j["primary"]), (1, "fetch-failed-repeat"))

    def test_skip_cycle_not_reported_while_paused(self):
        self._streak("backend: skip-cycle x", 30)
        self.touch("pause", f"until_epoch={NOW + 3600}\n")
        self.assertEqual(self.js(AX_PAUSE_FILE=str(self.state / "pause"))[1]["primary"], "paused-approved")

    # ---- 알림 ---------------------------------------------------------------------------------
    def test_notify_on_change_dedupes_and_recovers(self):
        env = {"AX_WD_TIMER_STATE": "inactive"}
        r = self.run_wd("--notify", **env)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("[watchdog]", self.sent()[0])
        self.assertIn("timer-inactive", self.sent()[0])
        # 같은 상태 10분 뒤: 보내지 않는다
        self.run_wd("--notify", AX_WD_NOW=NOW + 600, **env)
        self.assertEqual(len(self.sent()), 1)
        # 1시간 뒤: 한 번 더(last-run 도 같이 최신으로)
        self.touch("last-run", "x\n", -3700 + 30)
        self.run_wd("--notify", AX_WD_NOW=NOW + 3700, **env)
        self.assertEqual(len(self.sent()), 2)
        # 정상 복귀: 복구 알림 1번
        self.touch("last-run", "x\n", -3800 + 30)
        r = self.run_wd("--notify", AX_WD_NOW=NOW + 3800)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(self.sent()), 3)
        self.assertIn("복구", self.sent()[2])
        self.run_wd("--notify", AX_WD_NOW=NOW + 3900)
        self.assertEqual(len(self.sent()), 3)

    def test_first_ok_run_sends_nothing_and_heartbeat_once_a_day(self):
        self.run_wd("--notify")
        self.assertEqual(self.sent(), [])
        self.run_wd("--heartbeat")
        self.assertEqual(len(self.sent()), 1)
        self.assertIn("하루 1회", self.sent()[0])
        self.touch("last-run", "x\n", -3600 + 30)
        self.run_wd("--heartbeat", AX_WD_NOW=NOW + 3600)
        self.assertEqual(len(self.sent()), 1)
        self.touch("last-run", "x\n", -90000 + 30)
        self.run_wd("--heartbeat", AX_WD_NOW=NOW + 90000)
        self.assertEqual(len(self.sent()), 2)

    def test_send_failure_retries_next_cycle_and_never_prints_hook(self):
        r = self.run_wd("--notify", AX_WD_TIMER_STATE="inactive", FAKE_CURL_FAIL="1")
        self.assertNotIn("hooks.fake.invalid", r.stdout + r.stderr)
        self.assertFalse((self.state / "watchdog-notified").exists())
        self.run_wd("--notify", AX_WD_TIMER_STATE="inactive")
        self.assertEqual(len(self.sent()), 2)  # 실패한 시도 1회(가짜 curl 이 기록) + 재시도 1회
        self.assertTrue((self.state / "watchdog-notified").exists())

    def test_check_never_writes_or_sends(self):
        before = sorted(p.name for p in self.state.iterdir())
        self.run_wd("--check", AX_WD_TIMER_STATE="inactive")
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), before)
        self.assertEqual(self.sent(), [])

    def test_recover_is_refused(self):
        r = self.run_wd("--recover")
        self.assertEqual(r.returncode, 64)
        self.assertIn("알림 전용", r.stderr)

    def test_human_line_has_prefix(self):
        r = self.run_wd("--check", AX_WD_TIMER_STATE="inactive")
        self.assertTrue(r.stdout.startswith("[watchdog] 심각: timer-inactive"), r.stdout)


if __name__ == "__main__":
    unittest.main()
