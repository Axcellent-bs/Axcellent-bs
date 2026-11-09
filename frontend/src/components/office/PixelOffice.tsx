import { useEffect, useRef } from "react";
import { DESKS, POP_MS, type Bubble, type DeskKey, type OfficeView, type Visit } from "@/lib/office";

// 픽셀 사무실 캔버스: 서울 사무실 장면 (통유리 창 너머 한강·63빌딩, 오른쪽 라운지).
// 배경은 사람·이름표가 없는 빈 사무실 그림이고, 9자리 사람·이름표·말풍선·보드 카드·불꽃은 코드로 그린다.
// 캐릭터 스프라이트는 pablodelucca/pixel-agents(MIT)에서 가져왔다 (public/office/CREDITS.md).
// 그림 색은 이미지·캔버스 전용이다 (화면 UI는 디자인 토큰을 쓴다).
//
// 움직임
// - 총괄은 라운지 자리에 앉아 있다. 총괄이 단계를 맡기면 그 부서가 총괄 앞 만남 자리로 걸어와 받아 가고 자리로 돌아간다.
// - 부서끼리 직접 부탁하면(공통 로그 A → B) A가 B 옆 통로로 걸어가 말을 전하고 돌아온다.
// - 동시에 걷는 사람은 최대 3명. 이미 걷는 사람에게 온 부탁은 끝난 뒤 한 번만 이어서 한다.
// - 일하는 중인 자리는 픽셀 불꽃이 일렁이고, 일이 끝나면 머리 위로 "✓ 결과"가 떠오른다.
// - prefers-reduced-motion이면 걷지 않고, 불꽃은 고정 모양, "✓ 결과"는 떠오르지 않고 제자리에서 사라진다.

const W = 1280, H = 692; // 캔버스 픽셀 = 배경 비율(1392×752), 자르기 없음
const BASE = import.meta.env.BASE_URL;
const BG_SRC = `${BASE}office/seoul-office.webp`;
const FONT = '"Pretendard Variable", Pretendard, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif';

/** 배경 그림 비율 좌표 → 캔버스 좌표 */
const toC = (fx: number, fy: number) => ({ x: fx * W, y: fy * H });

type Side = "up" | "down";
/** 말풍선 최대 폭 (캔버스 폭의 22%) */
const BUBBLE_MAX_W = Math.round(W * 0.22);
/** 자리(그림 비율, 의자 위 발끝)와 스프라이트. 그림이 6벌이라 멀리 앉은 자리끼리 같은 그림을 쓴다.
 *  `back`: 책상 앞쪽 의자라 뒷모습으로 앉는다. `bubble`: 말풍선 방향(up 머리 위, down 이름표 아래) */
const SEATS: Record<DeskKey, { fx: number; fy: number; char: number; back?: boolean; bubble: Side }> = {
  // 위 책상 뒤쪽 의자 (앞모습). 위 책상 앞쪽 의자 2개는 비워 둔다
  infra: { fx: 0.272, fy: 0.436, char: 0, bubble: "up" },
  data: { fx: 0.413, fy: 0.436, char: 2, bubble: "up" },
  // 아래 책상 뒤쪽 의자 (앞모습)
  planning: { fx: 0.246, fy: 0.654, char: 5, bubble: "up" },
  marketing: { fx: 0.359, fy: 0.654, char: 1, bubble: "up" },
  hr: { fx: 0.471, fy: 0.654, char: 0, bubble: "up" },
  // 아래 책상 앞쪽 의자 (뒷모습). 말풍선은 이름표 아래 (옆 사람 머리·위 줄 이름표를 가리지 않게)
  dev: { fx: 0.248, fy: 0.81, char: 4, back: true, bubble: "down" },
  support: { fx: 0.359, fy: 0.81, char: 2, back: true, bubble: "down" },
  finance: { fx: 0.472, fy: 0.81, char: 1, back: true, bubble: "down" },
  // 라운지 빈백 (앞모습, 왕관)
  orchestrator: { fx: 0.795, fy: 0.638, char: 3, bubble: "up" },
};
/** 대기 동작(고개 돌리기·좌우) 씨앗. 개발 자리를 끼워 넣어도 원래 8자리의 동작이 바뀌지 않게 예전 순번을 쓴다 */
const SEED = Object.fromEntries(DESKS.filter((d) => d.key !== "dev").map((d, i) => [d.key, i])) as Record<DeskKey, number>;
SEED.dev = 8;
const SEAT_PX = Object.fromEntries(DESKS.map((d) => [d.key, toC(SEATS[d.key].fx, SEATS[d.key].fy)])) as Record<DeskKey, Pt>;
const DESK_BY = Object.fromEntries(DESKS.map((d) => [d.key, d])) as Record<DeskKey, (typeof DESKS)[number]>;

