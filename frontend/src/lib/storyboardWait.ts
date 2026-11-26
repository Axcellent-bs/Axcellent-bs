// 기록에 붙일 스토리보드를 뒤에서 만들고(background) 붙을 때까지 기다리기 (11/26). node --test (storyboardWait.test.ts).
// 기다려서 받기(makeStoryboard)는 마케팅 LLM 60초·nginx 60초에 걸려 대본 뒤 12칸이면 끊겼다(스토리보드가 안 붙음).
// 11/25 제작 기록 [대본대로 스토리보드 다시 만들기]만 background로 고쳤던 것을 SNS·미디어 시트 화면도 같이 쓴다.
// - startStoryboard(background: true)는 바로 답한다. 그 뒤 POLL_MS마다 기록을 다시 읽어 boardArrived면 그 기록을 돌려준다
// - 서버가 job 실패를 알려 주면 이유 문장으로 멈춘다. POLL_LIMIT_MS(5분)를 넘으면 새로고침 안내
// 부르는 길(io)은 화면이 넘긴다(이 파일은 env를 읽지 않아 시험에서 가짜로 돈다)

import { boardArrived } from "./creativeScript.ts";

export const POLL_MS = 5000;
export const POLL_LIMIT_MS = 5 * 60 * 1000;
/** 서버 시계와 조금 어긋나도 놓치지 않게 시작 시각을 이만큼 앞당긴다 */
const CLOCK_SLACK_MS = 2000;

export const BOARD_TIMEOUT_KO = "5분 안에 스토리보드가 붙지 않았어요. 잠시 뒤 새로고침해 보세요";
/** 버튼 글자와 페이지를 떠나도 된다는 한 줄 */
export const BOARD_BUSY_KO = "스토리보드 만드는 중… (1~3분)";
export const BOARD_LEAVE_KO = "페이지를 떠나도 다 만들면 제작 기록에 붙어요.";

type BoardRecord = { storyboard?: { script_used?: boolean } | null; storyboard_at?: string | null };
type Job = { status: string; code?: string; message?: string };

export interface BoardIo<R extends BoardRecord, B> {
  start: (body: B) => Promise<void>;
  getRecord: (recordId: string) => Promise<R>;
  job: (recordId: string) => Promise<Job>;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

/** 실패 job → 화면 문장 */
export const jobFailureKo = (job: Job): string =>
  `스토리보드를 만들지 못했어요${job.message ? ` — ${job.message}` : ""}${job.code ? ` (${job.code})` : ""}`;

/** 뒤에서 만들기를 시작하고, 새 스토리보드가 붙은 기록을 돌려준다. 실패·시간 초과면 이유 문장으로 throw */
export async function startBoardAndWait<R extends BoardRecord, B extends { record_id: string }>(body: B, io: BoardIo<R, B>): Promise<R> {
  const now = io.now ?? Date.now;
  const sleep = io.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const startedAt = now() - CLOCK_SLACK_MS;
  await io.start(body);
  while (now() - startedAt < POLL_LIMIT_MS) {
    await sleep(POLL_MS);
    const rec = await io.getRecord(body.record_id).catch(() => null);
    if (rec && boardArrived(rec, startedAt)) return rec;
    // 서버가 실패를 알려 주면 5분을 다 기다리지 않고 이유를 보여 준다
    const job = await io.job(body.record_id).catch(() => null);
    if (job?.status === "failed") throw new Error(jobFailureKo(job));
  }
  throw new Error(BOARD_TIMEOUT_KO);
}
