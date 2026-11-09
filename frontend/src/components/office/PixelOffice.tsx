import { useEffect, useRef } from "react";
import { DESKS, type Bubble, type DeskKey, type OfficeView } from "@/lib/office";

// 픽셀 사무실 캔버스. 장면(SCENES)마다 배경·자리·통로·보드 카드 위치가 다르고, 사람·이름표·말풍선은 코드로 그린다.
// - cowork(기본): "Startup Cowork" 장면 (벽돌 벽·커피 바·빈백 라운지). 배경과 캐릭터 스프라이트는
//   Sebaspelusa/pixel-office(MIT)와 pablodelucca/pixel-agents(MIT)에서 가져왔다(public/office/CREDITS.md).
//   배경은 원본(2912×1440)에서 체크무늬 테두리를 잘라낸 방 안쪽만 쓴다.
// - seoul: 서울 사무실(통유리 창 너머 한강·63빌딩). 사람·이름표가 없는 빈 사무실 그림 위에 9자리를 그린다.
// 그림 색은 이미지·캔버스 전용이다 (화면 UI는 디자인 토큰을 쓴다).

const BASE = import.meta.env.BASE_URL;
const FONT = '"Pretendard Variable", Pretendard, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif';

type Pt = { x: number; y: number };
type Pose = { row: number; col: number; flip: boolean };
type Side = "up" | "down" | "left";

/** 자리(원본 이미지 비율, 의자 위 발끝)와 스프라이트. 그림이 6벌이라 멀리 앉은 자리끼리 같은 그림을 쓴다.
 *  `back`: 책상 앞쪽 의자라 뒷모습으로 앉는다. `bubble`: 말풍선 방향(up 머리 위, down 이름표 아래, left 머리 왼쪽 옆).
 *  `lift`: up 말풍선을 이만큼 더 올린다(옆자리 말풍선과 겹치지 않게) */
interface Seat { fx: number; fy: number; char: number; back?: boolean; bubble: Side; lift?: number }

export interface Scene {
  /** 화면 이름 (장면 고르기) */
  label: string;
  bg: string;
  /** 캔버스 픽셀 */
  W: number; H: number;
  /** 원본 이미지 비율 좌표 → 캔버스 좌표 */
  toC: (fx: number, fy: number) => Pt;
  /** 캐릭터 배율 (스프라이트 한 칸 16×32의 몇 배) */
  sc: number;
  seats: Record<DeskKey, Seat>;
  /** 책상 사이 통로(원본 비율 y). 총괄은 이 줄을 따라 걷고, 맡긴 부서 바로 앞(통로)에 선다 */
  aisleFy: number;
  /** 총괄이 설 때 자리 x에서 얼마나 옆에 서는지 */
  standDx: (k: DeskKey) => number;
  /** 보드 카드 사각형 (원본 비율 fx0, fy0, fx1, fy1) */
  board: [number, number, number, number];
}

export type SceneKey = "cowork" | "seoul";