// ---------------------------------------------------------------- 길
/** 위 책상과 아래 책상 사이 통로 (위 줄·아래 줄 앞모습 자리의 통로) */
const AISLE_Y = toC(0, 0.563).y;
/** 아래 책상 앞쪽 의자 뒤 바닥 통로 (뒷모습 자리의 통로) */
const LOW_Y = toC(0, 0.905).y;
/** 책상이 없는 세로 옆길 (두 통로를 잇는다) */
const SIDE_X = toC(0.6, 0).x;
/** 위 줄 자리는 책상 앞으로 바로 못 나가서 책상 끝 옆으로 나간 뒤 통로로 내려온다 */
const TOP_EXIT_X: Partial<Record<DeskKey, number>> = { infra: toC(0.199, 0).x, data: toC(0.5, 0).x };
const HOME = SEAT_PX.orchestrator;
/** 총괄 앞 만남 자리 (총괄 왼쪽 3칸, 먼저 온 순서로 빈 칸을 쓴다) */
const MEET: Pt[] = [{ x: -72, y: 8 }, { x: -112, y: -20 }, { x: -112, y: 34 }].map((d) => ({ x: HOME.x + d.x, y: HOME.y + d.y }));
const SPEED = 300; // 걷기 캔버스 px/초
const MAX_WALKERS = 3;
const STAY_ORCH_MS = 1200, STAY_DESK_MS = 1400;
/** 보드 카드 (그림 비율 fx0, fy0, fx1, fy1): 라운지 위 화이트보드 */
const BOARD = [0.74, 0.305, 0.867, 0.445] as const;

// 스프라이트 시트: 16×32 칸 7열 × 3줄 (줄 0 앞, 1 뒤, 2 옆). 열 0~2 걷기, 3~4 타이핑, 5~6 읽기
const FW = 16, FH = 32, SC = 2.2;
const SW = FW * SC, SH = FH * SC;

type Pt = { x: number; y: number };
type Pose = { row: number; col: number; flip: boolean };

const isBack = (k: DeskKey) => !!SEATS[k].back;
const isTop = (k: DeskKey) => k in TOP_EXIT_X;
/** 그 자리 사람이 다니는 통로 y */
const laneOf = (k: DeskKey) => (isBack(k) ? LOW_Y : AISLE_Y);
/** 자리에서 통로까지 (마지막 점이 통로 위) */
function exitPath(k: DeskKey): Pt[] {
  const s = SEAT_PX[k];
  if (isTop(k)) { const x = TOP_EXIT_X[k] as number; return [{ x, y: s.y }, { x, y: AISLE_Y }]; }
  return [{ x: s.x, y: laneOf(k) }];
}
/** 다른 부서에게 말을 전할 때 서는 곳: 받는 자리 옆 통로 */
function besideOf(k: DeskKey): Pt {
  const s = SEAT_PX[k];
  const y = isBack(k) ? LOW_Y : isTop(k) ? AISLE_Y : AISLE_Y + 22;
  return { x: s.x + 46, y };
}
/** 길: 자리 → 그 자리 통로 → (통로가 다르면 옆길) → 목적지 통로 → 목적지 */
function pathTo(from: DeskKey, dest: Pt, destLane: number): Pt[] {
  const pts = exitPath(from);
  const lane = pts[pts.length - 1].y;
  if (lane !== destLane) pts.push({ x: SIDE_X, y: lane }, { x: SIDE_X, y: destLane });
  pts.push({ x: dest.x, y: destLane }, dest);
  return pts;
}
/** 같은 길을 거꾸로 걸어 자리로 */
const pathBack = (go: Pt[], seat: Pt) => [...go.slice(0, -1).reverse(), seat];

