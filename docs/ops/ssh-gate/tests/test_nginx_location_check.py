"""nginx_location_check.py 단위 테스트 (표준 라이브러리 unittest, pytest 로도 돈다).

값은 전부 가짜 예시다(example-api, 포트 8201 등). 실제 서버 값은 여기 없다.

실행: python -m unittest discover -s docs/ops/ssh-gate/tests -v
"""

import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("nginx_location_check", HERE.parent / "nginx_location_check.py")
chk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(chk)

METHODS = ["POST"]
PREFIXES = ["/example-api/api/items/"]
UPSTREAMS = {"example-api": 8201, "other-api": 8202}
BLOCK_LINES = [
    "limit_except {method} { deny all; }",
    "rewrite ^/{segment}/(.*)$ /$1 break;",
    "proxy_pass http://127.0.0.1:{port};",
    "include snippets/example-proxy.conf;",
    "include snippets/example-auth.conf;",
]
GOOD = "^/example-api/api/items/cards/[^/]+/decision$"

CONF = """server {
    listen 8300;
    # AX-GATE-INSERT-BELOW
    location ~ ^/example-api/api/items/old/[^/]+/decision$ { limit_except POST { deny all; } }
    location /example-api/ {
        limit_except GET HEAD { deny all; }
    }
}
"""


def validate(regex, method="POST", methods=None, prefixes=None, upstreams=None, block_lines=None):
    return chk.validate(method, regex, METHODS if methods is None else methods,
                        PREFIXES if prefixes is None else prefixes,
                        UPSTREAMS if upstreams is None else upstreams,
                        BLOCK_LINES if block_lines is None else block_lines)


class AcceptTests(unittest.TestCase):
    def test_example_passes(self):
        self.assertEqual(validate(GOOD), ("example-api", 8201))

    def test_other_safe_forms(self):
        for regex in (
            "^/example-api/api/items/cards/[0-9]+/approve$",
            "^/example-api/api/items/(approve|reject)$",
            "^/example-api/api/items/files/[0-9a-f-]+/v1\\.json$",
            "^/example-api/api/items/x/[A-Za-z0-9_-]+/y/[a-z0-9-]+$",
        ):
            with self.subTest(regex=regex):
                validate(regex)

    def test_prefix_without_trailing_slash_matches_on_segment_boundary(self):
        validate("^/example-api/api/items/x$", prefixes=["/example-api/api"])
        validate("^/example-api/api$", prefixes=["/example-api/api"])

    def test_port_comes_from_the_first_segment(self):
        self.assertEqual(validate("^/other-api/v1/things/[^/]+/approve$", prefixes=["/other-api/"]),
                         ("other-api", 8202))


