#!/usr/bin/env python3
"""ax-gate 게이트웨이 허용 목록 검증기 (표준 라이브러리만, Python 3.8+).

게이트(ops-gate.sh)의 gateway-allow-propose / gateway-allow-approve / gateway-allow-restore 동작이 부른다.
사람이 손으로 돌려 봐도 된다. 서버를 바꾸지 않는다. 읽기와 "새 파일 쓰기"만 한다(교체·백업·reload 는 게이트가 한다).

이 파일에는 특정 서버의 값이 없다. 카드 파일·허용 목록 파일의 경로는 호출하는 쪽이 넘긴다
(게이트는 그것을 정책 파일 [gateway_allow] 에서 읽는다).

하는 일
  check-name   skill 이름 형식(^[a-z][a-z0-9_]{1,63}$)만 검사한다.
  card         에이전트 카드 파일에 그 skill 이 있는지, 등급이 read 인지, (선택) 요청자 이름이
               allowed_requesters 에 있는지 검사한다.
  list-add     허용 목록 파일에 skill 한 줄을 더한 새 파일을 --out 에 쓴다. 이미 있으면 거부한다.
               원본은 건드리지 않는다.
  list-has     허용 목록 파일에 그 skill 이 있는지(없으면 거부). 적용 뒤 확인용.
  list-check   허용 목록 파일이 어댑터가 아는 형식인지 검사한다(복원 전 확인용).

끝 코드: 0 통과, 2 거부(입력이 규칙에 어긋남), 3 설정·사용법·형식 오류.
거부 사유는 표준 오류에 "REJECT <코드>: <설명>" 한 줄로 나온다.

가정(서버에서 확인 전 — 형식이 다르면 아래 두 곳만 고치면 된다):
  1) 카드: JSON 객체. {"skills": [{"id": "<skill>", "grade": "read"}, ...], "grade": "read",
     "allowed_requesters": ["<게이트웨이 요청자 이름>", ...]}
     - 등급은 카드 수준 "grade" 와 skill 수준 "grade" 중 적혀 있는 것 전부가 정확히 "read" 여야 하고,
       적힌 곳이 하나도 없으면 거부한다(안전한 쪽으로 실패).
     - 카드 읽기: card_skill_entry() / check_card()
  2) 허용 목록: 한 줄에 skill 하나, 빈 줄과 "#" 로 시작하는 줄은 무시. 읽기·쓰기는 allowlist_adapter() 한 함수.
"""

import argparse
import json
import os
import re
import stat
import sys

EXIT_OK = 0
EXIT_REJECT = 2
EXIT_CONFIG = 3

RE_SKILL = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
RE_REQUESTER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
MAX_FILE_BYTES = 1024 * 1024  # 카드·허용 목록 모두 1 MiB 를 넘으면 읽지 않는다
READ_GRADE = "read"


