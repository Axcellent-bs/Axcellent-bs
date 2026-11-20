"""api-direct-env.sh 환경값 우선순위 테스트 (표준 라이브러리 unittest, pytest 로도 돈다).

임시 폴더만 쓰고 네트워크·서버는 건드리지 않는다. bash 가 없으면 건너뛴다.
실행: python -m pytest docs/ops/deploy/tests -q   (backend CI 의 testpaths 밖이라 CI 에서는 안 돈다)
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "api-direct-env.sh"
BASH = shutil.which("bash")
DEFAULT_BASE = "https://api-app.axcellent.cloud"
DEFAULT_SERVICES = "dashboard,orch,safety,planning,dev"


def run(args, env_extra=None, unset=()):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VITE_API_DIRECT")}
    env.update(env_extra or {})
    for k in unset:
        env.pop(k, None)
    return subprocess.run([BASH, str(SCRIPT), *args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)


def parse(out):
    return dict(line.split("=", 1) for line in out.splitlines() if line.startswith("VITE_"))


@unittest.skipUnless(BASH, "bash 없음")
class PrintEnvTests(unittest.TestCase):
    def test_syntax(self):
        self.assertEqual(subprocess.run([BASH, "-n", str(SCRIPT)]).returncode, 0)

    def test_defaults_when_unset(self):
        r = run(["--print-env"])
        self.assertEqual(r.returncode, 0, r.stderr)
        vals = parse(r.stdout)
        self.assertEqual(vals["VITE_API_DIRECT_BASE"], DEFAULT_BASE)
        self.assertEqual(vals["VITE_API_DIRECT_SERVICES"], DEFAULT_SERVICES)
        self.assertIn("직접 호출: 켜짐", r.stdout)

    def test_explicit_values_win(self):
        r = run(["--print-env"], {"VITE_API_DIRECT_BASE": "https://x.example", "VITE_API_DIRECT_SERVICES": "dashboard"})
        vals = parse(r.stdout)
        self.assertEqual(vals["VITE_API_DIRECT_BASE"], "https://x.example")
        self.assertEqual(vals["VITE_API_DIRECT_SERVICES"], "dashboard")
        self.assertIn("직접 호출: 켜짐", r.stdout)

    def test_empty_services_turns_off(self):
        r = run(["--print-env"], {"VITE_API_DIRECT_SERVICES": ""})
        vals = parse(r.stdout)
        self.assertEqual(vals["VITE_API_DIRECT_SERVICES"], "")
        self.assertEqual(vals["VITE_API_DIRECT_BASE"], DEFAULT_BASE)
        self.assertIn("직접 호출: 꺼짐", r.stdout)

    def test_empty_base_turns_off(self):
        r = run(["--print-env"], {"VITE_API_DIRECT_BASE": ""})
        vals = parse(r.stdout)
        self.assertEqual(vals["VITE_API_DIRECT_BASE"], "")
        self.assertEqual(vals["VITE_API_DIRECT_SERVICES"], DEFAULT_SERVICES)
        self.assertIn("직접 호출: 꺼짐", r.stdout)

    def test_log_shows_state_not_values(self):
        r = run(["--print-env"])
        first = r.stdout.splitlines()[0]
        self.assertEqual(first, "직접 호출: 켜짐")
        self.assertNotIn("api-app", first)

    def test_no_args_is_usage_error(self):
        self.assertEqual(run([]).returncode, 2)


@unittest.skipUnless(BASH, "bash 없음")
class VerifyTests(unittest.TestCase):
    def verify(self, files, env_extra=None):
        with tempfile.TemporaryDirectory() as d:
            for name, body in files.items():
                p = Path(d) / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            cmd = f'. "{SCRIPT.as_posix()}"; ax_api_direct_defaults >/dev/null; ax_api_direct_verify "{Path(d).as_posix()}"'
            env = {k: v for k, v in os.environ.items() if not k.startswith("VITE_API_DIRECT")}
            env.update(env_extra or {})
            return subprocess.run([BASH, "-c", cmd], env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_found_in_bundle(self):
        r = self.verify({"assets/index-1.js": f'var a="{DEFAULT_BASE}";'})
        self.assertEqual(r.returncode, 0)
        self.assertIn("포함됨", r.stdout)

    def test_missing_warns_and_returns_1(self):
        r = self.verify({"assets/index-1.js": "var a=1;"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("경고", r.stderr)

    def test_off_skips(self):
        r = self.verify({"assets/index-1.js": "var a=1;"}, {"VITE_API_DIRECT_SERVICES": ""})
        self.assertEqual(r.returncode, 0)
        self.assertIn("건너뜀", r.stdout)


if __name__ == "__main__":
    unittest.main()
