import { Link } from "react-router";
import type { ReactNode } from "react";

// 로그인 없이 여는 개인정보처리방침·서비스 약관(11/23). 구글 OAuth 앱(유튜브 연결) 게시에 필요한 공개 주소다.
// 내용은 지금 코드가 실제로 하는 일만 적는다(유튜브: 채널 이름 읽기, 사람이 승인한 영상 올리기, 연결 값은 회사 키 저장소에 암호화).
// 법률 검토 전 초안이다. 바꿀 때는 실제 동작과 맞는지 먼저 본다.

function Page({ title, updated, children }: { title: string; updated: string; children: ReactNode }) {
  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-10 text-sm leading-relaxed text-ink-800">
      <Link to="/" className="text-xs text-brand-700 underline">AXcellent 홈</Link>
      <header>
        <h1 className="text-2xl font-bold tracking-tight text-ink-900">{title}</h1>
        <p className="mt-1 text-xs text-ink-500">최종 수정 {updated}</p>
      </header>
      {children}
    </main>
  );
}

const H = ({ children }: { children: ReactNode }) => <h2 className="pt-2 text-base font-semibold text-ink-900">{children}</h2>;

export function PrivacyPage() {
  return (
    <Page title="개인정보처리방침" updated="2026-11-23">
      <p>AXcellent(이하 "서비스")는 고객사가 자기 서버에 설치해 쓰는 AI 업무 도구입니다. 이 방침은 서비스가 다루는 정보와 Google(유튜브) 연결로 받는 정보를 설명합니다.</p>
      <H>1. 다루는 정보</H>
      <ul className="list-disc space-y-1 pl-5">
        <li>계정 정보: 고객사 직원의 이름·이메일·역할(로그인과 권한 확인용)</li>
        <li>고객사 업무 데이터: 고객사가 연결한 사이트·서비스의 주문·문의 등. 고객사 서버에 저장되고 고객사가 관리합니다</li>
        <li>마케팅 제작물: 사람이 올린 이미지·줄거리, AI가 만든 이미지 시트·스토리보드·영상</li>
      </ul>
      <H>2. Google 사용자 데이터(유튜브)</H>
      <p>고객사 관리자가 [유튜브 채널 연결]을 누르고 동의하면 서비스는 아래 범위만 씁니다.</p>
      <ul className="list-disc space-y-1 pl-5">
        <li><code>youtube.readonly</code>: 연결된 채널 이름을 읽어 화면에 "연결됨"을 보여 줍니다</li>
        <li><code>youtube.upload</code>: 사람이 승인한 영상만, 사람이 확인한 제목·설명·공개 범위로 그 회사 채널에 올립니다</li>
      </ul>
      <p>받은 연결 값(refresh token)은 그 고객사의 키 저장소에 암호화해 저장하며, 화면·로그·응답에 내보내지 않습니다. 채널의 다른 영상·댓글·구독자 정보는 읽지 않습니다. Google 사용자 데이터를 광고, 판매, 다른 회사와의 공유, AI 모델 학습에 쓰지 않습니다. 서비스의 Google API 데이터 사용은 <a className="text-brand-700 underline" href="https://developers.google.com/terms/api-services-user-data-policy" target="_blank" rel="noreferrer">Google API 서비스 사용자 데이터 정책</a>(제한적 사용 요건 포함)을 따릅니다.</p>
      <H>3. 보관과 삭제</H>
      <p>관리자가 [연결 끊기]를 누르면 저장된 연결 값을 지웁니다. Google 계정의 "타사 앱 및 서비스"에서 AXcellent 접근을 직접 해제할 수도 있습니다. 그 밖의 데이터는 고객사 서버에 있으며 고객사의 요청이나 계약 종료 시 지웁니다.</p>
      <H>4. 문의</H>
      <p>개인정보 관련 문의는 앱 동의 화면에 표시된 지원 이메일로 보내 주세요.</p>
    </Page>
  );
}

export function TermsPage() {
  return (
    <Page title="서비스 약관" updated="2026-11-23">
      <p>이 약관은 AXcellent 서비스 이용에 적용됩니다.</p>
      <H>1. 서비스</H>
      <p>AXcellent는 고객사 업무(고객 문의, 재무, 마케팅 등)를 돕는 AI 도구입니다. 돈이 오가거나 외부에 게시되는 일(예: 유튜브·인스타그램 게시)은 사람의 승인을 거친 뒤에만 실행합니다.</p>
      <H>2. 이용자의 책임</H>
      <ul className="list-disc space-y-1 pl-5">
        <li>올리는 이미지·영상·글에 대한 권리를 이용자가 가지고 있어야 합니다</li>
        <li>연결한 외부 서비스(유튜브 등)의 약관과 정책을 지켜야 합니다. 유튜브에 올릴 때는 <a className="text-brand-700 underline" href="https://www.youtube.com/t/terms" target="_blank" rel="noreferrer">YouTube 서비스 약관</a>이 함께 적용됩니다</li>
        <li>AI가 만든 결과물은 게시 전에 사람이 확인해야 합니다</li>
      </ul>
      <H>3. AI 생성물 표시</H>
      <p>서비스로 만든 영상은 AI 생성물임을 설명과 플랫폼 표시(합성 미디어)에 남깁니다.</p>
      <H>4. 변경</H>
      <p>약관이 바뀌면 이 페이지에 알립니다.</p>
      <p className="pt-4 text-xs text-ink-500"><Link to="/privacy" className="underline">개인정보처리방침</Link></p>
    </Page>
  );
}
