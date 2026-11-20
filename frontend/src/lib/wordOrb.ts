// 급상승 검색어 "동그란 단어 구름" 자리 계산 (components/data/KeywordTrend.tsx). 라이브러리 없이 순수 함수. node --test (wordOrb.test.ts).
// - 글자 크기: 0.78rem ~ 2.9rem 사이에서 weight(평소 대비 배수) 비례
// - 놓는 순서: weight 큰 말부터. 가운데에서 아르키메데스 나선(r = a·θ)을 따라 돌며 다른 말과 겹치지 않는(사각형 충돌) 첫 자리
// - 원(반지름 size/2 − 8) 밖으로 나가는 말은 뺀다. 최대 24개. 난수 없음 (같은 입력이면 같은 결과)

export type OrbLevel = "rising" | "warming" | "normal" | "unknown";
export interface OrbWord { text: string; weight: number; level: OrbLevel }
export interface OrbPlaced { text: string; x: number; y: number; fontPx: number; level: OrbLevel; w: number; h: number }

export const ORB_MAX = 24;
export const ORB_MIN_REM = 0.78;
export const ORB_MAX_REM = 2.9;
const REM_PX = 16;
const PAD = 2; // 말 사이 여백 (px)
const STEP = 0.12; // 나선 각 간격 (라디안)
const SPIRAL_A = 1.6; // 한 바퀴(2π)에 약 10px씩 바깥으로

/** 한글(그 밖 넓은 글자)은 글자 크기의 0.95, 라틴·숫자·기호·공백은 0.6 */
export function textWidth(text: string, fontPx: number): number {
  let w = 0;
  for (const ch of text) w += fontPx * (/[\x00-\x7F]/.test(ch) ? 0.6 : 0.95);
  return w;
}

export const lineHeight = (fontPx: number) => fontPx * 1.1;

/** weight 비례 글자 크기 (px). 가장 큰 weight가 2.9rem, 0이면 0.78rem */
export function fontPxOf(weight: number, maxWeight: number): number {
  const min = ORB_MIN_REM * REM_PX;
  const max = ORB_MAX_REM * REM_PX;
  if (!(maxWeight > 0) || !(weight > 0)) return Math.round(min * 100) / 100;
  return Math.round((min + (max - min) * Math.min(1, weight / maxWeight)) * 100) / 100;
}

interface Box { x0: number; y0: number; x1: number; y1: number }
const overlaps = (a: Box, b: Box) => a.x0 < b.x1 + PAD && b.x0 < a.x1 + PAD && a.y0 < b.y1 + PAD && b.y0 < a.y1 + PAD;
/** 사각형 네 꼭짓점이 모두 원 안 */
const inCircle = (b: Box, cx: number, cy: number, r: number) =>
  [[b.x0, b.y0], [b.x1, b.y0], [b.x0, b.y1], [b.x1, b.y1]].every(([x, y]) => (x - cx) ** 2 + (y - cy) ** 2 <= r * r);

export function orbLayout(words: OrbWord[], size: number): OrbPlaced[] {
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 8;
  if (radius <= 0) return [];
  const clean = words.filter((w) => w.text.trim()).map((w, i) => ({ ...w, weight: Number.isFinite(w.weight) && w.weight > 0 ? w.weight : 0, i }));
  // weight 큰 말부터 (같으면 들어온 순서)
  const order = [...clean].sort((a, b) => b.weight - a.weight || a.i - b.i);
  const maxWeight = order[0]?.weight ?? 0;
  const placed: (OrbPlaced & { box: Box })[] = [];
  for (const word of order) {
    if (placed.length >= ORB_MAX) break;
    const fontPx = fontPxOf(word.weight, maxWeight);
    const w = textWidth(word.text, fontPx);
    const h = lineHeight(fontPx);
    let spot: Box | null = null;
    for (let t = 0; ; t += STEP) {
      const r = SPIRAL_A * t;
      if (r > radius) break;
      const x = cx + r * Math.cos(t);
      const y = cy + r * Math.sin(t);
      const box = { x0: x - w / 2, y0: y - h / 2, x1: x + w / 2, y1: y + h / 2 };
      if (!inCircle(box, cx, cy, radius)) continue;
      if (placed.some((p) => overlaps(p.box, box))) continue;
      spot = box;
      break;
    }
    if (!spot) continue; // 원 안에 자리가 없으면 뺀다
    placed.push({ text: word.text, level: word.level, fontPx, w, h, x: (spot.x0 + spot.x1) / 2, y: (spot.y0 + spot.y1) / 2, box: spot });
  }
  return placed.map(({ box: _box, ...p }) => p);
}