export const SCENES: Record<SceneKey, Scene> = {
  cowork: {
    label: "기본",
    bg: `${BASE}office/startup-cowork.jpg`,
    W: 1280, H: 591, // 캔버스 픽셀 = 잘라낸 배경 비율(2650×1224)
    // 잘라낸 영역: x 0.045~0.955, y 0.085~0.935
    toC: (fx, fy) => ({ x: ((fx - 0.045) / 0.91) * 1280, y: ((fy - 0.085) / 0.85) * 591 }),
    sc: 2.5,
    seats: {
      infra: { fx: 0.151, fy: 0.392, char: 0, bubble: "up" },
      data: { fx: 0.282, fy: 0.392, char: 2, bubble: "up", lift: 44 },
      support: { fx: 0.366, fy: 0.392, char: 4, bubble: "up" },
      finance: { fx: 0.496, fy: 0.392, char: 1, bubble: "up", lift: 44 },
      // 아래 줄 책상 사람은 위 줄 이름표를 가리지 않게 이름표 아래로
      planning: { fx: 0.189, fy: 0.585, char: 5, bubble: "down" },
      marketing: { fx: 0.302, fy: 0.585, char: 1, bubble: "down" },
      hr: { fx: 0.411, fy: 0.585, char: 0, bubble: "down" },
      // 기획 맞은편 빈 의자 (왼쪽 아래 책상 앞쪽). 아래가 캔버스 끝이라 늘 머리 위에
      dev: { fx: 0.192, fy: 0.835, char: 4, back: true, bubble: "up" },
      orchestrator: { fx: 0.591, fy: 0.676, char: 3, bubble: "down" },
    },
    aisleFy: 0.52,
    standDx: (k) => (k === "orchestrator" ? 0 : 44),
    board: [0.765, 0.135, 0.9, 0.28],
  },
  seoul: {
    label: "서울",
    bg: `${BASE}office/seoul-office.webp`,
    W: 1280, H: 692, // 원본 1392×752 비율, 자르기 없음
    toC: (fx, fy) => ({ x: fx * 1280, y: fy * 692 }),
    sc: 2.2,
    seats: {
      // 위 책상 뒤쪽 의자 (앞모습). 위 책상 앞쪽 의자 2개는 위 줄 이름표와 겹쳐 비워 둔다
      infra: { fx: 0.272, fy: 0.436, char: 0, bubble: "up" },
      data: { fx: 0.413, fy: 0.436, char: 2, bubble: "up" },
      // 아래 책상 뒤쪽 의자 (앞모습)
      planning: { fx: 0.246, fy: 0.654, char: 5, bubble: "up" },
      marketing: { fx: 0.359, fy: 0.654, char: 1, bubble: "up" },
      hr: { fx: 0.471, fy: 0.654, char: 0, bubble: "up" },
      // 아래 책상 앞쪽 의자 (뒷모습). 말풍선은 머리 왼쪽 옆에 (위 줄 이름표를 가리지 않게)
      dev: { fx: 0.248, fy: 0.81, char: 4, back: true, bubble: "left" },
      support: { fx: 0.359, fy: 0.81, char: 2, back: true, bubble: "left" },
      finance: { fx: 0.472, fy: 0.81, char: 1, back: true, bubble: "left" },
      // 라운지 빈백 (앞모습, 왕관)
      orchestrator: { fx: 0.795, fy: 0.638, char: 3, bubble: "up" },
    },
    aisleFy: 0.563,
    standDx: (k) => (k === "orchestrator" ? 0 : 30),
    board: [0.74, 0.305, 0.867, 0.445],
  },
};
export const DEFAULT_SCENE: SceneKey = "cowork";

/** 대기 동작(고개 돌리기·좌우) 씨앗. 장면과 상관없이 예전 8자리 순번을 쓴다 (개발 자리를 끼워 넣어도 그대로) */
const SEED = Object.fromEntries(DESKS.filter((d) => d.key !== "dev").map((d, i) => [d.key, i])) as Record<DeskKey, number>;
SEED.dev = 8;
const DESK_BY = Object.fromEntries(DESKS.map((d) => [d.key, d])) as Record<DeskKey, (typeof DESKS)[number]>;
const SPEED = 220; // 캔버스 px/초

// 스프라이트 시트: 16×32 칸 7열 × 3줄 (줄 0 앞, 1 뒤, 2 옆). 열 0~2 걷기, 3~4 타이핑, 5~6 읽기
const FW = 16, FH = 32;