class RejectTests(unittest.TestCase):
    def assertRejected(self, code, regex, **kw):
        with self.assertRaises(chk.Rejected) as ctx:
            validate(regex, **kw)
        self.assertEqual(ctx.exception.code, code, regex)

    def test_anchors(self):
        self.assertRejected("not-anchored-start", "/example-api/api/items/x$")
        self.assertRejected("not-anchored-end", "^/example-api/api/items/x")
        self.assertRejected("not-anchored-end", "^/example-api/api/items/x\\$")
        self.assertRejected("anchor-inside", "^/example-api/api/items/x$|^/y$")

    def test_wildcards(self):
        for regex in (
            "^/example-api/api/items/.*$",
            "^/example-api/api/items/x.$",
            "^/example-api/api/items/x*$",
            "^/example-api/api/items/x?$",
            "^/example-api/api/items/x+$",
            "^/example-api/api/items/x{1,9}$",
        ):
            with self.subTest(regex=regex):
                self.assertRejected("wildcard", regex)

    def test_classes_and_groups(self):
        self.assertRejected("class-not-allowed", "^/example-api/api/items/[^?]+$")
        self.assertRejected("class-not-allowed", "^/example-api/api/items/[a-z/]+$")
        self.assertRejected("class-not-allowed", "^/example-api/api/items/[^/]*$")
        self.assertRejected("group-not-allowed", "^/example-api/api/items/(a|b/c)$")
        self.assertRejected("group-not-allowed", "^/example-api/api/items/(a|(b|c))$")
        self.assertRejected("group-not-allowed", "^/example-api/api/items/(?i)x$")
        self.assertRejected("group-not-allowed", "^/example-api/api/items/(a|)$")
        self.assertRejected("group-unclosed", "^/example-api/api/items/(a|b$")

    def test_escapes_and_odd_chars(self):
        self.assertRejected("bad-escape", "^/example-api/api/items/\\d$")
        self.assertRejected("bad-escape", "^/example-api/api/items/x\\/y$")
        for regex in (
            "^/example-api/api/items/x y$",
            "^/example-api/api/items/x;return 200$",
            "^/example-api/api/items/x\"$",
            "^/example-api/api/items/x#$",
            "^/example-api/api/items/x\n$",
            "^/example-api/api/items/한글$",
        ):
            with self.subTest(regex=regex):
                self.assertRejected("char-not-allowed", regex)

    def test_path_shape(self):
        self.assertRejected("empty-segment", "^/example-api/api/items//x$")
        self.assertRejected("dot-dot", "^/example-api/api/items/\\.\\./x$")
        self.assertRejected("too-long", "^/example-api/api/items/" + "a" * 200 + "$")
        self.assertRejected("first-segment", "^/[^/]+/api/items/x$", prefixes=["/example-api/"])
        self.assertRejected("first-segment", "^/example-api$", prefixes=["/example-api"])

    def test_prefix(self):
        self.assertRejected("prefix-not-allowed", "^/other-api/v1/things/x$")
        self.assertRejected("prefix-not-allowed", "^/example-api/api/other/x$")
        # 접두가 조각 경계가 아닌 곳에서 맞으면 안 된다
        self.assertRejected("prefix-not-allowed", "^/example-api/apix/y$", prefixes=["/example-api/api"])
        self.assertRejected("prefix-not-allowed", "^/example-api/api[0-9]+$", prefixes=["/example-api/api"])
        # 접두 한가운데가 묶음이면 접두 안이라고 볼 수 없다
        self.assertRejected("prefix-not-allowed", "^/example-api/[^/]+/items/x$")

    def test_method(self):
        self.assertRejected("method-not-allowed", GOOD, method="GET")
        self.assertRejected("method-not-allowed", GOOD, method="HEAD")
        self.assertRejected("method-not-allowed", GOOD, method="DELETE")  # 정책은 POST 만
        self.assertRejected("method-not-allowed", GOOD, method="POST GET")

    def test_upstream(self):
        self.assertRejected("upstream-unknown", GOOD, upstreams={"other-api": 8202})

    def test_config_errors(self):
        with self.assertRaises(chk.ConfigError):
            validate(GOOD, prefixes=[])
        with self.assertRaises(chk.ConfigError):
            validate(GOOD, prefixes=["/"])
        with self.assertRaises(chk.ConfigError):
            validate(GOOD, methods=["GET"])
        with self.assertRaises(chk.ConfigError):
            validate(GOOD, upstreams={"example-api": 80})


class BlockLinePolicyTests(unittest.TestCase):
    """블록 줄은 정책이 준다. 정책이 이상하면 설정 오류로 멈춘다."""

    def test_good_lines(self):
        chk.check_block_lines(BLOCK_LINES)
        chk.check_block_lines(["limit_except {method} { deny all; }", "proxy_pass http://127.0.0.1:{port};"])

    def test_bad_lines_are_config_errors(self):
        base = ["limit_except {method} { deny all; }", "proxy_pass http://127.0.0.1:{port};"]
        bad_sets = [
            [],                                                             # 비어 있음
            base[:1],                                                       # proxy_pass 없음
            base[1:],                                                       # 메서드 제한 없음
            base + ["proxy_pass http://127.0.0.1:{port};"],                 # proxy_pass 두 줄
            [base[0], "proxy_pass http://10.9.9.9:{port};"],                # 다른 호스트
            [base[0], "proxy_pass http://127.0.0.1:{port}/fixed;"],         # 경로 고정
            [base[0], "proxy_pass http://127.0.0.1:9999;"],                 # 포트를 정책 표에서 안 읽음
            base + ["limit_except {method} { allow all; }"],                # 메서드 제한 두 줄
            base + ["include snippets/x.conf"],                             # ; 없음
            base + ["add_header X \"y\";"],                                 # 따옴표
            base + ["set $x {unknown};"],                                   # 모르는 자리표시
            base + ["return 200 ok; }"],                                    # 중괄호 짝 안 맞음
            base + ["# comment;"],                                          # 주석 기호
            base + ["include a;\ninclude b;"],                              # 줄바꿈
            base + ["include snippets/x.conf;"] * 11,                       # 너무 많음
        ]
        for lines in bad_sets:
            with self.subTest(lines=lines):
                with self.assertRaises(chk.ConfigError):
                    chk.check_block_lines(lines)
                with self.assertRaises(chk.ConfigError):
                    validate(GOOD, block_lines=lines)


