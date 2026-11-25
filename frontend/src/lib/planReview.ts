// 제작안 판 기록 (11/26 마케팅↔기획 재협업). 의뢰 제작 기록 render.plan_review(마케팅 plan_review.py)를
// 의뢰 카드 ⑦ 마케팅 준비에 "제작안 1판 · 마케팅 확인 ✕ 2개 → 제작안 2판 · 마케팅 확인 ✓"로 보인다.
// 순수 계산만 한다 (node --test planReview.test.ts). 모양이 틀리면 null(그리지 않는다)

export interface PlanReviewProblem { kind: string; scene: number | null; detailKo: string }
export interface PlanReviewVersion { version: number; passed: boolean; problems: PlanReviewProblem[] }
export interface PlanReviewView {
  versions: PlanReviewVersion[];
  requested: boolean; // 기획에 고치기를 요청했나
  outcome: string; // revised · already · revise_limit · planning_timeout · planning_<이유>
  needsHuman: boolean;
}

type Json = Record<string, unknown>;
const obj = (v: unknown): Json | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : null);
const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
const int = (v: unknown) => (typeof v === "number" && Number.isInteger(v) ? v : null);

export function toPlanReview(raw: unknown): PlanReviewView | null {
  const o = obj(raw);
  if (!o || !Array.isArray(o.versions)) return null;
  const versions = o.versions.flatMap((x) => {
    const v = obj(x);
    const version = v ? int(v.version) : null;
    if (!v || version === null) return [];
    const problems = (Array.isArray(v.problems) ? v.problems : []).flatMap((y) => {
      const p = obj(y);
      const detailKo = p ? str(p.detail_ko ?? p.detailKo) : "";
      return p && detailKo ? [{ kind: str(p.kind), scene: int(p.scene), detailKo }] : [];
    });
    return [{ version, passed: v.passed === true, problems }];
  });
  if (!versions.length) return null;
  return { versions, requested: o.requested === true, outcome: str(o.outcome), needsHuman: o.needs_human === true || o.needsHuman === true };
}

/** 판 한 칸 제목·칩·설명 */
export function versionCell(r: PlanReviewView, v: PlanReviewVersion, i: number): { title: string; chip: string; ok: boolean; note: string; current: boolean } {
  const last = i === r.versions.length - 1;
  const chip = v.passed ? "마케팅 확인 ✓" : `마케팅 확인 ✕ ${v.problems.length}개`;
  const note = i === 0 ? "기획 자체 검사 → 마케팅 확인" : "마케팅 요청으로 기획이 고침 · 1회";
  return { title: `제작안 ${v.version}판${last ? " (사용 중)" : ""}`, chip, ok: v.passed, note, current: last };
}

/** 판 기록 아래 한 줄 (고치기를 못 받았거나 2판도 ✕면 사람이 정한다) */
export function reviewNote(r: PlanReviewView): string {
  if (r.outcome === "planning_timeout") return "기획이 제한 시간 안에 고친 판을 주지 못해 1판으로 준비했어요. 사람 확인이 필요해요.";
  if (r.outcome.startsWith("planning_")) return "기획에 고치기를 요청하지 못해 1판으로 준비했어요. 사람 확인이 필요해요.";
  if (r.needsHuman) return "고치기 요청은 1회까지예요. 통과 못 한 판으로 준비했어요. 사람 확인이 필요해요.";
  return "고치기 요청은 1회까지예요.";
}

/** 문제 한 줄 "장면 2 · 장면 길이 2초 (영상 모델 최소 3초)" */
export const reviewProblemLine = (p: PlanReviewProblem) => (p.scene ? `장면 ${p.scene} · ${p.detailKo}` : p.detailKo);