class Rejected(Exception):
    """입력이 규칙에 어긋난다."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ConfigError(Exception):
    """파일 형식·사용법이 잘못됐다(입력 탓이 아니다)."""


def check_name(skill):
    if not isinstance(skill, str) or not RE_SKILL.match(skill):
        raise Rejected("bad-name", "skill 이름은 ^[a-z][a-z0-9_]{1,63}$ 이어야 한다")


def read_text_file(path):
    """일반 파일(심볼릭 링크 아님)만 UTF-8 로 읽는다. 크기 한도 안에서만."""
    try:
        st = os.lstat(path)
    except OSError:
        raise ConfigError("파일을 읽을 수 없다") from None
    if not stat.S_ISREG(st.st_mode):
        raise ConfigError("일반 파일이 아니다(심볼릭 링크·폴더는 읽지 않는다)") from None
    if st.st_size > MAX_FILE_BYTES:
        raise ConfigError("파일이 너무 크다") from None
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        raise ConfigError("파일을 UTF-8 글로 읽을 수 없다") from None


# ---------------------------------------------------------------------------
# 어댑터 1: 허용 목록 파일 형식(읽기·쓰기). 서버의 실제 형식이 다르면 이 함수 하나만 바꾼다.
# ---------------------------------------------------------------------------
def allowlist_adapter(text, skill=None):
    """허용 목록 글 → (지금 들어 있는 skill 목록, skill 을 더한 새 글).

    skill 이 None 이면 읽기만 한다(새 글은 원문 그대로).
    형식: 한 줄에 skill 하나. 빈 줄과 '#' 로 시작하는 줄은 무시한다. 그 밖의 줄이 skill 형식이 아니거나
    CR 이 있으면 형식 오류(ConfigError)로 멈춘다 — 모르는 형식에 한 줄을 끼워 넣어 망가뜨리지 않는다.
    새 skill 은 맨 끝에 한 줄로 더한다. 이미 있으면 거부(duplicate).
    """
    if "\r" in text or "\x00" in text:
        raise ConfigError("허용 목록에 CR·NUL 이 있다(어댑터가 모르는 형식)") from None
    skills = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not RE_SKILL.match(line):
            raise ConfigError("허용 목록에 어댑터가 모르는 줄이 있다(한 줄에 skill 하나여야 한다)") from None
        skills.append(line)
    if skill is None:
        return skills, text
    if skill in skills:
        raise Rejected("duplicate", "이미 허용 목록에 있는 skill 이다")
    new_text = text if (not text or text.endswith("\n")) else text + "\n"
    return skills, new_text + skill + "\n"


# ---------------------------------------------------------------------------
# 어댑터 2: 에이전트 카드 읽기. 서버의 실제 카드 모양이 다르면 이 두 함수만 바꾼다.
# ---------------------------------------------------------------------------
def _no_dup_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ConfigError("카드 JSON 에 같은 키가 두 번 나온다") from None
        seen[key] = value
    return seen


def load_card(path):
    text = read_text_file(path)
    try:
        card = json.loads(text, object_pairs_hook=_no_dup_keys)
    except ValueError:
        raise ConfigError("카드가 JSON 이 아니다") from None
    if not isinstance(card, dict):
        raise ConfigError("카드가 JSON 객체가 아니다") from None
    return card


def card_skill_entry(card, skill):
    """카드에서 id 가 skill 인 항목 하나. 없으면 거부, 둘 이상이면 거부."""
    skills = card.get("skills")
    if not isinstance(skills, list):
        raise ConfigError("카드에 skills 목록이 없다") from None
    found = [e for e in skills if isinstance(e, dict) and e.get("id") == skill]
    if not found:
        raise Rejected("skill-not-in-card", "대상 에이전트 카드에 그 skill id 가 없다")
    if len(found) > 1:
        raise Rejected("skill-ambiguous", "카드에 같은 skill id 가 둘 이상이다")
    return found[0]


def check_card(card, skill, requester=""):
    """등급이 read 인지, (요청자 이름이 주어지면) allowed_requesters 에 있는지. 통과하면 'read'."""
    entry = card_skill_entry(card, skill)
    grades = [v["grade"] for v in (card, entry) if "grade" in v]
    if not grades:
        raise Rejected("grade-missing", "카드에 grade 가 없다(등급을 알 수 없으면 거부한다)")
    for g in grades:
        if not isinstance(g, str) or g != READ_GRADE:
            raise Rejected("grade-not-read", "카드 등급이 read 가 아니다(write 등급은 자동 거부)")
    if requester:
        for holder in (card, entry):
            if holder is entry and "allowed_requesters" not in entry:
                continue
            names = holder.get("allowed_requesters")
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise Rejected("requester-missing", "카드에 allowed_requesters 목록이 없거나 형식이 틀리다")
            if requester not in names:
                raise Rejected("requester-not-allowed", "카드의 allowed_requesters 에 게이트웨이 요청자가 없다")
    return READ_GRADE


# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(description="ax-gate 게이트웨이 허용 목록 검증기")
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("check-name")
    sp.add_argument("--skill", required=True)
    sp = sub.add_parser("card")
    sp.add_argument("--card", required=True)
    sp.add_argument("--skill", required=True)
    sp.add_argument("--requester", default="")
    sp = sub.add_parser("list-add")
    sp.add_argument("--file", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--skill", required=True)
    sp = sub.add_parser("list-has")
    sp.add_argument("--file", required=True)
    sp.add_argument("--skill", required=True)
    sp = sub.add_parser("list-check")
    sp.add_argument("--file", required=True)
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
        if args.cmd == "check-name":
            check_name(args.skill)
            print("OK")
        elif args.cmd == "card":
            check_name(args.skill)
            if args.requester and not RE_REQUESTER.match(args.requester):
                raise ConfigError("요청자 이름 형식이 잘못됐다") from None
            grade = check_card(load_card(args.card), args.skill, args.requester)
            print("OK grade=%s" % grade)
        elif args.cmd == "list-add":
            check_name(args.skill)
            _, new_text = allowlist_adapter(read_text_file(args.file), args.skill)
            with open(args.out, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)
            print("OK")
        elif args.cmd == "list-has":
            check_name(args.skill)
            skills, _ = allowlist_adapter(read_text_file(args.file))
            if args.skill not in skills:
                raise Rejected("not-in-list", "허용 목록에 그 skill 이 없다")
            print("OK")
        elif args.cmd == "list-check":
            skills, _ = allowlist_adapter(read_text_file(args.file))
            print("OK count=%d" % len(skills))
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
