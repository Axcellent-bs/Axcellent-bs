import LiveMarketing from "@/components/marketing/LiveMarketing";

// 마케팅 AI 화면. 캣북스 쇼핑몰 배너와 안전 장치 기록을 실제 백엔드에서 읽어 그린다 (LiveMarketing).
// 공지는 확인된 사실 필드만 보여 주고 장애 원인은 적지 않는다.

export default function MarketingPage() {
  return <LiveMarketing />;
}
