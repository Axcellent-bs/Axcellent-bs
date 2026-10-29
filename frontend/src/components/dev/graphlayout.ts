// 커밋 그래프 줄 배치 (SourceTree·`git log --graph`와 같은 방식).
// 커밋은 최신이 먼저이고 자식이 늘 부모보다 앞이어야 한다 (백엔드가 그렇게 준다).
// 줄(lane)마다 "다음에 올 커밋"을 기다린다. 커밋이 오면 그 줄에 점을 찍고, 첫 부모는 같은 줄로,
// 나머지 부모(머지)는 다른 줄로 잇는다. 줄은 옮기지 않고 비면 다시 쓴다.
// 기준 브랜치(·기본 브랜치) 줄을 맨 왼쪽에 미리 잡아 둔다.

export interface LayoutCommit {
  sha: string
  parents: string[]
  refs: string[]
  message: string
  prs: { branch: string }[]
}

/** 한 줄 안의 선 하나. a·b는 줄 번호, y는 0 위 · 1 가운데(점) · 2 아래 */
export interface Seg { a: number; b: number; y0: 0 | 1; y1: 1 | 2; c: number }
export interface GraphRow { col: number; c: number; merge: boolean; segs: Seg[] }
export interface GraphLayout { rows: GraphRow[]; width: number }

export const LANE_COLORS = 8

interface Lane { sha: string; name?: string; live: boolean }

function hash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return h >>> 0
}

/** 브랜치 이름 → 색 번호. 기준 브랜치는 0, 나머지는 이름으로 정해 늘 같은 색 */
export function laneColor(name: string | undefined, base: string, k: number): number {
  if (name && name === base) return 0
  if (name) return 1 + (hash(name) % (LANE_COLORS - 1))
  return 1 + (k % (LANE_COLORS - 1))
}

const MERGE_FROM = /^Merge pull request #\d+ from [^/\s]+\/(\S+)/
const MERGE_BRANCH = /^Merge (?:remote-tracking )?branch '([^']+)'/

function headName(c: LayoutCommit, base: string): string | undefined {
  if (c.refs.includes(base)) return base
  return c.refs[0] ?? c.prs.find((p) => p.branch)?.branch
}

function mergedName(c: LayoutCommit): string | undefined {
  const pr = c.prs.find((p) => p.branch)
  if (pr) return pr.branch
  const m = MERGE_FROM.exec(c.message) ?? MERGE_BRANCH.exec(c.message)
  return m?.[1]
}

/**
 * @param reserve 맨 왼쪽부터 미리 잡을 브랜치 [이름, 끝 sha] (기준 → 기본 순)
 */
export function layoutGraph(commits: LayoutCommit[], base: string, reserve: [string, string][]): GraphLayout {
  const present = new Set(commits.map((c) => c.sha))
  const lanes: (Lane | null)[] = []
  for (const [name, sha] of reserve) {
    if (present.has(sha) && !lanes.some((l) => l?.sha === sha)) lanes.push({ sha, name, live: false })
  }
  const free = () => {
    const i = lanes.indexOf(null)
    if (i >= 0) return i
    lanes.push(null)
    return lanes.length - 1
  }
  const color = (k: number) => laneColor(lanes[k]?.name, base, k)
  const rows: GraphRow[] = []
  let width = 1

  for (const c of commits) {
    const hits: number[] = []
    lanes.forEach((l, k) => { if (l && l.sha === c.sha) hits.push(k) })
    let col: number
    if (hits.length) col = hits[0]
    else {
      col = free()
      lanes[col] = { sha: c.sha, name: headName(c, base), live: false }
    }
    const segs: Seg[] = []
    // 위 절반: 지나가는 줄은 곧게, 이 커밋을 기다리던 줄은 점으로 모인다
    lanes.forEach((l, k) => {
      if (!l || !l.live) return
      if (l.sha === c.sha) segs.push({ a: k, b: col, y0: 0, y1: 1, c: color(k) })
      else segs.push({ a: k, b: k, y0: 0, y1: 2, c: color(k) })
    })
    const lane = lanes[col] as Lane
    lane.live = true
    if (!lane.name) lane.name = headName(c, base)
    const nodeColor = color(col)
    for (const k of hits) if (k !== col) lanes[k] = null

    // 아래 절반: 첫 부모는 같은 줄, 나머지는 기다리는 줄이나 새 줄로
    const [first, ...rest] = c.parents
    if (first === undefined) lanes[col] = null
    else {
      lane.sha = first
      segs.push({ a: col, b: col, y0: 1, y1: 2, c: nodeColor })
    }
    for (const p of rest) {
      if (p === first) continue
      let j = lanes.findIndex((l, k) => k !== col && l?.sha === p)
      if (j < 0) {
        j = free()
        lanes[j] = { sha: p, name: mergedName(c), live: true }
      } else {
        const l = lanes[j] as Lane
        l.live = true
        if (!l.name) l.name = mergedName(c)
      }
      segs.push({ a: col, b: j, y0: 1, y1: 2, c: color(j) })
    }
    while (lanes.length && lanes[lanes.length - 1] === null) lanes.pop()
    width = Math.max(width, col + 1, lanes.length, ...segs.map((s) => Math.max(s.a, s.b) + 1))
    rows.push({ col, c: nodeColor, merge: c.parents.length > 1, segs })
  }
  return { rows, width }
}
