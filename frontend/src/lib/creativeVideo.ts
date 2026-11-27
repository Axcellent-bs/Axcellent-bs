// 영상 승인·제작 화면의 요약 한 줄. node --test (creativeVideo.test.ts)
// 제작 기록 render.video.summary(새 필드, 없을 수 있음)를 "참조: … · 모델 · 15초(20초에서 줄임) · 9:16 · 예상 약 22.5"로 만든다.
// 없는 부분은 지어내지 않고 뺀다. summary가 없으면 줄 자체를 숨긴다.

export interface CreativeVideoSummary {
  model?: string | null;
  seconds?: number | null;
  /** 줄이기 전 길이(20초 → 15초면 20) */
  seconds_clamped_from?: number | null;
  aspect?: string | null;
  reference?: "character_original" | "storyboard_image" | string | null;
  credits_estimate?: number | null;
  /** 11/25 재생 속도: 실제 배속(1.0이면 배속 안 함)과 만든 길이(= seconds) */
  speed?: number | null;
  generated_seconds?: number | null;
  /** 11/25 키프레임 방식(reference "keyframes"): 키프레임 장 수·그 그림에 쓴 크레딧(모르면 null) */
  keyframes?: number | null;
  keyframe_credits?: number | null;
  /** 11/27 등장 캐릭터 여럿: 캐릭터 수·참조로 올리는 캐릭터 수·모델이 1장만 받는지 */
  cast?: number | null;
  cast_refs?: number | null;
  cast_single_ref?: boolean | null;
}

/** 11/27 영상 모델이 참조 1장만 받으면 안내 한 줄 (등장 캐릭터 여럿일 때만) */
export const CAST_SINGLE_REF_KO = "이 영상 모델은 캐릭터 1개만 참조해요";
export function castRefLine(s: CreativeVideoSummary | null | undefined): string | null {
  if (!s || typeof s.cast !== "number" || s.cast < 2) return null;
  return s.cast_single_ref ? CAST_SINGLE_REF_KO : `캐릭터 ${s.cast_refs ?? s.cast}명 참조`;
}

/** 배속한 영상이면 "18초 만들어 1.2배속 → 15초", 아니면 null */
export function speedLine(s: Pick<CreativeVideoSummary, "speed" | "generated_seconds" | "seconds"> | null | undefined): string | null {
  const made = s?.generated_seconds ?? s?.seconds;
  if (!s || typeof s.speed !== "number" || s.speed <= 1 || typeof made !== "number" || made <= 0) return null;
  return `${num(made)}초 만들어 ${num(s.speed)}배속 → ${num(made / s.speed)}초`;
}

const REFERENCE_KO: Record<string, string> = {
  character_original: "가상 인물 원본",
  storyboard_image: "스토리보드 그림",
};

/** 요약 줄을 보일 영상 상태: 승인 대기 · 만드는 중 · 완료 */
const SHOW_STATUS = new Set(["awaiting_approval", "running", "uploading", "done"]);

const num = (n: number) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10));

/** 보여 줄 글. 보이지 않을 때는 null */
export function videoSummaryLine(status: string | null | undefined, s: CreativeVideoSummary | null | undefined): string | null {
  if (!s || !SHOW_STATUS.has(status ?? "")) return null;
  const parts: string[] = [];
  const ref = s.reference ? REFERENCE_KO[s.reference] : undefined;
  if (s.reference === "keyframes" && typeof s.keyframes === "number" && s.keyframes > 0) {
    const credits = typeof s.keyframe_credits === "number" ? `(그림 약 ${num(s.keyframe_credits)})` : "";
    parts.push(`참조: 키프레임 ${s.keyframes}장${credits}`);
  } else if (ref) parts.push(`참조: ${ref}`);
  if (s.model) parts.push(s.model);
  if (typeof s.seconds === "number" && s.seconds > 0) {
    const from = typeof s.seconds_clamped_from === "number" && s.seconds_clamped_from > s.seconds ? `(${num(s.seconds_clamped_from)}초에서 줄임)` : "";
    parts.push(speedLine(s) ?? `${num(s.seconds)}초${from}`);
  }
  if (s.aspect) parts.push(s.aspect);
  if (typeof s.credits_estimate === "number") parts.push(`예상 약 ${num(s.credits_estimate)}`);
  return parts.length ? parts.join(" · ") : null;
}