type Phase = "sit" | "go" | "stay" | "back";
interface Walker {
  pos: Pt; route: Pt[]; back: Pt[]; phase: Phase; dir: Pt; stayUntil: number;
  slot: number | null; say: string | null; face: boolean; queued: Visit | null;
}

function loadImage(src: string): HTMLImageElement {
  const img = new Image();
  img.src = src;
  return img;
}

const REDUCE = "(prefers-reduced-motion: reduce)";
const reducedNow = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(REDUCE).matches;

export function PixelOffice({ view, label }: { view: OfficeView; label: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef(view);
  viewRef.current = view;

  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    const bg = loadImage(BG_SRC);
    const sprites = Array.from({ length: 6 }, (_, i) => loadImage(`${BASE}office/char_${i}.png`));
    let reduced = reducedNow();
    const mq = typeof window.matchMedia === "function" ? window.matchMedia(REDUCE) : null;
    const onMq = () => { reduced = !!mq?.matches; };
    mq?.addEventListener?.("change", onMq);

    const walkers = Object.fromEntries(DESKS.map((d) => [d.key, {
      pos: { ...SEAT_PX[d.key] }, route: [], back: [], phase: "sit", dir: { x: 0, y: 0 }, stayUntil: 0, slot: null, say: null, face: false, queued: null,
    } as Walker])) as Record<DeskKey, Walker>;
    const slots: (DeskKey | null)[] = MEET.map(() => null);
    const handled = new Set<string>();
    const busyCount = () => DESKS.filter((d) => walkers[d.key].phase !== "sit").length;
    const start = (v: Visit) => {
      const w = walkers[v.who];
      if (w.phase !== "sit") { if (!w.queued) w.queued = v; return; } // 끝난 뒤 한 번만 이어서, 그 뒤로 밀린 것은 버린다
      if (busyCount() >= MAX_WALKERS) return; // 넘으면 걷지 않는다 (받는 쪽은 작업 중·말풍선만)
      let dest: Pt, lane: number, slot: number | null = null;
      if (v.to === "orchestrator") {
        slot = slots.findIndex((s) => s === null);
        if (slot < 0) return;
        slots[slot] = v.who;
        dest = MEET[slot]; lane = AISLE_Y;
      } else {
        dest = besideOf(v.to); lane = laneOf(v.to);
      }
      const go = pathTo(v.who, dest, lane);
      Object.assign(w, { route: go, back: pathBack(go, SEAT_PX[v.who]), phase: "go", slot, say: v.say ?? null, face: v.to !== "orchestrator", stayUntil: 0 });
    };

    let last = performance.now();
    let raf = 0;
    const frame = (now: number) => {
      // 프레임이 드물게 오거나 멈췄다 와도(가려진 창·느린 기기) 걷기는 실제 지난 시간대로 (리플레이 시계와 맞춘다)
      const dt = Math.min(10, (now - last) / 1000);
      last = now;
      const v = viewRef.current;
      const wall = Date.now();
      // 새로 들어온 걸어가기 (같은 기록은 한 번만. 리플레이를 다시 보면 처음 본 시각이 달라 다시 걷는다)
      for (const visit of v.visits) {
        const key = `${visit.id}@${visit.at}`;
        if (handled.has(key)) continue;
        handled.add(key);
        if (!reduced) start(visit);
      }
      for (const d of DESKS) {
        const w = walkers[d.key];
        if (w.phase === "sit") continue;
        if (reduced) { Object.assign(w, { pos: { ...SEAT_PX[d.key] }, route: [], phase: "sit", queued: null }); if (w.slot !== null) slots[w.slot] = null; w.slot = null; continue; }
        let budget = SPEED * dt;
        while (w.route.length && budget > 0) {
          const p = w.route[0];
          const dx = p.x - w.pos.x, dy = p.y - w.pos.y, dist = Math.hypot(dx, dy);
          if (dist > 0) w.dir = { x: dx / dist, y: dy / dist };
          if (dist <= budget) { w.pos = { ...p }; budget -= dist; w.route.shift(); }
          else { w.pos = { x: w.pos.x + (dx / dist) * budget, y: w.pos.y + (dy / dist) * budget }; budget = 0; }
        }
        if (w.route.length) continue;
        if (w.phase === "go") { w.phase = "stay"; w.stayUntil = wall + (w.slot !== null ? STAY_ORCH_MS : STAY_DESK_MS); }
        else if (w.phase === "stay" && wall >= w.stayUntil) { w.phase = "back"; w.route = w.back; w.say = null; }
        else if (w.phase === "back") {
          w.phase = "sit"; w.pos = { ...SEAT_PX[d.key] };
          if (w.slot !== null) slots[w.slot] = null;
          w.slot = null;
          const next = w.queued;
          w.queued = null;
          if (next) start(next);
        }
      }
      draw(ctx, { bg, sprites }, v, now / 1000, wall, walkers, reduced);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); mq?.removeEventListener?.("change", onMq); };
  }, []);

  return (
    <canvas ref={ref} width={W} height={H} role="img" aria-label={label}
      className="block h-auto w-full rounded-2xl [image-rendering:pixelated]" />
  );
}

