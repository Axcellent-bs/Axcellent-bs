// 제작 기록 영상 기술 검사(render.qc, 11/26) 표시 계산. 화면: components/marketing/VideoQc.tsx. node --test (videoQcView.test.ts).
// 검사·판정은 서버(marketing creative_qc) 값 그대로. 다시 만들기는 지금 있는 영상 요청 길(regenerate)만 쓴다

export type QcCheckId = "black" | "freeze" | "duration";
export type QcStatus = "pass" | "warn" | "error";
export interface QcCheck { id: QcCheckId; status: QcStatus; detail_ko: string; ranges?: number[][] }
/** 기록 render.qc (계약 CreativeVideoQc) */
export interface VideoQcState { video_sha?: string | null; part?: string | null; checks?: QcCheck[]; at?: string | null }

export const QC_LABEL: Record<QcCheckId, string> = { black: "검은 화면", freeze: "멈춘 화면", duration: "길이" };
export const QC_RESULT: Record<QcStatus, { label: string; tone: "ok" | "bad" | "muted" }> = {
  pass: { label: "통과", tone: "ok" },
  warn: { label: "확인 필요", tone: "bad" },
  error: { label: "확인 못 함", tone: "muted" },
};
export const QC_NOTE = "자막·소리는 우리 후처리가 얹어서 늘 들어가므로 검사하지 않아요. 의뢰 대사는 영상 전에 기획·대본에서 확인해요.";
export const QC_NO_AUTO = "통과 못 해도 자동으로 다시 만들지 않아요.";

export interface QcView {
  chip: { label: string; tone: "ok" | "bad" | "muted" };
  rows: { id: QcCheckId; label: string; result: string; tone: "ok" | "bad" | "muted"; detail: string }[];
  /** 확인 필요(warn)가 하나라도 있으면 [영상 다시 만들기]를 보인다 */
  needsAction: boolean;
}

/** 칩: 확인 필요 N개 > 모두 통과 > 일부 확인 못 함. 검사 결과가 없으면 null */
export function qcView(qc: VideoQcState | null | undefined): QcView | null {
  const checks = (qc?.checks ?? []).filter((c) => c && c.id in QC_LABEL);
  if (!checks.length) return null;
  const warn = checks.filter((c) => c.status === "warn").length;
  const error = checks.filter((c) => c.status === "error").length;
  const chip = warn > 0 ? { label: `${warn}개 확인 필요`, tone: "bad" as const }
    : error > 0 ? { label: `${error}개 확인 못 함`, tone: "muted" as const }
      : { label: "모두 통과", tone: "ok" as const };
  return {
    chip,
    rows: checks.map((c) => ({ id: c.id, label: QC_LABEL[c.id], result: QC_RESULT[c.status]?.label ?? c.status, tone: QC_RESULT[c.status]?.tone ?? "muted", detail: c.detail_ko })),
    needsAction: warn > 0,
  };
}

/** "영상 다시 만들기 · 사람 결정 · 약 27 크레딧" (예상 크레딧을 모르면 크레딧 칸을 뺀다) */
export function regenerateLabel(credits: number | null | undefined): string {
  const base = "영상 다시 만들기 · 사람 결정";
  return typeof credits === "number" && Number.isFinite(credits) ? `${base} · 약 ${Math.round(credits * 10) / 10} 크레딧` : base;
}

/** 영상 요청 본문(creativeRecords.requestCreativeVideo): 보통은 "{}", 기술 검사 [영상 다시 만들기]만 regenerate=true */
export const videoRequestBody = (opts?: { regenerate?: boolean }) => (opts?.regenerate ? JSON.stringify({ regenerate: true }) : "{}");

/** 다 만든 영상이고 확인 필요가 있을 때만 다시 만들기 버튼 (서버도 같은 조건으로만 받는다) */
export function canRegenerate(videoStatus: string | null | undefined, qc: VideoQcState | null | undefined): boolean {
  return videoStatus === "done" && !!qcView(qc)?.needsAction;
}
