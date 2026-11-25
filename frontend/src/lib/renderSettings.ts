// 마케팅 › SNS·미디어 › 계정·연결 › "만들기 설정" (Higgsfield 그림·영상 모델/길이/비율). 화면 문구·계산을 한곳에 둔다.
// API: GET /api/dashboard/marketing/render-settings · POST .../save · POST .../clear (dashboard-api)
// 서버가 아직 없거나 응답이 이상하면 칸 전체를 숨긴다(지금 화면 그대로). 화면은 값을 지어내지 않는다.

export type Aspect = "9:16" | "1:1" | "16:9";
export const ASPECTS: Aspect[] = ["9:16", "1:1", "16:9"];
/** 값이 "plan"이면 기획서를 따른다 */
export type PlanOr<T> = "plan" | T;

/** 재생 속도(11/25 AI 티 줄이기): 길게 만들고 빠르게 튼다. "1.0" = 배속 끔 */
export type Speed = "1.0" | "1.2" | "1.5";
export const SPEEDS: Speed[] = ["1.0", "1.2", "1.5"];
const isSpeed = (x: unknown): x is Speed => x === "1.0" || x === "1.2" || x === "1.5";

export interface RenderValues {
  image_model: string | null;
  video_model: string | null;
  image_credits_ok: boolean | null;
  video_seconds: PlanOr<number> | null;
  aspect: PlanOr<Aspect> | null;
  /** 서버가 아직 이 칸을 모르면 늘 null (칸도 숨긴다: `speedSupported`) */
  speed: Speed | null;
}
export interface ImageModelOption { id: string; name: string }
/** 11/25: 서버가 모르는 모델은 `min_s`·`max_s`가 null이다(계약 `RenderVideoModel.min_s: int | None`) */
export interface VideoModelOption { id: string; name: string; min_s: number | null; max_s: number | null; aspects: Aspect[] }
export interface RenderSettingsState {
  settings: RenderValues | null;
  defaults: RenderValues;
  models: { image: ImageModelOption[]; video: VideoModelOption[] };
  can_edit: boolean;
  /** 서버 기본값에 `speed` 칸이 있을 때만 재생 속도 칸을 보여 주고 저장 본문에 넣는다 */
  speedSupported: boolean;
}

export const READ_ONLY_NOTE = "보기만 할 수 있어요. 바꾸는 건 회사 관리자가 해요.";
export const FORBIDDEN_NOTE = "회사 관리자만 바꿀 수 있어요";
export const CREDITS_STOP_NOTE = "그림 단계에서 멈춰요";
export const PLAN_LABEL = "기획서 따르기";

const isObj = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === "object" && !Array.isArray(x);
const isAspect = (x: unknown): x is Aspect => x === "9:16" || x === "1:1" || x === "16:9";
const optStr = (x: unknown): string | null | undefined => (x === null || x === undefined ? null : typeof x === "string" ? x : undefined);

function parseValues(x: unknown): RenderValues | null {
  if (!isObj(x)) return null;
  const image_model = optStr(x.image_model);
  const video_model = optStr(x.video_model);
  if (image_model === undefined || video_model === undefined) return null;
  const c = x.image_credits_ok;
  if (c !== null && c !== undefined && typeof c !== "boolean") return null;
  const s = x.video_seconds;
  if (s !== null && s !== undefined && s !== "plan" && !(typeof s === "number" && Number.isInteger(s))) return null;
  const a = x.aspect;
  if (a !== null && a !== undefined && a !== "plan" && !isAspect(a)) return null;
  const sp = x.speed;
  if (sp !== null && sp !== undefined && !isSpeed(sp)) return null;
  return {
    image_model, video_model,
    image_credits_ok: c ?? null,
    video_seconds: (s as PlanOr<number> | null | undefined) ?? null,
    aspect: (a as PlanOr<Aspect> | null | undefined) ?? null,
    speed: sp ?? null,
  };
}

