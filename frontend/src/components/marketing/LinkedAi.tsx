// 마케팅 화면 탭 위의 "이 화면에서 연계되는 AI" 카드. 누가 시키고 누구에게 시키는지 한 줄씩만 적는다.
// 근거는 backend/domains/marketing 의 skill 이름과 호출 코드에서 확인된 것만 쓴다(여기서 새로 정하지 않는다).

export type MarketingTab = "notice" | "campaign" | "briefs" | "sns";

interface Link { who: string; dir: "받음" | "시킴"; line: string }

const LINKS: Record<MarketingTab, Link[]> = {
  notice: [
    { who: "총괄 AI", dir: "받음", line: "장애가 나면 공지 초안(draft_notice)·게시(publish_notice)를, 정상화되면 공지 내림(end_incident_notice)을 시킵니다." },
    { who: "안전 장치", dir: "시킴", line: "공지 게시 전에 execute로 등급 판정을 요청합니다." },
    { who: "쇼핑몰(shop-api)", dir: "시킴", line: "안전 장치를 통과한 공지를 쇼핑몰 배너로 올리고 내립니다." },
  ],
  campaign: [
    { who: "고객지원 AI", dir: "받음", line: "장애 보상 쿠폰 발급(issue_coupon)을 시킵니다." },
    { who: "이 화면", dir: "받음", line: "사람이 캠페인 시작·결과 기록·취소(start_campaign·record_campaign·cancel_campaign)를 시킵니다." },
    { who: "재무 AI", dir: "시킴", line: "캠페인 예산에 의견을 받습니다(review_campaign_budget). 의견은 참고용이고 시작을 막지 않습니다." },
    { who: "데이터 AI", dir: "시킴", line: "캠페인 성과(campaign_result)를 받아 결과 기록에 씁니다." },
    { who: "안전 장치", dir: "시킴", line: "쿠폰 발급과 캠페인 집행 전에 execute로 등급 판정을 요청합니다." },
    { who: "쇼핑몰(shop-api)", dir: "시킴", line: "안전 장치를 통과한 쿠폰을 쇼핑몰에 만듭니다." },
  ],
  briefs: [
    { who: "기획 AI", dir: "받음", line: "사람이 확정한 캠페인 기획안을 넘겨 줍니다(receive_campaign_brief)." },
  ],
  sns: [
    { who: "안전 장치", dir: "시킴", line: "이미지·영상 생성(generate_media)과 SNS 게시(publish_sns)를 execute로 요청합니다. 게시는 항상 사람 승인을 거칩니다." },
  ],
};

export function LinkedAi({ tab }: { tab: MarketingTab }) {
  return (
    <aside className="card space-y-2 p-4" aria-label="이 화면에서 연계되는 AI">
      <h2 className="text-sm font-semibold">이 화면에서 연계되는 AI</h2>
      <ul className="space-y-1 text-xs">
        {LINKS[tab].map((l) => (
          <li key={l.who + l.dir} className="flex flex-wrap items-baseline gap-x-2">
            <span className={`chip ${l.dir === "받음" ? "bg-brand-50 text-brand-700" : "bg-ink-50 text-ink-700"}`}>{l.who} · {l.dir === "받음" ? "받는 일" : "시키는 일"}</span>
            <span className="text-ink-700">{l.line}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}
