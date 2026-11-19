// 인사이트 브리프 판(components/data/InsightBrief.tsx)을 어떻게 만들었는지 글자. node --test로 시험한다 (dataAfterMigration.test.ts).
// 서버 판 이름(label)은 "00:27 자동" · "11:16 다시 추천" 모양이다. auto_retry(자동으로 다시 만든 판)는 화면에서 "자동(다시)"로 보인다
import type { BriefTrigger } from "./data.ts";

export const BRIEF_TRIGGER_LABEL: Record<BriefTrigger, string> = { auto: "자동", manual: "다시 추천", auto_retry: "자동(다시)" };

/** trigger 글자. 모르는 값은 그대로, 없으면 null */
export function briefTriggerLabel(t: BriefTrigger | string | null | undefined): string | null {
  if (!t) return null;
  return BRIEF_TRIGGER_LABEL[t as BriefTrigger] ?? t;
}

/** 판 목록 한 줄: auto_retry면 서버 이름의 시각("00:27")만 살리고 "자동(다시)", 그 밖은 서버 이름 그대로 */
export function versionLabel(v: { label: string; trigger: BriefTrigger | string }): string {
  if (v.trigger !== "auto_retry") return v.label;
  const time = /^\d{1,2}:\d{2}/.exec(v.label.trim())?.[0];
  return time ? `${time} ${BRIEF_TRIGGER_LABEL.auto_retry}` : `${v.label} · ${BRIEF_TRIGGER_LABEL.auto_retry}`;
}
