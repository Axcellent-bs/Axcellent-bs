#!/usr/bin/env node
// PreToolUse 훅: 쓰기 도구(Edit/Write/MultiEdit/NotebookEdit)를 레포 안 작업 폴더로 제한한다.
// 루프(에이전트)는 frontend/, backend/, docs/ 만 고친다. 자동화 파일(.claude/, .github/, CLAUDE.md)은 사람만 바꾼다.
// 사람이 직접 자동화 파일을 고칠 때는 .claude/settings.local.json 에 {"env": {"AX_HUMAN": "1"}} 를 둔다.
// exit 0 = 허용, exit 2 = 차단(사유는 stderr). 파싱 오류 등은 모두 차단(fail closed).
import fs from 'node:fs';
import path from 'node:path';

const WIN = process.platform === 'win32';
const norm = (p) => (WIN ? p.toLowerCase() : p);

function block(msg) {
  process.stderr.write(`[guard-folder] ${msg}\n`);
  process.exit(2);
}

/** 루프가 고칠 수 있는 곳 */
const WORK = ['frontend', 'backend', 'docs'];
/** 사람만 고치는 곳 */
const HUMAN = ['.claude', '.github', 'CLAUDE.md'];
/** 아무도 손으로 고치지 않는 곳 (생성물·비밀값) */
const NEVER = [
  ['frontend', 'node_modules'],
  ['frontend', 'dist'],
  ['backend', '.venv'],
  ['backend', '.logs'],
];

function main() {
  let input;
  try {
    input = JSON.parse(fs.readFileSync(0, 'utf8'));
  } catch {
    return block('입력을 해석할 수 없어 차단합니다.');
  }
  const t = input.tool_input || {};
  const file = t.file_path || t.notebook_path || t.path;
  if (typeof file !== 'string' || !file) return block('대상 파일을 알 수 없어 차단합니다.');

  // 사람 모드: 비밀값만 막고 나머지는 사람 판단에 맡긴다
  if (process.env.AX_HUMAN === '1') {
    const b = path.basename(file).toLowerCase();
    if (b === '.env' || (b.startsWith('.env.') && b !== '.env.example')) return block(`비밀값 파일은 고치지 않습니다: ${file}`);
    process.exit(0);
  }

  const root = path.resolve(process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd());
  const abs = path.resolve(root, file);
  const rel = path.relative(norm(root), norm(abs));
  if (rel === '' || rel.split(path.sep)[0] === '..' || path.isAbsolute(rel)) return block(`레포 밖은 고치지 않습니다: ${file}`);

  const segs = path.relative(root, abs).split(path.sep).filter(Boolean);
  const low = segs.map((s) => s.toLowerCase());
  const base = low[low.length - 1];

  if (base === '.env' || base.startsWith('.env.') && base !== '.env.example') return block(`비밀값 파일은 고치지 않습니다: ${file}`);
  if (low.includes('__pycache__')) return block(`생성물 폴더는 손으로 고치지 않습니다: __pycache__`);
  for (const n of NEVER) if (n.every((s, i) => low[i] === s.toLowerCase())) return block(`생성물 폴더는 손으로 고치지 않습니다: ${n.join('/')}`);

  const top = segs[0];
  if (HUMAN.some((h) => norm(h) === norm(top))) {
    if (process.env.AX_HUMAN === '1') process.exit(0);
    return block(`${top} 은(는) 사람만 고칩니다. 사람이 직접 고칠 때는 .claude/settings.local.json 에 AX_HUMAN=1`);
  }
  if (WORK.some((w) => norm(w) === norm(top))) process.exit(0);
  if (segs.length === 1 && base === 'readme.md') process.exit(0);
  return block(`작업 폴더(${WORK.join(', ')}) 밖입니다: ${file}`);
}

try {
  main();
} catch (e) {
  block(`예상치 못한 오류로 차단합니다: ${e && e.message}`);
}
