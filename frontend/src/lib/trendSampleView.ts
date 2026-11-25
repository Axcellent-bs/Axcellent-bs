// 보고서 맨 위 인스타 경고 줄 · 표본 칸의 순수 계산 (components/data/TrendSample.tsx). node --test (trendSampleView.test.ts).
// 숫자는 서버 값 그대로 (calls·sample). 예전 저장본(sample 없음)은 호출 수만으로 경고한다
import type { TrendReport } from "./data.ts";

export interface IgWarning { title: string; body: string }

/** 인스타 호출 0번이면 경고. 연결 안 된 회사면 문구만 다르게. 오늘 저장분을 다시 써서 0번이면(읽은 계정 있음) 경고 없음 */
export function igWarning(r: Pick<TrendReport, "calls" | "sample">): IgWarning | null {
  if (r.calls.instagram > 0) return null;
  if ((r.sample?.instagram_read ?? 0) > 0) return null;
  const naver = `네이버 ${r.calls.naver}번`;
  if (r.sample?.instagram_connected === false) {
    return { title: `인스타 연결 안 됨 · ${naver}`, body: "이 회사는 인스타가 연결되어 있지 않아 인스타 근거 없이 만들었어요. 인스타를 연결하고 다시 만들면 형식 신호가 들어가요." };
  }
  return { title: `인스타 호출 0번 · ${naver}`, body: "이 보고서는 인스타 근거 없이 만들어졌어요. 인스타 연결을 확인하고 다시 만들면 형식 신호가 들어가요." };
}

export interface SampleChip { text: string; tone: "plain" | "brand" | "warn" }
export interface SampleView { chips: SampleChip[]; notes: string[] }

/** "전체 N곳 · 대상 맞음 M곳 · 사람 확인 K곳" 칩 + 한 줄들. sample이 없으면(예전 저장본) null */
export function sampleView(r: Pick<TrendReport, "sample" | "sections">): SampleView | null {
  const s = r.sample;
  if (!s) return null;
  const chips: SampleChip[] = [
    { text: `전체 ${s.total}곳`, tone: "plain" },
    { text: `대상 맞음 ${s.matched}곳`, tone: "brand" },
    { text: s.human_checked > 0 ? `사람 확인 ${s.human_checked}곳` : "사람 확인 0곳 · 모두 AI 추정", tone: s.human_checked > 0 ? "plain" : "warn" },
  ];
  const notes: string[] = [`한국어 계정 ${s.korean}곳 (최근 캡션 한글 비율로 셈)`];
  if (s.korean_first) notes.push("대상층이 한국이라 한국어 계정 근거를 순위 앞으로 옮겼어요 (빼지는 않아요)");
  const posts = r.sections.content.posts ?? [];
  const ads = posts.filter((p) => p.notice_ad).length;
  if (ads > 0) notes.push(`"갑자기 뜬 게시물" ${posts.length}개 중 공지·광고 ${ads}개는 근거에서 뺐어요`);
  return { chips, notes };
}