/** 초 범위 값: 수 또는 null(없음 포함). 그 밖(글자 등)은 모양이 틀린 응답 */
const secOrNull = (v: unknown): v is number | null | undefined => v === null || v === undefined || (typeof v === "number" && Number.isFinite(v));

/** 서버 응답(`data` 안쪽)을 화면 상태로. 모양이 맞지 않으면 null → 칸을 숨긴다 */
export function parseRenderSettings(raw: unknown): RenderSettingsState | null {
  if (!isObj(raw)) return null;
  const defaults = parseValues(raw.defaults);
  if (!defaults) return null;
  let settings: RenderValues | null = null;
  if (raw.settings !== null && raw.settings !== undefined) {
    settings = parseValues(raw.settings);
    if (!settings) return null;
  }
  const m = raw.models;
  if (!isObj(m) || !Array.isArray(m.image) || !Array.isArray(m.video)) return null;
  const image: ImageModelOption[] = [];
  for (const o of m.image) {
    if (!isObj(o) || typeof o.id !== "string" || typeof o.name !== "string") return null;
    image.push({ id: o.id, name: o.name });
  }
  const video: VideoModelOption[] = [];
  for (const o of m.video) {
    if (!isObj(o) || typeof o.id !== "string" || typeof o.name !== "string" || !secOrNull(o.min_s) || !secOrNull(o.max_s) || !Array.isArray(o.aspects)) return null;
    video.push({ id: o.id, name: o.name, min_s: o.min_s ?? null, max_s: o.max_s ?? null, aspects: o.aspects.filter(isAspect) });
  }
  if (typeof raw.can_edit !== "boolean") return null;
  const speedSupported = isObj(raw.defaults) && "speed" in raw.defaults;
  return { settings, defaults, models: { image, video }, can_edit: raw.can_edit, speedSupported };
}

/** 불러오기: 실패(404·네트워크·이상한 응답)는 모두 null = 칸 숨김 */
export async function loadRenderSettings(fetcher: () => Promise<unknown>): Promise<RenderSettingsState | null> {
  try {
    return parseRenderSettings(await fetcher());
  } catch {
    return null;
  }
}

/** 화면에 보이는 값(저장값이 없으면 전부 비움) */
export function startValues(s: RenderSettingsState): RenderValues {
  return s.settings ?? { image_model: null, video_model: null, image_credits_ok: null, video_seconds: null, aspect: null, speed: null };
}

export function videoModelOf(s: RenderSettingsState, id: string | null): VideoModelOption | null {
  return id ? s.models.video.find((m) => m.id === id) ?? null : null;
}

/** 길이 선택지를 만들 때 모르는 쪽 끝(한쪽만 알 때) */
export const SECONDS_FLOOR = 1;
export const SECONDS_CEIL = 60;

/** 고른 영상 모델의 길이 선택지(기획서 따르기는 늘 따로). 모르는 모델이면 비어 있다.
 *  범위를 둘 다 모르면(null) 비어 있다(= "기획서 따르기"만). 한쪽만 알면 아는 쪽으로 자른다(모르는 쪽은 1·60초) */
export function secondsOptions(model: VideoModelOption | null): number[] {
  if (!model || (model.min_s === null && model.max_s === null)) return [];
  const lo = Math.max(SECONDS_FLOOR, Math.ceil(model.min_s ?? SECONDS_FLOOR));
  const hi = model.max_s ?? Math.min(SECONDS_CEIL, lo + SECONDS_CEIL - 1);
  if (hi < lo) return [];
  const out: number[] = [];
  for (let n = lo; n <= hi && out.length < 60; n++) out.push(n);
  return out;
}

/** 모델 목록에 붙일 길이 글: "3~20초", 한쪽만 알면 "~20초"·"3초~", 모르면 "" */
export function rangeLabel(model: Pick<VideoModelOption, "min_s" | "max_s">): string {
  if (model.min_s === null && model.max_s === null) return "";
  return `${model.min_s ?? ""}${model.min_s !== null && model.max_s === null ? "초" : ""}~${model.max_s !== null ? `${model.max_s}초` : ""}`;
}

