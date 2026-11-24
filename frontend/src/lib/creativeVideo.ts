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
  if (ref) parts.push(`참조: ${ref}`);
  if (s.model) parts.push(s.model);
  if (typeof s.seconds === "number" && s.seconds > 0) {
    const from = typeof s.seconds_clamped_from === "number" && s.seconds_clamped_from > s.seconds ? `(${num(s.seconds_clamped_from)}초에서 줄임)` : "";
    parts.push(`${num(s.seconds)}초${from}`);
  }
  if (s.aspect) parts.push(s.aspect);
  if (typeof s.credits_estimate === "number") parts.push(`예상 약 ${num(s.credits_estimate)}`);
  return parts.length ? parts.join(" · ") : null;
}