class RenderAndBlockTests(unittest.TestCase):
    def render(self, block_lines=None, **kw):
        args = dict(name="card-decision", user="owner-a", stamp="20260102-101500",
                    method="POST", regex=GOOD, segment="example-api", port=8201)
        args.update(kw)
        return chk.render_block(block_lines=BLOCK_LINES if block_lines is None else block_lines, **args)

    def test_render_is_one_line_block_built_from_policy_lines(self):
        self.assertEqual(self.render().splitlines(), [
            "# ax-gate: name=card-decision user=owner-a added=20260102-101500",
            "location ~ ^/example-api/api/items/cards/[^/]+/decision$ "
            "{ limit_except POST { deny all; } rewrite ^/example-api/(.*)$ /$1 break; "
            "proxy_pass http://127.0.0.1:8201; include snippets/example-proxy.conf; "
            "include snippets/example-auth.conf; }",
        ])

    def test_different_policy_lines_give_a_different_block(self):
        lines = ["limit_except {method} { deny all; }", "proxy_pass http://127.0.0.1:{port};"]
        block = self.render(block_lines=lines)
        self.assertEqual(block.splitlines()[1],
                         "location ~ " + GOOD + " { limit_except POST { deny all; } "
                         "proxy_pass http://127.0.0.1:8201; }")
        self.assertTrue(chk.check_block(block, METHODS, PREFIXES, UPSTREAMS, lines))
        # 다른 정책(블록 줄)으로 만든 블록은 통과하지 못한다
        with self.assertRaises(chk.Rejected):
            chk.check_block(block, METHODS, PREFIXES, UPSTREAMS, BLOCK_LINES)

    def test_render_rejects_bad_name(self):
        for name in ("Bad", "a b", "../x", "x;y", ""):
            with self.subTest(name=name), self.assertRaises(chk.Rejected):
                self.render(name=name)

    def test_check_block_accepts_rendered(self):
        self.assertTrue(chk.check_block(self.render(), METHODS, PREFIXES, UPSTREAMS, BLOCK_LINES))
        # 줄바꿈 위치만 다른 같은 블록도 받는다
        spread = self.render().replace("; ", ";\n    ").replace("{ limit", "{\n    limit")
        self.assertTrue(chk.check_block(spread, METHODS, PREFIXES, UPSTREAMS, BLOCK_LINES))

    def test_check_block_rejects_extra_directives(self):
        good = self.render()
        for extra in (
            "alias /etc/;",
            "return 200 ok;",
            "include snippets/other.conf;",
            "content_by_lua_block { }",
            "proxy_set_header Authorization x;",
            "client_max_body_size 5m;",
        ):
            with self.subTest(extra=extra):
                bad = good.replace(" proxy_pass", " " + extra + " proxy_pass")
                with self.assertRaises(chk.Rejected) as ctx:
                    chk.check_block(bad, METHODS, PREFIXES, UPSTREAMS, BLOCK_LINES)
                self.assertEqual(ctx.exception.code, "block-shape")
        with self.assertRaises(chk.Rejected):
            chk.check_block(good + "return 200;\n", METHODS, PREFIXES, UPSTREAMS, BLOCK_LINES)

    def test_check_block_rejects_changed_lines(self):
        good = self.render()
        for old, new in (
            ("http://127.0.0.1:8201;", "http://10.9.9.9:8201;"),
            ("http://127.0.0.1:8201;", "http://127.0.0.1:8201/other;"),
            ("http://127.0.0.1:8201;", "http://127.0.0.1:9999;"),
            ("limit_except POST { deny all; }", "limit_except POST { allow all; }"),
            ("limit_except POST { deny all; }", "limit_except GET { deny all; }"),
            (" limit_except POST { deny all; }", ""),
            (" include snippets/example-auth.conf;", ""),
            (" rewrite ^/example-api/(.*)$ /$1 break;", ""),
            ("rewrite ^/example-api/", "rewrite ^/other-api/"),
            ("location ~ ", "location ~* "),
            ("location ~ ", "location = "),
            ("user=owner-a", "user=Owner"),
        ):
            with self.subTest(new=new):
                self.assertIn(old, good)
                with self.assertRaises(chk.Rejected):
                    chk.check_block(good.replace(old, new), METHODS, PREFIXES, UPSTREAMS, BLOCK_LINES)

    def test_check_block_revalidates_the_regex_inside(self):
        bad = self.render(regex="^/example-api/api/items/.*$")
        with self.assertRaises(chk.Rejected) as ctx:
            chk.check_block(bad, METHODS, PREFIXES, UPSTREAMS, BLOCK_LINES)
        self.assertEqual(ctx.exception.code, "wildcard")


