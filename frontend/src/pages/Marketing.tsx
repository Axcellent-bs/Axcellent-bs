import { Navigate, useLocation } from "react-router";
import LiveMarketing from "@/components/marketing/LiveMarketing";
import { LinkedAi, type MarketingTab } from "@/components/marketing/LinkedAi";
import { ReceivedBriefs } from "@/components/marketing/ReceivedBriefs";
import { SnsMedia } from "@/components/marketing/SnsMedia";

// 마케팅 AI 화면. 캣북스 쇼핑몰 배너와 안전 장치 기록을 실제 백엔드에서 읽어 그린다 (LiveMarketing).
// 공지는 확인된 사실 필드만 보여 주고 장애 원인은 적지 않는다.
// 사이드바 하위 메뉴 4개가 화면을 가른다(demo/services.ts coupon 서비스 children, pages/Data.tsx와 같은 짜임):
// - /marketing 장애·공지 · /marketing/campaign 캠페인·쿠폰 · /marketing/briefs 받은 기획안 · /marketing/sns SNS·미디어
// 옛 주소 /marketing 은 장애·공지 탭으로 계속 열린다.

const TAB_PATH: Record<MarketingTab, string> = { notice: "/marketing", campaign: "/marketing/campaign", briefs: "/marketing/briefs", sns: "/marketing/sns" };

export default function MarketingPage() {
  const { pathname } = useLocation();
  const path = pathname.replace(/\/+$/, "") || "/";
  const tab = (Object.keys(TAB_PATH) as MarketingTab[]).find((t) => TAB_PATH[t] === path);
  if (!tab) return <Navigate to={TAB_PATH.notice} replace />;

  return (
    <>
      <div className="w-full px-8 pt-2"><LinkedAi tab={tab} /></div>
      {tab === "notice" && <LiveMarketing view="notice" />}
      {tab === "campaign" && <LiveMarketing view="campaign" />}
      {tab === "briefs" && <div className="w-full space-y-8 px-8 pb-12 pt-6"><ReceivedBriefs /></div>}
      {tab === "sns" && <div className="w-full px-8 pb-12 pt-6"><SnsMedia /></div>}
    </>
  );
}
