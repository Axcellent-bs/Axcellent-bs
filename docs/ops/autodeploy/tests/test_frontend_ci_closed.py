"""ax-autodeploy 화면(프론트·홍보) 단계의 CI 확인용 토큰 정책 시험: 토큰이 없거나 비어 있으면 배포하지 않는다(fail-closed).

서버·네트워크 없이 돈다(test_backend_stage.Harness: 임시 git 저장소, 가짜 curl). 배포 스크립트(deploy.sh)는 호출 여부만 남기는 대역이다.
실행: python -m unittest discover -s docs/ops/autodeploy/tests -v   (bash, git, python3 필요)
"""
import json
import stat
import unittest

from test_backend_stage import Harness

REASON = "CI 확인용 토큰 파일이 없거나 비어 있어 화면 배포를 보류"


class FrontendCiClosedTest(Harness):
    def setUp(self):
        super().setUp()
        # 백엔드 변경은 이미 배포된 것으로 보고, 기준 ref 를 거기에 둔 뒤 화면 파일만 바뀐 커밋을 올린다.
        (self.state / "last-deployed-ref").write_text(self.target + "\n")
        self.last = self.target
        self.add_commit({"frontend/src/x.tsx": "export const x = 1\n"}, msg="fe change")
        self.deploy_marker = self.tmp / "deploy-called"
        self.deploy_sh = self.tmp / "deploy.sh"
        self.deploy_sh.write_text(f'#!/usr/bin/env bash\necho "$AXCELLENT_REF" >> "{self.deploy_marker.as_posix()}"\nexit 0\n', newline="\n")
        self.deploy_sh.chmod(self.deploy_sh.stat().st_mode | stat.S_IXUSR)
        self.promo_root = self.tmp / "promo"
        self.promo_root.mkdir()

    def go(self, **kw):
        env = {"AX_DEPLOY_SH": self.deploy_sh.as_posix(),
               "AX_WEB_ROOT_90": str(self.tmp / "web90"), "AX_WEB_ROOT_91": str(self.tmp / "web91"),
               "AX_PROMO_ROOT": str(self.promo_root), "AX_URL_PROMO": "off"}
        env.update(kw.pop("extra_env", {}))
        return self.run_script(stage=kw.pop("stage", "off"), extra_env=env, **kw)

    def last_ref(self):
        return (self.state / "last-deployed-ref").read_text().strip()

    def assert_held(self, r, state_text):
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self.log_text()
        self.assertIn(f"wait: {state_text}", t)
        self.assertIn("-> 이번 주기엔 배포하지 않음", t)
        self.assertIn(REASON, t)
        self.assertNotIn("start:", t)
        self.assertFalse(self.deploy_marker.exists(), "deploy.sh 가 불리면 안 된다")
        self.assertEqual(self.last_ref(), self.last, "기준 ref 가 바뀌면 안 된다")
        self.assertIn("ci-wait", (self.state / "last-run").read_text())

    # ---- 토큰 없음/비어 있음: 배포 안 함 ----
    def test_missing_token_file_holds_frontend_deploy(self):
        (self.state / "token").unlink()
        self.assert_held(self.go(), "ci-check=off")
        self.assertIn("ci-check=off(no token file)", self.log_text())

    def test_empty_token_file_holds_frontend_deploy(self):
        (self.state / "token").write_text("")
        self.assert_held(self.go(), "ci-check=off(empty token file)")

    def test_whitespace_only_token_file_holds_frontend_deploy(self):
        (self.state / "token").write_text("  \r\n\n")
        self.assert_held(self.go(), "ci-check=off(empty token file)")

    def test_hold_repeats_every_cycle_without_changing_ref(self):
        (self.state / "token").unlink()
        self.go()
        self.assert_held(self.go(), "ci-check=off")
        self.assertEqual(self.log_text().count("-> 이번 주기엔 배포하지 않음"), 2)

    def test_dry_run_explains_why_without_token(self):
        (self.state / "token").unlink()
        r = self.go(args=("--dry-run",))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CI: ci-check=off(no token file) (rc=2;", r.stdout)
        self.assertIn(REASON, r.stdout)
        self.assertFalse(self.deploy_marker.exists())
        self.assertEqual(self.last_ref(), self.last)

    def test_promo_only_change_is_also_held_without_token(self):
        self.add_commit({"docs/promo/assets/a.png": "png\n"}, msg="promo change")
        (self.state / "token").unlink()
        r = self.go()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wait: ci-check=off", self.log_text())
        self.assertEqual(list(self.promo_root.rglob("*")), [])
        self.assertEqual(self.last_ref(), self.last)

    def test_backend_hold_without_token_leaves_promo_catchup_unrun(self):
        # 백엔드 변경 + 홍보 파일 변경, 토큰 없음: 백엔드가 ci-off 로 멈추므로 promo_catchup 은 돌지 않고 화면 단계도 멈춘다.
        (self.state / "last-deployed-ref").write_text(self.base + "\n")
        self.last = self.base
        self.add_commit({"docs/promo/assets/a.png": "png\n"}, msg="promo change")
        (self.state / "token").unlink()
        r = self.go(stage="dry-run", now=self.target_time())
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self.log_text()
        self.assertIn("backend-dry-run: ci-off", t)
        self.assertNotIn("promo: 백엔드 배포", t)
        self.assertIn("wait: ci-check=off", t)
        self.assertEqual(list(self.promo_root.rglob("*")), [])
        self.assertEqual(self.last_ref(), self.base)
        self.assertFalse(self.deploy_marker.exists())

    def target_time(self):
        from test_backend_stage import COMMIT_AT
        return COMMIT_AT + 3600

    # ---- 토큰 있음: 기존 동작 그대로 ----
    def test_token_and_ci_pass_attempts_deploy(self):
        self.ok_checks()
        r = self.go()
        t = self.log_text()
        self.assertIn("start:", t)
        self.assertIn("ci-check=pass(", t)
        self.assertNotIn("wait: ci-check", t)
        self.assertTrue(self.deploy_marker.exists(), "CI 통과면 deploy.sh 를 불러야 한다")
        self.assertEqual(self.deploy_marker.read_text().split()[0], self.target)
        # 대역 환경에는 화면 확인(HTTP 200)이 없어 이후 검증에서 실패 처리되는 것은 이 시험의 관심 밖
        self.assertIn(r.returncode, (0, 1))

    def test_token_and_ci_pending_holds(self):
        (self.api / "checks.json").write_text(json.dumps({"check_runs": [
            {"id": 1, "name": "ci", "status": "in_progress", "conclusion": None}]}))
        r = self.go()
        self.assertEqual(r.returncode, 0, r.stderr)
        t = self.log_text()
        self.assertIn("wait: ci-check=pending(", t)
        self.assertNotIn(REASON, t)
        self.assertFalse(self.deploy_marker.exists())
        self.assertEqual(self.last_ref(), self.last)

    def test_token_and_ci_failed_holds(self):
        (self.api / "checks.json").write_text(json.dumps({"check_runs": [
            {"id": 1, "name": "ci", "status": "completed", "conclusion": "failure"}]}))
        r = self.go()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wait: ci-check=FAILED(", self.log_text())
        self.assertNotIn(REASON, self.log_text())
        self.assertFalse(self.deploy_marker.exists())

    def test_token_and_api_error_holds(self):
        (self.api / "checks.json").unlink()
        r = self.go()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wait: ci-check=api-error(", self.log_text())
        self.assertFalse(self.deploy_marker.exists())


if __name__ == "__main__":
    unittest.main()
