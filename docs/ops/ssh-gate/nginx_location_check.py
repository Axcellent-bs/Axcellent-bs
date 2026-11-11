#!/usr/bin/env python3
"""ax-gate nginx location 검증기 (표준 라이브러리만, Python 3.8+).

게이트(ops-gate.sh)의 nginx-add-location / nginx-approve-location 동작이 부른다.
사람이 손으로 돌려 봐도 된다.

이 파일에는 특정 서버의 값이 없다. location 블록의 모양(안에 들어가는 지시어 줄),
허용 메서드, 허용 경로 접두, 접두 → 업스트림 포트 표는 전부 호출하는 쪽이 넘긴다
(게이트는 그것을 정책 파일에서 읽는다).

만드는 블록은 한 가지 꼴뿐이다. 서버의 기존 설정과 같은 모양이 되게 "블록 줄"을 정책에 적는다.

    # ax-gate: name=<이름> user=<제안자> added=<시각>
    location ~ <정규식> { <블록 줄 1> <블록 줄 2> ... }

블록 줄에는 자리표시 {method}, {segment}, {port} 를 쓸 수 있다. 예:

    limit_except {method} { deny all; }
    rewrite ^/{segment}/(.*)$ /$1 break;
    proxy_pass http://127.0.0.1:{port};
    include snippets/example-proxy.conf;

요청하는 사람이 정하는 것은 이름·메서드·정규식뿐이다. 블록 줄·포트·호스트는 고를 수 없다.

하는 일
  check        메서드·정규식·허용 접두·업스트림 표·블록 줄을 검사한다.
  render       검사를 통과한 입력으로 location 블록 한 개를 만들어 출력한다.
  check-block  블록 글이 정책의 블록 줄로 만든 것과 정확히 같은지 검사한다(표준 입력).
  apply        설정 파일의 삽입 표시 줄 바로 아래에 블록을 넣은 새 파일을 쓴다.
               원본은 건드리지 않는다(교체·nginx -t·reload 는 게이트가 한다).

끝 코드: 0 통과, 2 거부(입력이 규칙에 어긋남), 3 설정·사용법 오류.
거부 사유는 표준 오류에 "REJECT <코드>: <설명>" 한 줄로 나온다.
"""

import argparse
import re
import string
import sys

# 설정 파일에 사람이 한 번 넣어 두는 삽입 표시 줄. 읽기용 location 들보다 앞에 둔다.
INSERT_MARKER = "# AX-GATE-INSERT-BELOW"

# 쓰기 경로만 연다. GET/HEAD(읽기용)는 게이트로 열지 않는다.
METHOD_UNIVERSE = ("POST", "PUT", "PATCH", "DELETE")

# 한 경로 조각 안에서만 맞는 문자 묶음. 어느 것도 "/" 를 넘지 못한다.
SAFE_CLASSES = (
    "[^/]+",
    "[0-9]+",
    "[0-9a-f]+",
    "[0-9a-f-]+",
    "[a-z0-9-]+",
    "[A-Za-z0-9_-]+",
)

MAX_REGEX_LEN = 200
LITERAL_CHARS = set(string.ascii_letters + string.digits + "_-/")
RE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RE_KEY = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
RE_STAMP = re.compile(r"^[0-9]{8}-[0-9]{6}$")
RE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
RE_ALT = re.compile(r"^[A-Za-z0-9_-]+$")
RE_PREFIX = re.compile(r"^/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*/?$")

# 블록 줄(정책이 준다)에 쓸 수 있는 글자와 자리표시
RE_BLOCK_LINE = re.compile(r"^[A-Za-z0-9 _./:;^$(){}*-]{1,200}$")
BLOCK_PLACEHOLDERS = ("{method}", "{segment}", "{port}")
MAX_BLOCK_LINES = 12
# 블록 줄에 반드시 있어야 하는 두 가지: 메서드 제한, 이 서버 자신(127.0.0.1)의 정책 포트로만 가는 proxy_pass
REQUIRED_LIMIT_PREFIX = "limit_except {method} "
REQUIRED_PROXY_LINE = "proxy_pass http://127.0.0.1:{port};"

RE_COMMENT = re.compile(
    r"^# ax-gate: name=(?P<name>[a-z0-9][a-z0-9-]{0,39}) user=(?P<user>[a-z][a-z0-9-]{1,31})"
    r" added=(?P<stamp>[0-9]{8}-[0-9]{6}) location ~ (?P<regex>\S+) \{ "
)

EXIT_OK = 0
EXIT_REJECT = 2
EXIT_CONFIG = 3


