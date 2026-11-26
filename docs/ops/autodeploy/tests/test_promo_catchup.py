"""ax-autodeploy: 백엔드를 배포한 주기에 같은 묶음의 홍보 파일을 올리는 promo_catchup 과 promo_allowed 시험.

스크립트 전체를 돌리지 않고 두 함수 본문만 꺼내 임시 git 저장소·임시 PROMO_ROOT 에서 돌린다(sudo·chown 은 대역으로 바꾼다).
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "ax-autodeploy"


def functions_text() -> str:
    s = SCRIPT.read_text(encoding="utf-8")
    start = s.index("promo_allowed() {")
    end = s.index("be_rc=0\nbackend_stage")
    return s[start:end]


HARNESS = r'''
set -u
git_src() { git -C "$SRC" "$@"; }
log() { echo "LOG $*"; }
say() { echo "SAY $*"; }
install() { # -o/-g 는 시험에서 못 쓴다. 쓴 순서를 남기고, FAIL_ON 이름이면 실패
  local a=() skip=0 last=""
  for x in "$@"; do
    if (( skip )); then skip=0; continue; fi
    case "$x" in -o|-g) skip=1; continue ;; esac
    a+=("$x"); last="$x"
  done
  echo "${last#"$PROMO_ROOT"/}" >> "$ORDER_FILE"
  if [[ -n "${FAIL_ON:-}" && "$last" == *"$FAIL_ON" ]]; then return 1; fi
  command install "${a[@]}"
}
SUDO=""
URL_PROMO=off
''' 


class PromoCatchup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pctest-"))
        self.src = self.tmp / "src"
        self.root = self.tmp / "www"
        self.state = self.tmp / "state"
        for d in (self.src / "docs/promo", self.root, self.state):
            d.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("docs/promo/index.html", "old index")
        self.write("docs/promo/app.js", "app")
        self.c1 = self.commit("c1")
        (self.root / "index.html").write_text("old index")
        (self.root / "app.js").write_text("app")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.src), *a], check=True, capture_output=True, text=True).stdout.strip()

    def write(self, rel, text):
        p = self.src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode() if isinstance(text, str) else text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def run_fn(self, call, dry=0, fail_on=""):
        order = self.tmp / "order.txt"
        order.write_text("")
        script = HARNESS + functions_text() + f"\nDRY={dry}\n{call}\necho RC=$?\n"
        env = dict(os.environ, SRC=str(self.src), PROMO_ROOT=str(self.root), STATE_DIR=str(self.state),
                   ORDER_FILE=str(order), FAIL_ON=fail_on)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, encoding="utf-8")
        rc = int(re.search(r"RC=(\d+)", r.stdout).group(1))
        return rc, r.stdout, order.read_text().split()

    def bundle(self):
        """#1907 모양: 새 릴스 파일 + 바뀐 index.html 과 app.js + 올리면 안 되는 문서·소스"""
        self.write("docs/promo/index.html", "new index")
        self.write("docs/promo/app.js", "new app")
        self.write("docs/promo/assets/instagram/catbooks/r1.mp4", b"\x00mp4")
        self.write("docs/promo/instagram/assets/catbooks/r2-poster.jpg", b"\xffjpg")
        self.write("docs/promo/README.md", "doc")
        self.write("docs/promo/content.mjs", "src")
        self.write("docs/promo/data.json", "{}")
        self.write("docs/promo/other/secret.txt", "no")
        return self.commit("c2")

    def test_copies_new_files_first_and_index_html_last(self):
        c2 = self.bundle()
        rc, out, order = self.run_fn(f'promo_catchup "{self.c1}" "{c2}"')
        self.assertEqual(rc, 0, out)
        self.assertEqual(order[-1], "index.html")
        self.assertEqual((self.root / "index.html").read_text(), "new index")
        self.assertEqual((self.root / "app.js").read_text(), "new app")
        self.assertTrue((self.root / "assets/instagram/catbooks/r1.mp4").exists())
        self.assertTrue((self.root / "instagram/assets/catbooks/r2-poster.jpg").exists())
        self.assertIn("promo: 백엔드 배포", out)

    def test_never_copies_docs_sources_or_unknown_new_paths(self):
        c2 = self.bundle()
        self.run_fn(f'promo_catchup "{self.c1}" "{c2}"')
        for rel in ("README.md", "content.mjs", "data.json", "other/secret.txt"):
            self.assertFalse((self.root / rel).exists(), rel)

    def test_nothing_to_do_returns_ok_without_touching_anything(self):
        self.write("docs/promo/README.md", "doc only")
        c2 = self.commit("docs")
        rc, out, order = self.run_fn(f'promo_catchup "{self.c1}" "{c2}"')
        self.assertEqual((rc, order), (0, []))
        self.assertEqual((self.root / "index.html").read_text(), "old index")

    def test_failure_restores_everything_and_returns_error(self):
        c2 = self.bundle()
        rc, out, order = self.run_fn(f'promo_catchup "{self.c1}" "{c2}"', fail_on="index.html")
        self.assertEqual(rc, 1, out)
        self.assertEqual((self.root / "index.html").read_text(), "old index")
        self.assertEqual((self.root / "app.js").read_text(), "new app".replace("new app", "app"))
        self.assertFalse((self.root / "assets/instagram/catbooks/r1.mp4").exists())
        self.assertIn("restore:", out)

    def test_dry_run_only_reports(self):
        c2 = self.bundle()
        rc, out, order = self.run_fn(f'promo_catchup "{self.c1}" "{c2}"', dry=1)
        self.assertEqual((rc, order), (0, []))
        self.assertIn("SAY 백엔드 배포 뒤 홍보 파일", out)
        self.assertEqual((self.root / "index.html").read_text(), "old index")

    def test_promo_allowed_rules(self):
        _, out, _ = self.run_fn(
            'for r in index.html assets/new.mp4 instagram/assets/x.jpg README.md a.json b.mjs unknown/new.png; '
            'do promo_allowed "$r" && echo "yes $r" || echo "no $r"; done'
        )
        yes = {l.split()[1] for l in out.splitlines() if l.startswith("yes ")}
        no = {l.split()[1] for l in out.splitlines() if l.startswith("no ")}
        self.assertEqual(yes, {"index.html", "assets/new.mp4", "instagram/assets/x.jpg"})
        self.assertEqual(no, {"README.md", "a.json", "b.mjs", "unknown/new.png"})

    def test_script_calls_catchup_before_advancing_on_backend_deploy(self):
        s = SCRIPT.read_text(encoding="utf-8")
        i = s.index("  10) # 배포 완료")
        j = s.index("  11) heartbeat")
        block = s[i:j]
        self.assertLess(block.index("promo_catchup"), block.index("advance_ref"))


if __name__ == "__main__":
    unittest.main()