// ---------------------------------------------------------------- 그리기
type Mode = "sit" | "walk" | "stand";
type Art = { bg: HTMLImageElement; sprites: HTMLImageElement[] };
const ready = (img: HTMLImageElement) => img.complete && img.naturalWidth > 0;

function draw(ctx: CanvasRenderingContext2D, art: Art, v: OfficeView, t: number, wall: number, walkers: Record<DeskKey, Walker>, reduced: boolean) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.imageSmoothingEnabled = false;
  if (ready(art.bg)) ctx.drawImage(art.bg, 0, 0, W, H);
  else { ctx.fillStyle = "#d6d3cf"; ctx.fillRect(0, 0, W, H); }

  // 사람: 발끝 y 순서로 그려 앞사람이 위에 오게 한다
  const people = DESKS.map((d) => {
    const w = walkers[d.key];
    const mode: Mode = w.phase === "sit" ? "sit" : w.route.length ? "walk" : "stand";
    return { key: d.key, p: w.phase === "sit" ? SEAT_PX[d.key] : w.pos, mode, w };
  }).sort((a, b) => a.p.y - b.p.y);

  for (const { key, p, mode, w } of people) {
    const st = v.desks[key].state;
    // 불꽃은 자리에서 일할 때만 (일을 받으러 걷는 동안은 그림자만)
    if (st === "working" && mode === "sit") flame(ctx, p, reduced ? 0 : t, SEED[key] * 1.7);
    else statusGlow(ctx, p, st, t);
    const seat = SEATS[key];
    const sheet = art.sprites[seat.char];
    const pose = mode === "stand" ? { row: 2, col: 1, flip: w.face } : poseOf(mode, st, t, SEED[key], w.dir, !!seat.back);
    if (ready(sheet)) sprite(ctx, sheet, p, pose);
    if (key === "orchestrator") crown(ctx, p);
  }

  boardCard(ctx, v);
  // 이름표는 자리에 앉아 있을 때만 (걷는 동안 숨긴다)
  for (const { key, p, mode } of people) if (mode === "sit") nameTag(ctx, key, p);

  // 말풍선: 앉은 자리는 자리마다 정한 방향, 걷는 사람은 머리 위. 옆 자리에 도착해 서 있으면 전할 말
  const tops = new Map<DeskKey, number>();
  for (const { key, p, mode, w } of people) {
    const dv = v.desks[key];
    const b: Bubble | null = mode === "stand" && w.say ? { text: w.say, kind: "say" } : dv.state === "waiting" ? { text: "! 승인 대기", kind: "warn" } : dv.bubble;
    if (!b) continue;
    const blink = dv.state === "waiting";
    // 이름표(p.y + 16, 높이 26) 바로 아래
    if (mode === "sit" && SEATS[key].bubble === "down") { bubble(ctx, p.x, p.y + 32, b, t, blink, "down"); continue; }
    bubble(ctx, p.x, p.y - SH - 6, b, t, blink, "up");
    tops.set(key, p.y - SH - 6 - 34 - 12);
  }

  // 일이 끝났을 때 "✓ 결과" (사람 승인은 "👤 승인"): 머리 위에서 2.4초 동안 22px 떠오르며 사라진다
  for (const pop of v.pops) {
    const k = (wall - pop.at) / POP_MS;
    if (k < 0 || k >= 1) continue;
    const w = walkers[pop.desk];
    const sit = w.phase === "sit";
    const p = sit ? SEAT_PX[pop.desk] : w.pos;
    // 뒷모습 자리는 머리 위가 앞줄 이름표라, 자기 이름표 위에서 떠오른다
    const baseY = sit && SEATS[pop.desk].bubble === "down" ? p.y + 24 : (tops.get(pop.desk) ?? p.y - SH - 8) - 8;
    floatText(ctx, pop.text, p.x, baseY - (reduced ? 0 : 22 * k), k < 0.6 ? 1 : (1 - k) / 0.4, !!pop.human);
  }

  if (v.overlay) later(ctx, v.overlay.text, v.overlay.progress);
}