class Rejected(Exception):
    """입력이 규칙에 어긋난다."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ConfigError(Exception):
    """정책·사용법이 잘못됐다(입력 탓이 아니다)."""


def tokenize(regex):
    """정규식을 (종류, 값) 목록으로 쪼갠다. 허용 문법 밖이면 Rejected."""
    if not isinstance(regex, str) or not regex:
        raise Rejected("empty", "정규식이 비어 있다")
    if len(regex) > MAX_REGEX_LEN:
        raise Rejected("too-long", "정규식이 %d자를 넘는다" % MAX_REGEX_LEN)
    if not regex.startswith("^/"):
        raise Rejected("not-anchored-start", "정규식은 ^/ 로 시작해야 한다")
    if not regex.endswith("$") or regex.endswith("\\$"):
        raise Rejected("not-anchored-end", "정규식은 $ 로 끝나야 한다(끝까지 고정)")
    body = regex[1:-1]
    tokens = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch in LITERAL_CHARS:
            tokens.append(("lit", ch))
            i += 1
        elif ch == "\\":
            if body[i + 1:i + 2] == ".":
                tokens.append(("lit", "."))
                i += 2
            else:
                raise Rejected("bad-escape", "역슬래시는 \\. 만 쓸 수 있다")
        elif ch == "[":
            for cls in SAFE_CLASSES:
                if body.startswith(cls, i):
                    tokens.append(("class", cls))
                    i += len(cls)
                    break
            else:
                raise Rejected(
                    "class-not-allowed",
                    "문자 묶음은 다음만 쓸 수 있다: " + " ".join(SAFE_CLASSES),
                )
        elif ch == "(":
            end = body.find(")", i)
            if end < 0:
                raise Rejected("group-unclosed", "괄호가 닫히지 않았다")
            alts = body[i + 1:end].split("|")
            if len(alts) < 2 or not all(RE_ALT.match(a) for a in alts):
                raise Rejected(
                    "group-not-allowed",
                    "괄호 안은 글자 그대로인 선택지 두 개 이상(a|b)만 쓸 수 있다",
                )
            tokens.append(("alt", tuple(alts)))
            i = end + 1
        elif ch == ".":
            raise Rejected("wildcard", "점(아무 글자)은 금지다. 글자 점은 \\. 로 쓴다")
        elif ch in "*?+{}":
            raise Rejected("wildcard", "반복·와일드카드 기호(%s)는 금지다" % ch)
        elif ch in "^$":
            raise Rejected("anchor-inside", "^ 와 $ 는 맨 앞과 맨 끝에만 쓴다")
        else:
            raise Rejected("char-not-allowed", "쓸 수 없는 글자가 있다")
    return tokens


def literal_prefix(tokens):
    """맨 앞의 글자 그대로인 부분과, 그 뒤에 토큰이 더 있는지를 돌려준다."""
    out = []
    for kind, value in tokens:
        if kind != "lit":
            return "".join(out), True
        out.append(value)
    return "".join(out), False


def literal_text(tokens):
    """글자 부분만 이어 붙인다(묶음·선택지는 자리 표시 한 글자)."""
    return "".join(v if k == "lit" else "\x00" for k, v in tokens)


def first_segment(tokens):
    """첫 경로 조각(예: /example-api/... 의 example-api). 글자 그대로여야 한다."""
    prefix, _more = literal_prefix(tokens)
    parts = prefix.split("/")
    # prefix 는 "/" 로 시작하므로 parts[0] == "". 첫 조각 뒤에 "/" 가 있어야 조각이 끝난 것이다.
    if len(parts) < 3 or not RE_SEGMENT.match(parts[1]):
        raise Rejected(
            "first-segment",
            "첫 경로 조각은 글자 그대로여야 하고 그 뒤에 경로가 이어져야 한다(/<접두>/...)",
        )
    return parts[1]


def check_prefix(tokens, allowed_prefixes):
    if not allowed_prefixes:
        raise ConfigError("허용 경로 접두 목록이 비어 있다")
    for p in allowed_prefixes:
        if not RE_PREFIX.match(p) or ".." in p or len(p) < 3:
            raise ConfigError("허용 경로 접두가 잘못됐다: 형식은 /조각/조각/")
    prefix, more = literal_prefix(tokens)
    for p in allowed_prefixes:
        if p.endswith("/"):
            if prefix.startswith(p):
                return p
        elif prefix.startswith(p + "/") or (prefix == p and not more):
            return p
    raise Rejected("prefix-not-allowed", "이 계정에 허용된 경로 접두 안이 아니다")


def check_block_lines(block_lines):
    """정책이 준 블록 줄이 쓸 만한지 본다(입력이 아니라 정책 검사)."""
    if not block_lines:
        raise ConfigError("블록 줄(정책 [location] block_lines)이 비어 있다")
    if len(block_lines) > MAX_BLOCK_LINES:
        raise ConfigError("블록 줄이 너무 많다")
    for line in block_lines:
        if not RE_BLOCK_LINE.match(line) or line != line.strip() or "  " in line:
            raise ConfigError("블록 줄에 쓸 수 없는 글자가 있다")
        if not (line.endswith(";") or line.endswith("}")):
            raise ConfigError("블록 줄은 ; 나 } 로 끝나야 한다")
        rest = line
        for ph in BLOCK_PLACEHOLDERS:
            rest = rest.replace(ph, "")
        if rest.count("{") != rest.count("}"):
            raise ConfigError("블록 줄의 중괄호가 짝이 맞지 않거나 모르는 자리표시가 있다")
        if re.search(r"\{[a-z_]+\}", rest):
            raise ConfigError("블록 줄에 모르는 자리표시가 있다")
    if sum(1 for ln in block_lines if ln.startswith(REQUIRED_LIMIT_PREFIX)) != 1:
        raise ConfigError("블록 줄에 메서드 제한(limit_except {method} ...)이 정확히 한 줄 있어야 한다")
    if sum(1 for ln in block_lines if ln == REQUIRED_PROXY_LINE) != 1:
        raise ConfigError("블록 줄에 '%s' 가 정확히 한 줄 있어야 한다" % REQUIRED_PROXY_LINE)
    if sum(1 for ln in block_lines if ln.startswith("proxy_pass ")) != 1:
        raise ConfigError("proxy_pass 줄은 하나만 둘 수 있다")


def validate(method, regex, allowed_methods, allowed_prefixes, upstreams, block_lines):
    """전체 검사. (첫 조각, 포트) 를 돌려준다."""
    check_block_lines(block_lines)
    for m in allowed_methods:
        if m not in METHOD_UNIVERSE:
            raise ConfigError("허용 메서드 목록에 게이트가 다루지 않는 메서드가 있다")
    if not allowed_methods:
        raise ConfigError("허용 메서드 목록이 비어 있다")
    if method not in METHOD_UNIVERSE:
        raise Rejected("method-not-allowed", "메서드는 %s 중 하나여야 한다" % "/".join(METHOD_UNIVERSE))
    if method not in allowed_methods:
        raise Rejected("method-not-allowed", "정책이 허용하지 않는 메서드다")
    tokens = tokenize(regex)
    text = literal_text(tokens)
    if "//" in text:
        raise Rejected("empty-segment", "빈 경로 조각(//)이 있다")
    if ".." in text:
        raise Rejected("dot-dot", "연속된 점(..)은 금지다")
    segment = first_segment(tokens)
    check_prefix(tokens, allowed_prefixes)
    port = upstreams.get(segment)
    if port is None:
        raise Rejected("upstream-unknown", "첫 경로 조각에 맞는 업스트림 포트가 정책에 없다")
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ConfigError("업스트림 포트가 잘못됐다")
    return segment, port


def render_block(name, user, stamp, method, regex, segment, port, block_lines, indent=""):
    if not RE_NAME.match(name):
        raise Rejected("bad-name", "이름은 영어 소문자·숫자·- 만, 40자 이하")
    if not RE_KEY.match(user):
        raise ConfigError("계정 이름 형식이 잘못됐다")
    if not RE_STAMP.match(stamp):
        raise ConfigError("시각 형식이 잘못됐다(YYYYMMDD-HHMMSS)")
    if indent.strip(" \t"):
        raise ConfigError("들여쓰기에 공백 아닌 글자가 있다")
    check_block_lines(block_lines)
    body = " ".join(
        ln.replace("{method}", method).replace("{segment}", segment).replace("{port}", str(port))
        for ln in block_lines
    )
    return (
        "%s# ax-gate: name=%s user=%s added=%s\n" % (indent, name, user, stamp)
        + "%slocation ~ %s { %s }\n" % (indent, regex, body)
    )


def check_block(text, allowed_methods, allowed_prefixes, upstreams, block_lines):
    """블록 글이 정책의 블록 줄로 만든 것과 정확히 같은지 본다(공백·줄바꿈 위치는 무시)."""
    flat = " ".join(text.split())
    m = RE_COMMENT.match(flat)
    if not m:
        raise Rejected("block-shape", "게이트가 만든 블록의 모양이 아니다")
    found = m.groupdict()
    # 메서드는 블록 안에서 읽지 않는다. 허용 메서드마다 블록을 다시 만들어 글자 그대로 비교한다.
    last_error = Rejected("block-shape", "정책의 블록 줄과 다른 블록이다(임의 지시어·다른 include·다른 호스트 금지)")
    for method in allowed_methods:
        try:
            segment, port = validate(method, found["regex"], allowed_methods, allowed_prefixes,
                                     upstreams, block_lines)
        except Rejected as exc:
            last_error = exc
            continue
        expected = render_block(found["name"], found["user"], found["stamp"], method, found["regex"],
                                segment, port, block_lines)
        if " ".join(expected.split()) == flat:
            return True
        last_error = Rejected("block-shape", "정책의 블록 줄과 다른 블록이다(임의 지시어·다른 include·다른 호스트 금지)")
    raise last_error


def apply_to_conf(conf_text, name, user, stamp, method, regex,
                  allowed_methods, allowed_prefixes, upstreams, block_lines):
    """삽입 표시 줄 바로 아래에 블록을 넣은 새 설정 글을 돌려준다."""
    segment, port = validate(method, regex, allowed_methods, allowed_prefixes, upstreams, block_lines)
    lines = conf_text.splitlines(keepends=True)
    marker_at = [i for i, ln in enumerate(lines) if ln.strip() == INSERT_MARKER]
    if len(marker_at) != 1:
        raise ConfigError(
            "설정 파일에 삽입 표시 줄(%s)이 정확히 한 개 있어야 한다(지금 %d개)"
            % (INSERT_MARKER, len(marker_at))
        )
    for ln in lines:
        s = ln.strip()
        if s.startswith("location") and regex in s.split():
            raise Rejected("duplicate-regex", "같은 정규식 location 이 이미 있다")
        if s.startswith("# ax-gate: name=%s " % name):
            raise Rejected("duplicate-name", "같은 이름의 게이트 location 이 이미 있다")
    marker_line = lines[marker_at[0]]
    indent = marker_line[:len(marker_line) - len(marker_line.lstrip(" \t"))]
    block = render_block(name, user, stamp, method, regex, segment, port, block_lines, indent)
    check_block(block, allowed_methods, allowed_prefixes, upstreams, block_lines)  # 자기 점검
    if not marker_line.endswith("\n"):
        lines[marker_at[0]] = marker_line + "\n"
    return "".join(lines[:marker_at[0] + 1]) + block + "".join(lines[marker_at[0] + 1:])


def parse_upstreams(items):
    out = {}
    for item in items or []:
        seg, sep, port = item.partition("=")
        if not sep or not RE_SEGMENT.match(seg) or not port.isdigit():
            raise ConfigError("업스트림은 <첫조각>=<포트> 형식이어야 한다")
        out[seg] = int(port)
    return out


def build_parser():
    p = argparse.ArgumentParser(description="ax-gate nginx location 검증기")
    sub = p.add_subparsers(dest="cmd")

    def common(sp, with_input=True):
        if with_input:
            sp.add_argument("--method", required=True)
            sp.add_argument("--regex", required=True)
        sp.add_argument("--allowed-method", action="append", default=[])
        sp.add_argument("--allowed-prefix", action="append", default=[])
        sp.add_argument("--upstream", action="append", default=[], metavar="조각=포트")
        sp.add_argument("--block-line", action="append", default=[], metavar="지시어 줄")

    def naming(sp):
        sp.add_argument("--name", required=True)
        sp.add_argument("--user", required=True)
        sp.add_argument("--stamp", required=True)

    common(sub.add_parser("check"))
    sp = sub.add_parser("render")
    common(sp)
    naming(sp)
    common(sub.add_parser("check-block"), with_input=False)
    sp = sub.add_parser("apply")
    common(sp)
    naming(sp)
    sp.add_argument("--conf", required=True)
    sp.add_argument("--out", required=True)
    return p


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_CONFIG
    if not args.cmd:
        parser.print_usage(sys.stderr)
        return EXIT_CONFIG
    try:
        upstreams = parse_upstreams(args.upstream)
        rules = (args.allowed_method, args.allowed_prefix, upstreams, args.block_line)
        if args.cmd == "check":
            validate(args.method, args.regex, *rules)
            print("OK")
        elif args.cmd == "render":
            segment, port = validate(args.method, args.regex, *rules)
            sys.stdout.write(render_block(args.name, args.user, args.stamp, args.method,
                                          args.regex, segment, port, args.block_line))
        elif args.cmd == "check-block":
            check_block(sys.stdin.read(), *rules)
            print("OK")
        elif args.cmd == "apply":
            with open(args.conf, "r", encoding="utf-8", newline="") as fh:
                conf_text = fh.read()
            new_text = apply_to_conf(conf_text, args.name, args.user, args.stamp, args.method,
                                     args.regex, *rules)
            with open(args.out, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)
            print("OK")
    except Rejected as exc:
        sys.stderr.write("REJECT %s: %s\n" % (exc.code, exc.message))
        return EXIT_REJECT
    except ConfigError as exc:
        sys.stderr.write("CONFIG: %s\n" % exc)
        return EXIT_CONFIG
    except OSError as exc:
        sys.stderr.write("CONFIG: 파일을 읽거나 쓸 수 없다 (%s)\n" % exc.__class__.__name__)
        return EXIT_CONFIG
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
