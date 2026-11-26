// 스토리보드 뒤에서 만들기(storyboardWait.ts)가 부르는 실제 길 (11/26). 제작 기록 상세·SNS·미디어 시트 화면이 같이 쓴다
import { getCreativeRecord, type CreativeRecord } from "./creativeRecords";
import { startStoryboard, storyboardJob, type ShotCount } from "./storyboard";
import type { BoardIo } from "./storyboardWait";

export type BoardBody = { story: string; character_en?: string; shots: ShotCount; record_id: string };

export const RECORD_BOARD_IO: BoardIo<CreativeRecord, BoardBody> = {
  start: startStoryboard,
  getRecord: getCreativeRecord,
  job: storyboardJob,
};