function poseOf(mode: Mode, state: string, t: number, seed: number, dir: Pt, back = false): Pose {
  if (mode === "walk") {
    const col = [0, 1, 2, 1][Math.floor(t * 8) % 4];
    if (Math.abs(dir.y) > Math.abs(dir.x)) return { row: dir.y < 0 ? 1 : 0, col, flip: false };
    return { row: 2, col, flip: dir.x < 0 };
  }
  if (mode === "stand") return { row: 0, col: 1, flip: false };
  // 뒷모습 자리는 앉은 자세에서 줄 1(뒤)을 쓴다. 열 규칙은 같다
  const row = back ? 1 : 0;
  if (state === "working") return { row, col: 3 + (Math.floor(t * 8 + seed) % 2), flip: false };
  if (state === "waiting") return { row, col: 5 + (Math.floor(t * 2) % 2), flip: false };
  // 쉬는 중: 가끔 고개를 돌린다 (뒷모습 자리는 돌리지 않는다)
  if (back) return { row, col: 1, flip: false };
  return { row: Math.floor(t / 3.1 + seed) % 5 === 0 ? 2 : 0, col: 1, flip: seed % 2 === 1 };
}

function sprite(ctx: CanvasRenderingContext2D, sheet: HTMLImageElement, p: Pt, pose: Pose) {
  const fw = sheet.naturalWidth / 7, fh = sheet.naturalHeight / 3;
  ctx.save();
  ctx.translate(Math.round(p.x), Math.round(p.y));
  if (pose.flip) ctx.scale(-1, 1);
  ctx.drawImage(sheet, pose.col * fw, pose.row * fh, fw, fh, -SW / 2, -SH, SW, SH);
  ctx.restore();
}

