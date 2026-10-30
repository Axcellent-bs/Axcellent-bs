#!/usr/bin/env node
// PreToolUse(Bash|PowerShell) 훅. git push / gh 머지 제한. ax-backend guard-bash.mjs 를 이 레포에 맞게 줄였다.
// exit 0 = 허용, exit 2 = 차단(stderr 사유).
//
// 규칙(셸 종류와 무관한 보수적 토큰 규칙):
//  1) 명령 어디에든 `git` 토큰이 있고 push / send-pack / alias 토큰도 있으면,
//     명령 전체가 정확히 `git push [-u] origin <종류>/<이름>` 한 줄일 때만 통과. dev·main 직접 push 는 막힌다.
//     (오탐: `git commit -m "mention git push"` 도 차단된다. 의도된 보수적 동작.)
//     예외: `git stash push` 의 push 토큰(바로 앞이 `git` `stash`)만 위험으로 보지 않는다. 같은 명령에
//     다른 push·send-pack·alias 토큰이 있으면 지금처럼 막는다 (`git stash push && git push origin dev` 차단).
//  2) `gh pr merge` 는 자동 머지 예약 한 줄만. `gh api` 의 /merge 호출·PUT pulls, GraphQL 머지 변이는 차단.
import fs from 'node:fs';

function block(msg) {
  process.stderr.write(`[guard-bash] ${msg}\n`);
  process.exit(2);
}

/** 브랜치 이름 앞부분 (CLAUDE.md "브랜치와 머지") */
const PREFIXES = new Set(['feat', 'fix', 'refactor', 'chore', 'docs', 'ci', 'revert']);

// 자동 머지 예약 한 줄만 허용 (필수 체크·승인은 브랜치 보호가 판단). --admin·--delete-branch·--auto 없는 형태는 차단
const AUTO_MERGE = /^gh pr merge \d+ (--auto (--squash|--merge|--rebase)|(--squash|--merge|--rebase) --auto)( (-R|--repo) Axcellent-bs\/Axcellent-bs)?$/;

// 실행 파일 토큰인지: 경로 접두어 제거, 소문자, 확장자(.exe 등) 제거
const exeName = (tok) => tok.split(/[\/]/).pop().toLowerCase().replace(/\.(exe|cmd|bat|com)$/, '');

// 명령 전체가 정확히 허용된 push 한 줄인지 검사 (아니면 block)
function checkExactPush(cmd) {
  const why = '`git push [-u] origin <종류>/<이름>` 한 줄만 허용됩니다 (다른 명령과 섞거나 우회 형태는 차단).';
  const c = cmd.trim();
  if (/[;&|`$(){}<>"'\!\n\r]/.test(c)) return block(why);
  const t = c.split(/\s+/);
  if (t[0] !== 'git' || t[1] !== 'push') return block(why);
  const okFlags = new Set(['-u', '--set-upstream', '-q', '--quiet']);
  const pos = [];
  for (const a of t.slice(2)) {
    if (a.startsWith('-')) {
      if (!okFlags.has(a)) return block(`허용되지 않는 push 옵션: ${a}`);
    } else pos.push(a);
  }
  if (pos.length !== 2) return block(why);
  const [remote, branch] = pos;
  if (remote !== 'origin') return block(`origin 이외의 remote 는 막습니다: ${remote}`);
  const m = /^([^/]+)\/[a-z0-9][a-z0-9-]*$/.exec(branch);
  if (!m) return block(`브랜치 형식이 올바르지 않거나 보호 대상입니다: ${branch}`);
  if (!PREFIXES.has(m[1])) return block(`${m[1]}/ 접두어는 허용되지 않습니다. 허용: ${[...PREFIXES].join(', ')}`);
}

function main() {
  let cmd;
  try {
    cmd = JSON.parse(fs.readFileSync(0, 'utf8')).tool_input.command;
  } catch {
    return block('입력을 해석할 수 없어 차단합니다.');
  }
  if (typeof cmd !== 'string') return block('명령을 알 수 없어 차단합니다.');

  // 셸 구분자·따옴표·괄호·! 등으로 느슨하게 토큰화 (경로의 \ 와 / 는 유지)
  const toks = cmd.split(/[\s;&|(){}`$"'<>,!]+/).filter(Boolean);
  const lower = toks.map((x) => x.toLowerCase());
  const has = (name) => toks.some((x) => exeName(x) === name);

  // `git stash push` 의 push 토큰인지 (원격 업로드가 아니다)
  const isStashPush = (i) => i >= 2 && lower[i - 1] === 'stash' && exeName(toks[i - 2]) === 'git';

  if (has('git')) {
    const risky = lower.some(
      (x, i) => (x === 'push' && !isStashPush(i)) || x === 'send-pack' || x.includes('alias'),
    );
    if (risky) checkExactPush(cmd);
  }

  if (has('gh')) {
    if (lower.includes('pr') && lower.includes('merge') && !AUTO_MERGE.test(cmd.trim())) {
      return block('gh pr merge 는 `gh pr merge <번호> --auto --squash|--merge|--rebase` 한 줄(자동 머지 예약)만 허용합니다.');
    }
    if (lower.includes('api')) {
      const put = lower.some((x) => x === 'put' || /^--method=put$/.test(x) || x === '-xput');
      if (lower.some((x) => x.includes('/merge')) || (put && lower.some((x) => x.includes('pulls')))) {
        return block('gh api 를 통한 PR 머지는 금지입니다.');
      }
    }
    if (/mergepullrequest|enablepullrequestautomerge/i.test(cmd)) return block('GraphQL 을 통한 PR 머지는 금지입니다.');
  }
  process.exit(0);
}

try {
  main();
} catch (e) {
  block(`예상치 못한 오류로 차단합니다: ${e && e.message}`);
}
