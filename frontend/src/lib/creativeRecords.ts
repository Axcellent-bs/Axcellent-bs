import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import type { CharacterSheetResult } from "./characterSheet";
import type { StoryboardResult } from "./storyboard";

// 마케팅 › 제작 기록. 이미지 시트를 만들 때마다 남은 기록과 이어 만든 스토리보드를 읽는다.
// dashboard-api GET /marketing/creative-records(목록) · /marketing/creative-records/{id}(상세)
// → 마케팅 `creative_records_list`·`creative_record_get`(read). 원본 이미지는 저장되지 않고 작은 미리보기만 있다.

export interface CreativeRecordSummary {
  record_id: string;
  created_at: string;
  summary_ko: string;
  note?: string | null;
  story?: string | null;
  thumbnail_base64?: string | null;
  storyboard_title_ko?: string | null;
  shot_count: number;
  storyboard_at?: string | null;
}

export interface CreativeRecord {
  record_id: string;
  created_at: string;
  note?: string | null;
  story?: string | null;
  shots?: number | null;
  thumbnail_base64?: string | null;
  sheet: CharacterSheetResult;
  storyboard?: StoryboardResult | null;
  storyboard_at?: string | null;
}

const PATH = "/api/dashboard/marketing/creative-records";
export const RECORD_ID = /^CR-[0-9a-f]{12}$/;
export const thumbSrc = (b64?: string | null) => (b64 ? `data:image/jpeg;base64,${b64}` : null);

async function get<T>(url: string, fallback: string): Promise<T> {
  const res = await fetch(`${DASHBOARD_API_URL}${url}`, { headers: { ...dashboardAuth() }, cache: "no-store" });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, fallback);
  return (parsed as { data: T }).data;
}

export const listCreativeRecords = (limit = 50) =>
  get<{ items: CreativeRecordSummary[] }>(`${PATH}?limit=${limit}`, "제작 기록을 읽지 못했어요").then((d) => d.items);

export const getCreativeRecord = (id: string) => get<CreativeRecord>(`${PATH}/${encodeURIComponent(id)}`, "제작 기록을 읽지 못했어요");