/** 고른 영상 모델의 비율 선택지. 모르는 모델이거나 서버가 비율을 비워 주면 셋 모두 */
export function aspectOptions(model: VideoModelOption | null): Aspect[] {
  return model && model.aspects.length ? model.aspects : ASPECTS;
}

/** 영상 모델을 바꾸면, 새 모델 범위를 벗어난 길이·비율은 "기획서 따르기"로 되돌린다 */
export function changeVideoModel(s: RenderSettingsState, cur: RenderValues, id: string | null): RenderValues {
  const model = videoModelOf(s, id);
  const next: RenderValues = { ...cur, video_model: id };
  if (typeof cur.video_seconds === "number" && !secondsOptions(model).includes(cur.video_seconds)) next.video_seconds = "plan";
  if (cur.aspect !== null && cur.aspect !== "plan" && !aspectOptions(model).includes(cur.aspect)) next.aspect = "plan";
  return next;
}

/** 저장 본문: 5개 키 모두(+ 서버가 알면 재생 속도). 비워 둔 칸은 null(서버 기본값을 쓴다).
 *  서버가 아직 `speed`를 모르면 넣지 않는다(모르는 칸은 422) */
export function saveBody(v: Omit<RenderValues, "speed"> & { speed?: Speed | null }, speedSupported = false): SaveBody {
  const body: SaveBody = {
    image_model: v.image_model || null,
    video_model: v.video_model || null,
    image_credits_ok: v.image_credits_ok,
    video_seconds: v.video_seconds,
    aspect: v.aspect,
  };
  if (speedSupported) body.speed = v.speed ?? null;
  return body;
}
export type SaveBody = Omit<RenderValues, "speed"> & { speed?: Speed | null };

export function sameValues(a: RenderValues, b: RenderValues): boolean {
  return a.image_model === b.image_model && a.video_model === b.video_model && a.image_credits_ok === b.image_credits_ok
    && a.video_seconds === b.video_seconds && a.aspect === b.aspect && a.speed === b.speed;
}

/** 재생 속도 글: "1.0"은 배속 끔 */
export const speedLabel = (v: Speed | null): string => (v === null ? "" : v === "1.0" ? "1.0 배속 끔" : `${v} 배속`);
export const SPEED_NOTE = "길게 만들고 빠르게 틀어 AI 티를 줄여요. 영상 크레딧은 만든 길이만큼 써요";

export const secondsLabel = (v: PlanOr<number> | null): string => (v === null ? "" : v === "plan" ? PLAN_LABEL : `${v}초`);
export const aspectLabel = (v: PlanOr<Aspect> | null): string => (v === null ? "" : v === "plan" ? PLAN_LABEL : v);
export const creditsLabel = (v: boolean | null): string => (v === null ? "" : v ? "쓰기" : "쓰지 않기");

/** 칸을 비워 뒀을 때 옆에 회색으로 보이는 서버 기본값 */
export function defaultHint(s: RenderSettingsState, key: keyof RenderValues): string {
  const d = s.defaults[key];
  if (d === null || d === undefined) return "";
  switch (key) {
    case "image_model": return s.models.image.find((m) => m.id === d)?.name ?? String(d);
    case "video_model": return s.models.video.find((m) => m.id === d)?.name ?? String(d);
    case "image_credits_ok": return creditsLabel(d as boolean);
    case "video_seconds": return secondsLabel(d as PlanOr<number>);
    case "aspect": return aspectLabel(d as PlanOr<Aspect>);
    case "speed": return speedLabel(d as Speed);
  }
}

/** 저장·되돌리기 실패 문구: 403은 고정 문구, 422 등은 서버 문장 그대로 */
export function saveErrorKo(e: unknown): string {
  const status = isObj(e) ? e.status : undefined;
  if (status === 403) return FORBIDDEN_NOTE;
  return e instanceof Error && e.message ? e.message : "저장하지 못했어요";
}
