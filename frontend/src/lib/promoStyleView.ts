// 마케팅 › 브랜드(/marketing/brand): 홍보 영상 방향 두 줄 (11/24 "재미 먼저, 브랜드는 끝에"). 순수 계산만 한다:
// `npm test`(node --test)에서 그대로 돈다. API는 promoStyle.ts.
// 회사별로 저장하고 data 트렌드 보고서가 같은 값을 읽는다. 의뢰 문장이 "책 소개 영상"처럼 제품을
// 직접 말하면 그 의뢰만 제품·업종 소개로 본다(서버 판정).
export type PromoStyle = "fun_first" | "product";
export type BrandExposure = "end_card" | "prop" | "caption";
export type PromoSource = "screen" | "tenant" | "default";

// 영상 기본 모습 칸 (캐릭터·배경·그림체). 서버가 아직 안 보내면 없다 → 그룹을 숨긴다.
export const LOOK_KEYS = ["character_ko", "character_en", "setting_ko", "setting_en", "style_en"] as const;
export type LookKey = (typeof LOOK_KEYS)[number];
export type LookDraft = Record<LookKey, string>;

export interface PromoState {
  promo_style: PromoStyle;
  brand_exposure: BrandExposure;
  // 칸별 출처는 기존 source와 같은 모양으로 새 키만 더 온다고 가정 (모두 선택)
  source: { promo_style: PromoSource; brand_exposure: PromoSource } & Partial<Record<LookKey, PromoSource>>;
  defaults: { promo_style: PromoStyle; brand_exposure: BrandExposure };
  editable: boolean;
  character_ko?: string;
  character_en?: string;
  setting_ko?: string;
  setting_en?: string;
  style_en?: string;
}

export const LOOK_MAX = 200;

export interface PromoPick {
  promo_style: PromoStyle;
  brand_exposure: BrandExposure;
}

// 시안 문구 그대로
export const STYLE_ROW = {
  label: "홍보 영상 방향",
  hint: "회사마다 고르고, 의뢰 문장에 \"제품 소개 영상\"처럼 쓰면 그 의뢰만 바뀌어요",
  options: [
    { value: "fun_first", label: "재미 먼저 (브랜드는 끝에)" },
    { value: "product", label: "제품·업종 소개" },
  ] as { value: PromoStyle; label: string }[],
};
export const EXPOSURE_ROW = {
  label: "브랜드 노출",
  hint: "재미 먼저일 때",
  options: [
    { value: "end_card", label: "마지막 2~3초 채널 이름·로고" },
    { value: "prop", label: "화면 속 소품으로 살짝" },
    { value: "caption", label: "캡션·프로필에만" },
  ] as { value: BrandExposure; label: string }[],
};

/** 지금 값이 어디서 왔는지 한 줄 (두 칸 중 화면 저장이 하나라도 있으면 화면 저장) */
export function sourceLine(s: Pick<PromoState, "source">): string {
  const all = [s.source.promo_style, s.source.brand_exposure];
  if (all.includes("screen")) return "이 화면에서 저장한 값이에요";
  if (all.includes("tenant")) return "회사 설정 파일 값이에요";
  return "기본값이에요 (재미 먼저 · 마지막에 채널 이름·로고)";
}

/** 화면 저장 값이 있나 ([회사 설정 파일 값으로 되돌리기]를 보이나) */
export const hasScreenValue = (s: Pick<PromoState, "source">) =>
  s.source.promo_style === "screen" || s.source.brand_exposure === "screen" ||
  LOOK_KEYS.some((k) => s.source[k] === "screen");

/** 영상 기본 모습 그룹을 보이나: 서버가 새 칸을 하나라도 보냈을 때만 */
export const lookShown = (s: Partial<Record<LookKey, string>>) => LOOK_KEYS.some((k) => typeof s[k] === "string");

export const LOOK_ROWS: { key: LookKey; label: string; hint: string }[] = [
  { key: "character_ko", label: "기본 캐릭터 (한국어)", hint: "비우면 지금 값을 써요" },
  { key: "character_en", label: "기본 캐릭터 (영어)", hint: "영상 만들 때 쓰는 영어 문장이에요" },
  { key: "setting_ko", label: "기본 배경 (한국어)", hint: "비우면 지금 값을 써요" },
  { key: "setting_en", label: "기본 배경 (영어)", hint: "영상 만들 때 쓰는 영어 문장이에요" },
  { key: "style_en", label: "그림체", hint: "영어로 적어요" },
];
export const LOOK_NOTICE = "의뢰에서 캐릭터를 고르면 그 캐릭터가 먼저예요. 여기는 고르지 않았을 때 기본값이에요.";

const SOURCE_LABEL: Record<PromoSource, string> = { screen: "화면 저장", tenant: "회사 설정", default: "기본값" };
export const sourceLabel = (src: PromoSource | undefined) => (src ? SOURCE_LABEL[src] : "");

/** 입력칸 처음 값: 기본값 출처면 비워 두고(자리 표시에 지금 값이 보인다), 아니면 지금 값 */
export function lookDraft(s: PromoState): LookDraft {
  const d = {} as LookDraft;
  for (const k of LOOK_KEYS) d[k] = s.source[k] === "default" ? "" : (s[k] ?? "");
  return d;
}

/** 입력이 처음 값과 다른가 (그룹이 안 보이면 항상 아니다) */
export function lookDirty(s: PromoState, draft: LookDraft): boolean {
  if (!lookShown(s)) return false;
  const base = lookDraft(s);
  return LOOK_KEYS.some((k) => draft[k] !== base[k]);
}

/** 200자를 넘긴 칸이 있으면 안내 문장 (없으면 null) */
export function lookError(draft: LookDraft): string | null {
  const over = LOOK_ROWS.find((r) => draft[r.key].length > LOOK_MAX);
  return over ? `${over.label}은(는) ${LOOK_MAX}자까지 쓸 수 있어요 (지금 ${draft[over.key].length}자)` : null;
}

/** 서버가 막은 이유(422 등)는 서버 문장 그대로 보여 준다 */
export const errText = (x: unknown) => (x instanceof Error && x.message ? x.message : "처리하지 못했어요");

/** 저장 요청 본문: 새 칸이 없는 서버면 지금과 똑같이 두 칸만 보낸다 */
export function saveBody(s: PromoState, pick: PromoPick, draft: LookDraft): Record<string, string> {
  const body: Record<string, string> = { promo_style: pick.promo_style, brand_exposure: pick.brand_exposure };
  if (lookShown(s)) for (const k of LOOK_KEYS) body[k] = draft[k];
  return body;
}

/** [저장]을 켤 수 있나: 고친 값이 지금 값과 다르고, 저장할 수 있는 서버이고, 저장 중이 아니다 */
export function canSave(s: Pick<PromoState, "promo_style" | "brand_exposure" | "editable">, pick: PromoPick, busy = false, lookChanged = false): boolean {
  return s.editable && !busy && (pick.promo_style !== s.promo_style || pick.brand_exposure !== s.brand_exposure || lookChanged);
}

/** 브랜드 노출 줄은 재미 먼저일 때만 고른다 (제품·업종 소개면 흐리게) */
export const exposureActive = (style: PromoStyle) => style === "fun_first";