/** 승인 대기 주황 깜빡임, 쉬는 중 그림자 */
function statusGlow(ctx: CanvasRenderingContext2D, p: Pt, state: string, t: number) {
  const color = state === "waiting" ? (Math.floor(t * 3) % 2 ? "rgba(255,176,32,.75)" : "rgba(255,176,32,.35)") : "rgba(40,20,10,.28)";
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.ellipse(p.x, p.y - 2, SW * 0.55, 7, 0, 0, Math.PI * 2); ctx.fill();
}

/**
 * 일하는 중: 캐릭터 뒤 픽셀 불꽃. 4px 블록 13줄(가운데 높고 가장자리 낮게), 빨강·주황·노랑 3겹,
 * 높이는 시간에 따라 사인으로 일렁이고 위로 불티 3개가 튄다. `phase`로 자리마다 다르게 움직인다 (t=0이면 고정 모양)
 */
const FLAME_COLS = 13, FB = 4;
const FLAME_LAYERS: [number, string][] = [[1, "rgba(230,57,40,.55)"], [0.68, "rgba(255,140,30,.65)"], [0.4, "rgba(255,221,80,.75)"]];
function flame(ctx: CanvasRenderingContext2D, p: Pt, t: number, phase: number) {
  const x0 = Math.round(p.x - (FLAME_COLS * FB) / 2), y0 = Math.round(p.y);
  const maxH = SH * 0.92;
  for (const [scale, color] of FLAME_LAYERS) {
    ctx.fillStyle = color;
    for (let i = 0; i < FLAME_COLS; i++) {
      const edge = Math.abs(i - (FLAME_COLS - 1) / 2) / ((FLAME_COLS - 1) / 2 + 0.5);
      const wave = t ? Math.sin(t * 7 + i * 0.9 + phase) * 0.12 + Math.sin(t * 3.1 + i * 1.7 + phase * 2) * 0.06 : 0;
      const h = Math.max(FB, Math.round((maxH * scale * (1 - edge * edge) * (1 + wave)) / FB) * FB);
      ctx.fillRect(x0 + i * FB, y0 - h, FB, h);
    }
  }
  // 불티
  for (let k = 0; k < 3; k++) {
    const life = t ? ((t * 0.9 + k / 3 + phase * 0.13) % 1) : (k + 1) / 4;
    const sx = x0 + FB * (3 + k * 3 + (t ? Math.round(Math.sin(t * 2 + k + phase) * 1.5) : 0));
    const sy = y0 - maxH - 6 - Math.round(life * 26 / FB) * FB;
    ctx.fillStyle = `rgba(255,${200 - k * 30},70,${(1 - life).toFixed(2)})`;
    ctx.fillRect(sx, sy, FB, FB);
  }
}

function crown(ctx: CanvasRenderingContext2D, p: Pt) {
  const x = Math.round(p.x - 10), y = Math.round(p.y - SH - 6);
  ctx.fillStyle = "#7a4a00"; ctx.fillRect(x - 2, y + 4, 24, 8);
  ctx.fillStyle = "#ffcf3a"; ctx.fillRect(x, y + 6, 20, 5);
  ctx.fillRect(x, y, 4, 6); ctx.fillRect(x + 8, y - 2, 4, 8); ctx.fillRect(x + 16, y, 4, 6);
  ctx.fillStyle = "#e11d48"; ctx.fillRect(x + 9, y + 7, 2, 2);
}

