"""gateway_allow_check.py 단위 테스트 (표준 라이브러리 unittest, pytest 로도 돈다).

값은 전부 가짜 예시다. 실제 서버·게이트웨이는 건드리지 않는다(임시 폴더의 파일만 읽고 쓴다).
"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("gateway_allow_check", HERE.parent / "gateway_allow_check.py")
chk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(chk)

CARD = {
    "grade": "read",
    "allowed_requesters": ["gateway-x"],
    "skills": [{"id": "list_items", "grade": "read"}, {"id": "get_item"}, {"id": "write_item", "grade": "write"}],
}


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = chk.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class NameTests(unittest.TestCase):
    def test_valid_and_invalid_names(self):
        for ok in ("ab", "list_items", "a1_b2", "a" * 64):
            chk.check_name(ok)
        for bad in ("", "a", "A1", "1a", "a-b", "a b", "a" * 65, "a\n", "한글", None):
            with self.subTest(name=bad), self.assertRaises(chk.Rejected):
                chk.check_name(bad)


class CardTests(unittest.TestCase):
    def test_read_skill_passes_with_card_or_skill_grade(self):
        self.assertEqual(chk.check_card(CARD, "list_items", "gateway-x"), "read")
        self.assertEqual(chk.check_card(CARD, "get_item"), "read")
        # 카드 수준 grade 가 없고 skill 수준만 read 여도 통과
        card = {"skills": [{"id": "x1", "grade": "read"}]}
        self.assertEqual(chk.check_card(card, "x1"), "read")

    def test_rejections(self):
        cases = [
            (CARD, "write_item", "", "grade-not-read"),
            (dict(CARD, grade="write"), "list_items", "", "grade-not-read"),
            (dict(CARD, grade="READ"), "list_items", "", "grade-not-read"),
            (dict(CARD, grade=["read"]), "list_items", "", "grade-not-read"),
            ({"skills": [{"id": "x1"}]}, "x1", "", "grade-missing"),
            (CARD, "nothing", "", "skill-not-in-card"),
            ({"skills": [{"id": "x1", "grade": "read"}, {"id": "x1", "grade": "read"}]}, "x1", "", "skill-ambiguous"),
            (CARD, "list_items", "other", "requester-not-allowed"),
            ({k: v for k, v in CARD.items() if k != "allowed_requesters"}, "list_items", "gateway-x",
             "requester-missing"),
            (dict(CARD, allowed_requesters="gateway-x"), "list_items", "gateway-x", "requester-missing"),
        ]
        for card, skill, requester, code in cases:
            with self.subTest(code=code, skill=skill), self.assertRaises(chk.Rejected) as cm:
                chk.check_card(card, skill, requester)
            self.assertEqual(cm.exception.code, code)

    def test_skill_level_requesters_must_also_match(self):
        card = dict(CARD, skills=[{"id": "x1", "grade": "read", "allowed_requesters": ["someone"]}])
        with self.assertRaises(chk.Rejected):
            chk.check_card(card, "x1", "gateway-x")

    def test_missing_skills_is_a_config_error(self):
        with self.assertRaises(chk.ConfigError):
            chk.check_card({"grade": "read"}, "x1")


class FileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gwchk")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def put(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8", newline="")
        return str(path)

    def test_card_cli(self):
        card = self.put("card.json", json.dumps(CARD))
        rc, out, _ = run_cli("card", "--card", card, "--skill", "list_items", "--requester", "gateway-x")
        self.assertEqual((rc, out.strip()), (0, "OK grade=read"))
        rc, _, err = run_cli("card", "--card", card, "--skill", "write_item")
        self.assertEqual(rc, 2)
        self.assertIn("REJECT grade-not-read", err)
        rc, _, err = run_cli("card", "--card", card, "--skill", "Bad-Name")
        self.assertEqual(rc, 2)

    def test_bad_cards_are_config_errors(self):
        for text in ("not json", "[1, 2]", '{"grade": "write", "grade": "read", "skills": []}'):
            with self.subTest(text=text):
                rc, _, err = run_cli("card", "--card", self.put("c.json", text), "--skill", "list_items")
                self.assertEqual(rc, 3)
                self.assertIn("CONFIG", err)
        rc, _, _ = run_cli("card", "--card", str(self.dir / "missing.json"), "--skill", "list_items")
        self.assertEqual(rc, 3)

    def test_card_symlink_and_size_limit(self):
        card = self.put("card.json", json.dumps(CARD))
        link = self.dir / "link.json"
        try:
            os.symlink(card, link)
        except (OSError, NotImplementedError):
            self.skipTest("심볼릭 링크를 만들 수 없다")
        rc, _, _ = run_cli("card", "--card", str(link), "--skill", "list_items")
        self.assertEqual(rc, 3)
        big = self.put("big.json", " " * (chk.MAX_FILE_BYTES + 1))
        rc, _, _ = run_cli("card", "--card", big, "--skill", "list_items")
        self.assertEqual(rc, 3)

    def test_list_add_appends_and_keeps_original_text(self):
        src = self.put("list.txt", "# header\nexisting_one\n\nexisting_two")
        out = str(self.dir / "out.txt")
        rc, _, _ = run_cli("list-add", "--file", src, "--out", out, "--skill", "new_skill")
        self.assertEqual(rc, 0)
        self.assertEqual(Path(out).read_text(encoding="utf-8"), "# header\nexisting_one\n\nexisting_two\nnew_skill\n")
        self.assertEqual(Path(src).read_text(encoding="utf-8"), "# header\nexisting_one\n\nexisting_two")
        rc, _, err = run_cli("list-add", "--file", src, "--out", out, "--skill", "existing_one")
        self.assertEqual(rc, 2)
        self.assertIn("REJECT duplicate", err)

    def test_empty_list_gets_one_line(self):
        src = self.put("list.txt", "")
        out = str(self.dir / "out.txt")
        self.assertEqual(run_cli("list-add", "--file", src, "--out", out, "--skill", "first_one")[0], 0)
        self.assertEqual(Path(out).read_text(encoding="utf-8"), "first_one\n")

    def test_unknown_list_format_is_refused_not_modified(self):
        for text in ('ALLOWED = ["a"]\n', "ok_one\r\nok_two\r\n", "Bad-Line\n", "ok_one # inline\n"):
            with self.subTest(text=text):
                src = self.put("list.txt", text)
                out = self.dir / "out.txt"
                rc, _, err = run_cli("list-add", "--file", src, "--out", str(out), "--skill", "new_skill")
                self.assertEqual(rc, 3)
                self.assertIn("CONFIG", err)
                self.assertFalse(out.exists())

    def test_list_has_and_check(self):
        src = self.put("list.txt", "# c\nexisting_one\n")
        self.assertEqual(run_cli("list-has", "--file", src, "--skill", "existing_one")[0], 0)
        self.assertEqual(run_cli("list-has", "--file", src, "--skill", "other_one")[0], 2)
        rc, out, _ = run_cli("list-check", "--file", src)
        self.assertEqual((rc, out.strip()), (0, "OK count=1"))
        self.assertEqual(run_cli("list-check", "--file", self.put("bad.txt", "{}\n"))[0], 3)

    def test_usage_errors(self):
        self.assertEqual(run_cli()[0], 3)
        self.assertEqual(run_cli("nope")[0], 3)
        self.assertEqual(run_cli("check-name")[0], 3)


if __name__ == "__main__":
    unittest.main()