/** 장면 하나에서 그리는 데 쓰는 값 (캔버스 좌표) */
interface Layout {
  s: Scene; W: number; H: number; SW: number; SH: number;
  seatPx: Record<DeskKey, Pt>; aisleY: number; home: Pt;
}
function layoutOf(s: Scene): Layout {
  const seatPx = Object.fromEntries(DESKS.map((d) => [d.key, s.toC(s.seats[d.key].fx, s.seats[d.key].fy)])) as Record<DeskKey, Pt>;
  return { s, W: s.W, H: s.H, SW: FW * s.sc, SH: FH * s.sc, seatPx, aisleY: s.toC(0, s.aisleFy).y, home: seatPx.orchestrator };
}
const standOf = (L: Layout, k: DeskKey): Pt => ({ x: L.seatPx[k].x + L.s.standDx(k), y: L.aisleY });

function loadImage(src: string, onError?: () => void): HTMLImageElement {
  const img = new Image();
  if (onError) img.onerror = onError;
  img.src = src;
  return img;
}

export function PixelOffice({ view, label, scene = DEFAULT_SCENE, onSceneMissing }: {
  view: OfficeView; label: string; scene?: SceneKey; onSceneMissing?: (scene: SceneKey) => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef(view);
  viewRef.current = view;
  const missingRef = useRef(onSceneMissing);
  missingRef.current = onSceneMissing;
  const L = layoutOf(SCENES[scene]);

  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    const lay = layoutOf(SCENES[scene]);
    const bg = loadImage(lay.s.bg, () => missingRef.current?.(scene));
    const sprites = Array.from({ length: 6 }, (_, i) => loadImage(`${BASE}office/char_${i}.png`));
    const HOME = lay.home;
    const pos: Pt = { ...HOME };
    let route: Pt[] = [];
    let target: DeskKey | null = null;
    let last = performance.now();
    let raf = 0;
    let dir: Pt = { x: 0, y: 0 };

    const plan = (to: DeskKey | null) => {
      const dest = to ? standOf(lay, to) : HOME;
      route = [{ x: pos.x, y: lay.aisleY }, { x: dest.x, y: lay.aisleY }, dest];
    };

    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const v = viewRef.current;
      if (v.walkTo !== target) { target = v.walkTo; plan(target); }
      let moving = false;
      let budget = SPEED * dt;
      while (route.length && budget > 0) {
        const p = route[0];
        const dx = p.x - pos.x, dy = p.y - pos.y, d = Math.hypot(dx, dy);
        if (d > 0) dir = { x: dx / d, y: dy / d };
        if (d <= budget) { pos.x = p.x; pos.y = p.y; budget -= d; route.shift(); }
        else { pos.x += (dx / d) * budget; pos.y += (dy / d) * budget; budget = 0; moving = true; }
      }
      const seated = !route.length && target === null && pos.x === HOME.x && pos.y === HOME.y;
      draw(ctx, lay, { bg, sprites }, v, now / 1000, pos, seated ? "sit" : moving ? "walk" : "stand", dir);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [scene]);

  return (
    <canvas ref={ref} width={L.W} height={L.H} role="img" aria-label={label}
      className="block h-auto w-full rounded-2xl [image-rendering:pixelated]" />
  );
}

// ---------------------------------------------------------------- 그리기
type Mode = "sit" | "walk" | "stand";
type Art = { bg: HTMLImageElement; sprites: HTMLImageElement[] };
const ready = (img: HTMLImageElement) => img.complete && img.naturalWidth > 0;