function rr(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

function nameTag(ctx: CanvasRenderingContext2D, key: DeskKey, p: Pt) {
  const d = DESK_BY[key];
  const cx = p.x, cy = p.y + 16;
  ctx.font = `700 17px ${FONT}`;
  const w = ctx.measureText(d.label).width + 36;
  ctx.fillStyle = "rgba(34,20,12,.84)"; rr(ctx, cx - w / 2, cy - 13, w, 26, 13); ctx.fill();
  ctx.fillStyle = d.shirt; ctx.beginPath(); ctx.arc(cx - w / 2 + 14, cy, 5, 0, 7); ctx.fill();
  ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(d.label, cx + 8, cy + 1);
}

/** 떠오르는 결과 글: 초록(사람 승인은 주황) 글자에 진한 테두리 */
function floatText(ctx: CanvasRenderingContext2D, text: string, cx: number, y: number, alpha: number, human: boolean) {
  ctx.save();
  ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
  ctx.font = `800 20px ${FONT}`;
  ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  const w = ctx.measureText(text).width;
  const x = Math.max(6 + w / 2, Math.min(W - 6 - w / 2, cx));
  ctx.lineWidth = 5; ctx.lineJoin = "round";
  ctx.strokeStyle = human ? "#431407" : "#052e16";
  ctx.strokeText(text, x, y);
  ctx.fillStyle = human ? "#f59e0b" : "#4ade80";
  ctx.fillText(text, x, y);
  ctx.restore();
}

/** 화면 전환 ("7일 뒤"): 어둡게 했다가 걷힌다 */
function later(ctx: CanvasRenderingContext2D, text: string, k: number) {
  const a = k < 0.2 ? k / 0.2 : k > 0.8 ? (1 - k) / 0.2 : 1;
  ctx.save();
  ctx.fillStyle = `rgba(8,10,20,${(0.78 * a).toFixed(3)})`;
  ctx.fillRect(0, 0, W, H);
  ctx.globalAlpha = a;
  ctx.font = `800 56px ${FONT}`;
  ctx.fillStyle = "#ffffff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, W / 2, H / 2);
  ctx.restore();
}

/** 라운지 위 화이트보드에 사건 진행 카드를 붙인다 (캠페인 리플레이는 캠페인 단계) */
function boardCard(ctx: CanvasRenderingContext2D, v: OfficeView) {
  const a = toC(BOARD[0], BOARD[1]), b = toC(BOARD[2], BOARD[3]);
  const x = a.x, y = a.y, w = b.x - a.x, h = b.y - a.y;
  ctx.fillStyle = "rgba(255,255,252,.94)"; rr(ctx, x, y, w, h, 6); ctx.fill();
  ctx.strokeStyle = "#8e959e"; ctx.lineWidth = 2; ctx.stroke();
  ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
  if (v.board) {
    const { title, value, done, total } = v.board;
    ctx.font = `600 14px ${FONT}`; ctx.fillStyle = "#64748b";
    ctx.fillText(title, x + 12, y + 24);
    ctx.font = `800 21px ${FONT}`; ctx.fillStyle = done >= total ? "#16a34a" : "#d97706";
    ctx.fillText(value, x + 12, y + 54);
    progressBar(ctx, x, y, w, h, done, total, done >= total ? "#16a34a" : "#f59e0b");
    return;
  }
  const { done, total } = v.progress;
  ctx.font = `600 15px ${FONT}`; ctx.fillStyle = "#64748b";
  ctx.fillText(total ? "사건 진행" : "총괄 사건", x + 12, y + 24);
  ctx.font = `800 22px ${FONT}`; ctx.textAlign = "right";
  const txt = !v.incident ? "없음" : v.status === "alert" ? `${done}/${total}` : v.status === "degraded" ? "일부 미완료" : "해결";
  ctx.fillStyle = !v.incident ? "#475569" : v.status === "alert" ? "#d97706" : v.status === "degraded" ? "#ea580c" : "#16a34a";
  ctx.fillText(txt, x + w - 12, y + 27);
  if (v.incident && v.status === "alert") {
    ctx.font = `600 14px ${FONT}`; ctx.fillStyle = "#334155"; ctx.textAlign = "left";
    ctx.fillText("결제 장애 대응", x + 12, y + 50);
  }
  if (total > 0) progressBar(ctx, x, y, w, h, done, total, v.status === "alert" ? "#f59e0b" : v.status === "degraded" ? "#ea580c" : "#16a34a");
}

function progressBar(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, done: number, total: number, color: string) {
  const bw = w - 24;
  ctx.fillStyle = "#e2e8f0"; ctx.fillRect(x + 12, y + h - 20, bw, 8);
  ctx.fillStyle = color;
  ctx.fillRect(x + 12, y + h - 20, Math.round((Math.min(done, total) / total) * bw), 8);
}

/** 글을 폭(px)에 맞게 줄이고 "…"로 끝낸다 */
function clip(ctx: CanvasRenderingContext2D, s: string, room: number): string {
  if (ctx.measureText(s).width <= room) return s;
  let out = s;
  while (out && ctx.measureText(`${out}…`).width > room) out = out.slice(0, -1);
  return `${out}…`;
}

/**
 * `up`: 꼬리가 아래(머리 위 말풍선, anchor = 꼬리 끝 아래 기준).
 * `down`: 꼬리가 위(이름표 아래 말풍선, anchor = 꼬리 끝). 캔버스 아래로 넘치면 안으로 올려 붙인다.
 * 폭은 BUBBLE_MAX_W까지. 넘는 글은 줄여서 "…"로 끝낸다 (글 길이 규칙 short가 먼저 적용된다)
 */
function bubble(ctx: CanvasRenderingContext2D, cx: number, anchor: number, b: Bubble, t: number, blink: boolean, side: Side) {
  ctx.font = `700 18px ${FONT}`;
  const think = b.kind === "think";
  const room = BUBBLE_MAX_W - 24 - (think ? ctx.measureText("...").width : 0);
  const base = clip(ctx, b.text, room);
  const txt = think ? base + ".".repeat(1 + (Math.floor(t * 3) % 3)) : base;
  const w = Math.ceil(ctx.measureText(think ? `${base}...` : txt).width) + 24, h = 34;
  const x = Math.round(Math.max(6, Math.min(W - w - 6, cx - w / 2)));
  const y = Math.round(side === "up" ? anchor - h - 12 : Math.min(anchor + 12, H - 4 - 3 - h));
  const look: Record<string, [string, string, string]> = {
    say: ["#ffffff", "#1d2233", "#1d2233"], think: ["#eef1ff", "#2e2688", "#2e2688"], ok: ["#dcf7e6", "#065f46", "#065f46"],
    warn: [blink && Math.floor(t * 2.5) % 2 ? "#ffb020" : "#ffc95a", "#3b2600", "#6b4500"], alert: ["#ff4d5e", "#ffffff", "#7a1420"],
  };
  const [bg, fg, bd] = look[b.kind];
  ctx.fillStyle = bd; ctx.fillRect(x - 3, y, w + 6, h); ctx.fillRect(x, y - 3, w, h + 6);
  ctx.fillStyle = bg; ctx.fillRect(x, y, w, h);
  if (side === "up") {
    const ax = Math.round(cx);
    ctx.fillStyle = bd; ctx.fillRect(ax - 7, y + h, 14, 4); ctx.fillRect(ax - 4, y + h + 4, 8, 4); ctx.fillRect(ax - 1, y + h + 8, 3, 4);
    ctx.fillStyle = bg; ctx.fillRect(ax - 4, y + h, 8, 4); ctx.fillRect(ax - 1, y + h + 4, 3, 1);
  } else {
    const ax = Math.round(cx);
    ctx.fillStyle = bd; ctx.fillRect(ax - 7, y - 4, 14, 4); ctx.fillRect(ax - 4, y - 8, 8, 4); ctx.fillRect(ax - 1, y - 12, 3, 4);
    ctx.fillStyle = bg; ctx.fillRect(ax - 4, y - 4, 8, 4); ctx.fillRect(ax - 1, y - 5, 3, 1);
  }
  ctx.fillStyle = fg; ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(txt, x + 12, y + h / 2 + 1);
}