class ApplyTests(unittest.TestCase):
    def apply(self, conf=CONF, name="card-decision", regex=GOOD):
        return chk.apply_to_conf(conf, name, "owner-a", "20260102-101500", "POST", regex,
                                 METHODS, PREFIXES, UPSTREAMS, BLOCK_LINES)

    def test_inserts_right_below_marker_and_keeps_rest(self):
        out = self.apply()
        lines = out.splitlines()
        at = lines.index("    # AX-GATE-INSERT-BELOW")
        self.assertTrue(lines[at + 1].startswith("    # ax-gate: name=card-decision user=owner-a"))
        self.assertTrue(lines[at + 2].startswith("    location ~ " + GOOD + " { limit_except POST { deny all; }"))
        self.assertTrue(lines[at + 2].endswith("include snippets/example-auth.conf; }"))
        # 넣은 2줄(주석 + 한 줄 블록)을 빼면 원본과 같다
        self.assertEqual("\n".join(lines[:at + 1] + lines[at + 3:]) + "\n", CONF)
        # 읽기용 location 보다 앞이다
        self.assertLess(out.index(GOOD), out.index("limit_except GET HEAD"))

    def test_marker_must_be_exactly_one(self):
        with self.assertRaises(chk.ConfigError):
            self.apply(conf=CONF.replace("    # AX-GATE-INSERT-BELOW\n", ""))
        with self.assertRaises(chk.ConfigError):
            self.apply(conf=CONF + "# AX-GATE-INSERT-BELOW\n")

    def test_duplicates(self):
        once = self.apply()
        with self.assertRaises(chk.Rejected) as ctx:
            self.apply(conf=once, name="other")
        self.assertEqual(ctx.exception.code, "duplicate-regex")
        with self.assertRaises(chk.Rejected) as ctx:
            self.apply(conf=once, regex="^/example-api/api/items/z$")
        self.assertEqual(ctx.exception.code, "duplicate-name")
        with self.assertRaises(chk.Rejected) as ctx:
            self.apply(regex="^/example-api/api/items/old/[^/]+/decision$")
        self.assertEqual(ctx.exception.code, "duplicate-regex")


class CliTests(unittest.TestCase):
    BASE = ["--allowed-method", "POST", "--allowed-prefix", PREFIXES[0], "--upstream", "example-api=8201"]
    for _line in BLOCK_LINES:
        BASE += ["--block-line", _line]
    NAMING = ["--name", "n1", "--user", "owner-a", "--stamp", "20260102-101500"]

    def run_cli(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        old_stdin = chk.sys.stdin
        chk.sys.stdin = io.StringIO(stdin)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = chk.main(argv)
        finally:
            chk.sys.stdin = old_stdin
        return rc, out.getvalue(), err.getvalue()

    def test_exit_codes(self):
        self.assertEqual(self.run_cli(["check", "--method", "POST", "--regex", GOOD] + self.BASE)[0], 0)
        rc, _out, err = self.run_cli(["check", "--method", "POST", "--regex", "^/example-api/.*$"] + self.BASE)
        self.assertEqual(rc, 2)
        self.assertTrue(err.startswith("REJECT wildcard:"))
        self.assertEqual(self.run_cli(["check", "--method", "POST", "--regex", GOOD])[0], 3)   # 정책 없음
        self.assertEqual(self.run_cli(["check"])[0], 3)
        self.assertEqual(self.run_cli([])[0], 3)
        self.assertEqual(self.run_cli(["check", "--method", "POST", "--regex", GOOD,
                                       "--upstream", "bad"])[0], 3)
        # 블록 줄이 없는 정책은 설정 오류
        no_lines = ["--allowed-method", "POST", "--allowed-prefix", PREFIXES[0], "--upstream", "example-api=8201"]
        self.assertEqual(self.run_cli(["check", "--method", "POST", "--regex", GOOD] + no_lines)[0], 3)

    def test_render_then_check_block_roundtrip(self):
        rc, block, _err = self.run_cli(["render", "--method", "POST", "--regex", GOOD] + self.NAMING + self.BASE)
        self.assertEqual(rc, 0)
        self.assertEqual(self.run_cli(["check-block"] + self.BASE, stdin=block)[0], 0)
        self.assertEqual(self.run_cli(["check-block"] + self.BASE, stdin=block + "return 200;\n")[0], 2)

    def test_apply_writes_out_file_and_leaves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "site.conf")
            out = os.path.join(tmp, "site.conf.new")
            with open(conf, "w", encoding="utf-8", newline="") as fh:
                fh.write(CONF)
            argv = ["apply", "--conf", conf, "--out", out, "--method", "POST", "--regex", GOOD]
            rc, _o, _e = self.run_cli(argv + self.NAMING + self.BASE)
            self.assertEqual(rc, 0)
            with open(conf, encoding="utf-8", newline="") as fh:
                self.assertEqual(fh.read(), CONF)
            with open(out, encoding="utf-8", newline="") as fh:
                self.assertIn("location ~ " + GOOD + " {", fh.read())
            argv[2] = os.path.join(tmp, "none")
            rc, _o, _e = self.run_cli(argv + self.NAMING + self.BASE)
            self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