function draw(ctx: CanvasRenderingContext2D, L: Layout, art: Art, v: OfficeView, t: number, orch: Pt, orchMode: Mode, dir: Pt) {
  const { W, H, SH } = L;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.imageSmoothingEnabled = false;
  if (ready(art.bg)) ctx.drawImage(art.bg, 0, 0, W, H);
  else { ctx.fillStyle = "#b8743a"; ctx.fillRect(0, 0, W, H); }
  // 조명 아래 따뜻한 빛
  ctx.fillStyle = "rgba(255,200,120,.05)"; ctx.fillRect(0, 0, W, H);

  // 사람: 발끝 y 순서로 그려 앞사람이 위에 오게 한다
  const people = DESKS.map((d) => {
    const away = d.key === "orchestrator" && orchMode !== "sit";
    const p = away ? orch : L.seatPx[d.key];
    return { key: d.key, p, mode: away ? orchMode : ("sit" as Mode) };
  }).sort((a, b) => a.p.y - b.p.y);

  for (const { key, p, mode } of people) {
    const st = v.desks[key].state;
    statusGlow(ctx, L, p, st, t);
    const seat = L.s.seats[key];
    const sheet = art.sprites[seat.char];
    if (ready(sheet)) sprite(ctx, L, sheet, p, poseOf(mode, st, t, SEED[key], dir, !!seat.back));
    if (key === "orchestrator") crown(ctx, L, p);
  }

  boardCard(ctx, L, v);
  for (const { key, p } of people) nameTag(ctx, key, p);

  // 말풍선: 자리마다 정한 방향(장면 설정). 걷는 총괄은 머리 위(맡긴 부서 말풍선이 있으면 한 칸 더 위)
  DESKS.forEach((d) => {
    const dv = v.desks[d.key];
    const away = d.key === "orchestrator" && orchMode !== "sit";
    const p = away ? orch : L.seatPx[d.key];
    const b: Bubble | null = dv.state === "waiting" ? { text: "! 승인 대기", kind: "warn" } : dv.bubble;
    if (!b) return;
    const seat = L.s.seats[d.key];
    const blink = dv.state === "waiting";
    if (!away && seat.bubble === "down") { bubble(ctx, L, p.x, p.y + 32, b, t, blink, "down"); return; }
    if (!away && seat.bubble === "left") { bubble(ctx, L, p.x - L.SW / 2, p.y - SH * 0.6, b, t, blink, "left"); return; }
    const crowded = away && v.walkTo !== null && (v.desks[v.walkTo].state === "waiting" || v.desks[v.walkTo].bubble !== null);
    const lift = (away ? 0 : (seat.lift ?? 0)) + (crowded ? 48 : 0);
    bubble(ctx, L, p.x, p.y - SH - 6 - lift, b, t, blink, "up");
  });
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

function sprite(ctx: CanvasRenderingContext2D, L: Layout, sheet: HTMLImageElement, p: Pt, pose: Pose) {
  const { SW, SH } = L;
  const fw = sheet.naturalWidth / 7, fh = sheet.naturalHeight / 3;
  ctx.save();
  ctx.translate(Math.round(p.x), Math.round(p.y));
  if (pose.flip) ctx.scale(-1, 1);
  ctx.drawImage(sheet, pose.col * fw, pose.row * fh, fw, fh, -SW / 2, -SH, SW, SH);
  ctx.restore();
}

function statusGlow(ctx: CanvasRenderingContext2D, L: Layout, p: Pt, state: string, t: number) {
  const color = state === "working" ? "rgba(61,220,132,.55)" : state === "waiting" ? (Math.floor(t * 3) % 2 ? "rgba(255,176,32,.75)" : "rgba(255,176,32,.35)") : "rgba(40,20,10,.28)";
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.ellipse(p.x, p.y - 2, L.SW * 0.55, 7, 0, 0, Math.PI * 2); ctx.fill();
}

function crown(ctx: CanvasRenderingContext2D, L: Layout, p: Pt) {
  const x = Math.round(p.x - 10), y = Math.round(p.y - L.SH - 6);
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

/** 벽의 보드 자리에 사건 진행 카드를 붙인다 */
function boardCard(ctx: CanvasRenderingContext2D, L: Layout, v: OfficeView) {
  const [fx0, fy0, fx1, fy1] = L.s.board;
  const a = L.s.toC(fx0, fy0), b = L.s.toC(fx1, fy1);
  const x = a.x, y = a.y, w = b.x - a.x, h = b.y - a.y;
  const { done, total } = v.progress;
  ctx.fillStyle = "rgba(255,255,252,.94)"; rr(ctx, x, y, w, h, 6); ctx.fill();
  ctx.strokeStyle = "#8e959e"; ctx.lineWidth = 2; ctx.stroke();
  ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
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
  if (total > 0) {
    const bw = w - 24;
    ctx.fillStyle = "#e2e8f0"; ctx.fillRect(x + 12, y + h - 20, bw, 8);
    ctx.fillStyle = v.status === "alert" ? "#f59e0b" : v.status === "degraded" ? "#ea580c" : "#16a34a";
    ctx.fillRect(x + 12, y + h - 20, Math.round((done / total) * bw), 8);
  }
}

/**
 * `up`: 꼬리가 아래(머리 위 말풍선, anchor = 꼬리 끝 아래 기준). `down`: 꼬리가 위(anchor = 말풍선 꼬리 끝).
 * `left`: 꼬리가 오른쪽(머리 왼쪽 옆 말풍선, cx = 꼬리 끝 x, anchor = 말풍선 세로 가운데)
 */
function bubble(ctx: CanvasRenderingContext2D, L: Layout, cx: number, anchor: number, b: Bubble, t: number, blink: boolean, side: Side) {
  ctx.font = `700 18px ${FONT}`;
  const think = b.kind === "think";
  const txt = think ? b.text + ".".repeat(1 + (Math.floor(t * 3) % 3)) : b.text;
  const w = Math.ceil(ctx.measureText(think ? `${b.text}...` : txt).width) + 24, h = 34;
  const x = side === "left"
    ? Math.round(Math.max(6, cx - w - 14))
    : Math.round(Math.max(6, Math.min(L.W - w - 6, cx - w / 2)));
  const y = Math.round(side === "up" ? anchor - h - 12 : side === "down" ? anchor + 12 : anchor - h / 2);
  const look: Record<string, [string, string, string]> = {
    say: ["#ffffff", "#1d2233", "#1d2233"], think: ["#eef1ff", "#2e2688", "#2e2688"], ok: ["#dcf7e6", "#065f46", "#065f46"],
    warn: [blink && Math.floor(t * 2.5) % 2 ? "#ffb020" : "#ffc95a", "#3b2600", "#6b4500"], alert: ["#ff4d5e", "#ffffff", "#7a1420"],
  };
  const [bg, fg, bd] = look[b.kind];
  const ax = Math.round(cx);
  ctx.fillStyle = bd; ctx.fillRect(x - 3, y, w + 6, h); ctx.fillRect(x, y - 3, w, h + 6);
  ctx.fillStyle = bg; ctx.fillRect(x, y, w, h);
  if (side === "up") {
    ctx.fillStyle = bd; ctx.fillRect(ax - 7, y + h, 14, 4); ctx.fillRect(ax - 4, y + h + 4, 8, 4); ctx.fillRect(ax - 1, y + h + 8, 3, 4);
    ctx.fillStyle = bg; ctx.fillRect(ax - 4, y + h, 8, 4); ctx.fillRect(ax - 1, y + h + 4, 3, 1);
  } else if (side === "down") {
    ctx.fillStyle = bd; ctx.fillRect(ax - 7, y - 4, 14, 4); ctx.fillRect(ax - 4, y - 8, 8, 4); ctx.fillRect(ax - 1, y - 12, 3, 4);
    ctx.fillStyle = bg; ctx.fillRect(ax - 4, y - 4, 8, 4); ctx.fillRect(ax - 1, y - 5, 3, 1);
  } else {
    const ay = y + h / 2, rx = x + w;
    ctx.fillStyle = bd; ctx.fillRect(rx, ay - 7, 4, 14); ctx.fillRect(rx + 4, ay - 4, 4, 8); ctx.fillRect(rx + 8, ay - 1, 4, 3);
    ctx.fillStyle = bg; ctx.fillRect(rx, ay - 4, 4, 8); ctx.fillRect(rx + 4, ay - 1, 1, 3);
  }
  ctx.fillStyle = fg; ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(txt, x + 12, y + h / 2 + 1);
}
