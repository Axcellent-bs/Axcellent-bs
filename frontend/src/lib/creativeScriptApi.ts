import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import { saveBody, type CreativeScript, type ScriptSeconds } from "./creativeScript";
import type { ShotCount } from "./storyboard";

// 영상 대본 API (11/23). dashboard-api → 마케팅 `creative_script`·`creative_script_save`(read, 기록의 대본 칸만 바꾼다).
// - POST /marketing/creative-records/{id}/script       AI로 쓰기·다시 쓰기 (줄거리를 비우면 기록의 줄거리)
// - POST /marketing/creative-records/{id}/script/save  사람이 고친 대본 저장
// 대본 읽기는 상세 GET /marketing/creative-records/{id}의 `script`.

const PATH = "/api/dashboard/marketing/creative-records";

async function post(url: string, body: unknown, fallback: string): Promise<CreativeScript> {
  const res = await fetch(`${DASHBOARD_API_URL}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, fallback);
  return (parsed as { data: { script: CreativeScript } }).data.script;
}

export const writeScript = (id: string, body: { story?: string; scenes: ShotCount; seconds: ScriptSeconds }) =>
  post(`${PATH}/${encodeURIComponent(id)}/script`, { ...body, story: body.story?.trim() || undefined }, "대본을 쓰지 못했어요");

export const saveScript = (id: string, script: CreativeScript) =>
  post(`${PATH}/${encodeURIComponent(id)}/script/save`, saveBody(script), "대본을 저장하지 못했어요");
