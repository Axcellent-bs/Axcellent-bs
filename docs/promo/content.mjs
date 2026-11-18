// AXcellent 홍보 웹페이지의 모든 문구·숫자·링크가 여기 있다 (브로셔 PDF 기준, 2026-11-18).
// 고치는 법: 이 파일을 고치고 `node build.mjs`를 돌리면 index.html이 다시 만들어진다. 자세한 규칙은 README.md, 준하 님 자료 받는 자리는 INTAKE.md.
// 규칙: 숫자·문구는 브로셔와 같게 쓴다. 시연 숫자에는 "시연 환경 기준"을 붙이고, 측정하지 않은 효과(비용 절감·인력 대체)는 쓰지 않는다.

import instagram from "./instagram/load.mjs";

export default {
  site: {
    lang: "ko",
    // 공개하기로 정하기 전에는 검색에 나오지 않게 한다. 공개 때 false로 바꾼다
    noindex: true,
    title: "AXcellent — AI 직원들이 서로 알아서 일합니다",
    description:
      "AI 직원들이 서로 직접 요청을 주고받아 대표님이 전달하지 않아도 일이 이어지고, 돈과 외부 게시는 대표님이 승인합니다. (시연 환경 기준)",
    disclaimer:
      "이 페이지의 화면·숫자·고객사는 시연 환경(가상 데이터) 기준이며 실제 고객 성과가 아닙니다.",
    photoCredit: "사진: Unsplash",
  },

  // 비워 두면 그 버튼은 화면에 나오지 않는다 (확정되기 전에 잘못된 링크가 나가지 않게)
  links: {
    // 브로셔 PDF 받기. 예: "assets/AXcellent_홍보브로셔.pdf" (PDF 검수가 끝나고 공개하기로 정한 뒤에 채운다)
    brochureUrl: "",
    brochureLabel: "브로셔 PDF 받기",
    // 상담 문의. 예: Instagram DM 주소 또는 mailto 주소. 연락 수단이 정해지면 채운다
    contactUrl: "",
    contactLabel: "상담 문의",
    // 문의 안내 문장(링크 없이 글로만 보일 때). 비우면 안 보인다
    contactNote: "",
  },

  nav: [
    ["vision", "지향점"],
    ["team", "AI 직원"],
    ["story", "시연"],
    ["a2a", "A2A 자동화"],
    ["cases", "마케팅 사례"],
    ["approval", "승인"],
    ["customers", "고객사"],
    ["posts", "콘텐츠"],
  ],

  hero: {
    badge: "A2A 자동화 · Agent to Agent Automation",
    // 줄마다 조각 목록. em은 강조색
    titleLines: [[{ text: "AI 직원들이" }], [{ text: "서로 " }, { text: "알아서", em: true }], [{ text: "일합니다" }]],
    lead: ["대표님이 전달하지 않아도 일이 이어지고", "돈과 외부 게시는 대표님이 승인합니다."],
    leadEm: "대표님이 승인",
  },

  vision: {
    kicker: "지향점",
    title: ["부서마다 AI 직원이 붙어", "서로 부탁하며 일합니다"],
    points: [
      { n: "01", title: "에이전트끼리 직접 요청을 주고받습니다", body: "부서마다 AI 직원이 붙어 필요한 일을 서로에게 직접 부탁합니다. 이것이 A2A(Agent to Agent)입니다." },
      { n: "02", title: "사람이 중간에서 전달하지 않아도 일이 끝납니다", body: "감지, 분석, 조치, 기록까지 AI 직원들이 이어서 처리합니다." },
      { n: "03", title: "대표님은 결정이 필요한 순간에만 승인합니다", body: "기준을 넘는 돈과 외부 게시는 사람이 결정합니다." },
    ],
    note: "자동화는 시연 환경 기준으로 확인한 동작입니다.",
  },

  team: {
    kicker: "AI 직원 9명",
    title: ["대표님 곁에서", "일을 나누는 ", "총괄 AI"],
    lead: "여러 부서에 걸친 일을 한 번에 묶어 조율하고 사람이 결정해야 하는 일은 대표님께 올립니다.",
    diagramTitle: ["AI 직원 9명을", "총괄 AI가 잇습니다"],
    hub: "총괄 AI",
    members: [
      { name: "인프라", role: "원인 진단·복구" },
      { name: "고객지원", role: "문의·환불·정산" },
      { name: "마케팅", role: "공지·쿠폰·SNS" },
      { name: "데이터분석", role: "숫자로 영향 파악" },
      { name: "재무", role: "결산·정산·장부" },
      { name: "아카이브", role: "기록·비슷한 과거" },
      { name: "인사", role: "조직 변동·평가" },
      { name: "기획", role: "요청→기획서" },
      { name: "개발", role: "코드·검토·배포" },
    ],
    facts: [
      ["9명", "부서마다 맡은 일이 정해진 AI 직원이 붙습니다."],
      ["A2A", "AI 직원끼리는 서로 직접 요청을 주고받습니다."],
      ["안전", "모든 AI의 돈·게시·배포 요청은 안전 처리 로그에 남습니다."],
    ],
  },

  // 시연 장면. 준하 님 자료(영상·GIF·화면)는 media에 넣으면 이 섹션에 나온다 (INTAKE.md)
  story: {
    kicker: "시연 상황 (연출)",
    title: ["퇴근한 밤", "결제가 멈췄습니다"],
    lead: "직원은 퇴근했고 대표님은 아직 모릅니다.",
    turn: ["그런데 대표님이 깨기 전에", "AI 직원들이 먼저 움직였습니다."],
    stats: [
      { value: "36", unit: "초", label: "감지부터 기록까지" },
      { value: "17", unit: "건", label: "AI끼리 주고받은 요청" },
    ],
    statsNote: "시연 환경(가상 데이터)에서 결제 장애를 일으켜 잰 값이며 실제 고객 성과가 아닙니다.",
    statsSource: "출처: 시연 이슈 기록(시스템이 자동으로 잰 시각)",
    // 시연 영상·화면. null이면 안 보인다. 예:
    // { type: "video", src: "assets/demo/incident.mp4", poster: "assets/demo/incident.jpg", alt: "결제 장애 시연", caption: "시연 환경(가상 데이터) 기준 결제 장애 대응 화면" }
    // { type: "image", src: "assets/demo/incident.png", alt: "결제 장애 시연", caption: "..." }
    media: null,
    stepsKicker: "시연 속 AI 직원들의 하루",
    stepsTitle: ["장애가 나면", "이렇게 움직입니다"],
    steps: [
      { n: "1", title: "감지", items: [["인프라 AI", "결제 오류 신호를 알아챕니다."], ["총괄 AI", "이슈를 열고 필요한 AI에게 일을 나눕니다."]] },
      { n: "2", title: "분석", items: [["아카이브 AI", "비슷한 과거 장애와 그때의 대응을 찾습니다."], ["데이터분석 AI", "실패 주문 7건, 영향 고객 6명, 피해 249,000원을 셉니다."]] },
      { n: "3", title: "조치", items: [["인프라 AI", "지난번 대응법으로 장애를 자동 해제합니다."], ["마케팅 AI", "쇼핑몰에 결제 장애 공지를 띄웁니다."], ["고객지원 AI", "피해 고객 6명 보상을 정하고 마케팅 AI에 쿠폰을 요청합니다."]] },
      { n: "4", title: "기록", items: [["재무 AI", "보상 12,000원을 장부에 기록합니다."], ["아카이브 AI", "이번 이슈를 저장해 다음 장애 때 다시 찾아 씁니다."]] },
    ],
    stepsNote: "시연 환경(가상 데이터) 기준 흐름이며 금액·건수는 시연용 값입니다.",
  },

  a2a: {
    kicker: "A2A 자동화",
    title: ["6쌍의 부탁이", "일을 이어 줍니다"],
    lead: "A2A 자동화는 AI 직원끼리 직접 요청을 주고받아 대표님이 일일이 전달하지 않아도 일이 이어지는 방식입니다.",
    listKicker: "AI끼리 주고받는 요청",
    listTitle: ["부서 AI들은", "이렇게 서로 부탁합니다"],
    // from → to 순서
    items: [
      { from: "데이터분석 AI", to: "기획 AI", body: "뜨는 주제를 출처와 함께 넘깁니다." },
      { from: "기획 AI", to: "마케팅 AI", body: "대표님이 확정한 기획서만 넘깁니다." },
      { from: "마케팅 AI", to: "재무 AI", body: "캠페인을 시작하기 전에 예산 검토를 부탁합니다." },
      // PDF에 없는 추가 항목(병환): 이미 배포된 동작이다. PDF에도 같이 넣을 예정
      { from: "재무 AI", to: "고객지원 AI", body: "광고할 책에 클레임이 많은지 고객지원 AI의 집계를 읽고, 많으면 예산 의견을 줄입니다." },
      { from: "고객지원 AI", to: "마케팅 AI", body: "피해 고객에게 보낼 쿠폰을 요청합니다." },
      { from: "마케팅 AI", to: "데이터분석 AI", body: "끝난 뒤 판매액·주문 수를 읽어 성과를 기록합니다." },
    ],
    closing: "돈이 나가거나 외부에 게시하는 일은 이 흐름의 끝에서 반드시 사람 승인을 거칩니다.",
  },

  approval: {
    kicker: "위험은 이렇게 막습니다",
    title: ["승인이 필요한 일은", "세 가지입니다"],
    lead: ["AI가 먼저 준비하고 위험한 일은 사람이 결정할 때까지 보류됩니다.", "정해 둔 범위 안의 일은 AI가 알아서 처리합니다."],
    leadEm: "보류",
    listKicker: "사람이 결정하는 일",
    listTitle: ["이런 일은", "대표님이 결정합니다"],
    items: [
      { icon: "₩", title: "돈", body: "환불·쿠폰은 금액이 기준을 넘으면, 판매자 정산 지급은 항상 대표님 승인 뒤에만 실행됩니다." },
      { icon: "↗", title: "외부 게시", body: "SNS 같은 외부 게시는 항상 사람 승인을 거칩니다." },
      { icon: "✓", title: "배포와 기록", body: "모든 AI의 돈·게시·배포 요청은 등급을 매겨 처리하고 안전 처리 로그에 남깁니다." },
    ],
    shot: {
      src: "assets/shot-approve.webp", w: 1616, h: 280,
      alt: "판매자 정산 지급 승인 화면: 3곳 정산금 합계와 지급 승인 안내",
      title: "판매자 정산 지급 승인 화면",
      body: "AI가 정산금을 계산해 지급 승인을 요청하고 사람이 승인해야 지급이 기록됩니다.",
      note: "시연 가상 데이터 · 시연에서는 기록만 하고 실제 송금은 하지 않습니다.",
    },
  },

  benefits: {
    kicker: "대표님이 얻는 것",
    title: ["회사 안에서 ", "안심하고", " 맡깁니다"],
    // "사내망 안에서만 열립니다 / 외부 접근은 막혀 있고"는 서버 사실 확인(재헌·태윤) 전이라 쓰지 않았다 (README의 확인 필요 목록)
    items: [
      { tag: "설치", title: ["회사 서버에", "설치합니다"], body: "AXcellent는 프로그램만 설치하고 서버도 데이터도 회사 것입니다. 쇼핑몰·앱 주소와 API 키를 넣으면 주문·문의 데이터를 받아옵니다.", img: "assets/cables.webp", alt: "서버실 케이블" },
      { tag: "위험", title: ["근거 없이는", "지어내지 않습니다"], body: "돈·게시·배포는 대표님 승인과 기록을 거칩니다. 근거가 없으면 지어내지 않고 “확인할 수 없음”으로 둡니다.", img: "assets/desk.webp", alt: "스탠드가 켜진 책상" },
      { tag: "효과", title: ["한 번 겪은 일은", "기억합니다"], body: "같은 장애가 나면 지난 기록을 찾아 씁니다. 각 AI가 맡은 일을 제때 끝냈는지도 점수·순위 없이 확인합니다.", img: "assets/books.webp", alt: "책이 가득한 도서관" },
    ],
    note: "효과는 시연 환경에서 확인한 동작이며 비용 절감·인력 대체를 약속하지 않습니다.",
  },

  // 고객사 마케팅 사례. AXcellent 마케팅 AI가 고객사의 홍보를 기획·제작하고(사람 승인 뒤 게시), 반응을 모아 다음 기획에 쓰는 순환을 보여 준다.
  // 게시물·반응 숫자는 준하 님 자료(INTAKE.md)가 오면 posts에 채운다. 비어 있어도 칸은 "준비 중"으로 남는다(showEmpty).
  cases: {
    kicker: "고객사 마케팅 사례 (시연용)",
    title: ["AI 직원이 고객사의", "마케팅까지 합니다."],
    lead: "기획 → 제작 → 사람 승인 → 게시 → 반응 수집 → 다음 기획. 시연용 가상 고객사에서 이 순환을 돌린 기록을 이곳에 모읍니다.",
    // 순환 그림. note가 있으면 그 단계 아래에 작게 보인다(아직 연결 중인 단계 표시)
    loop: [
      { label: "데이터분석 AI", body: "뜨는 주제와 지난 반응을 읽습니다." },
      { label: "기획 AI", body: "무엇을 올릴지 기획서를 만듭니다." },
      { label: "마케팅 AI", body: "글·이미지·영상을 만들고 게시를 요청합니다." },
      { label: "사람 승인", body: "대표님이 승인해야 게시됩니다." },
      { label: "게시와 반응 수집", body: "올린 뒤 반응을 모아 다시 데이터분석으로 보냅니다.", note: "반응 수집은 연결 준비 중" },
    ],
    // 비어 있는 사례도 "준비 중" 칸으로 보일지. 공개할 때 정한다(false면 채워진 사례만 보인다)
    showEmpty: true,
    // 사례 칸. posts가 비어 있으면 "준비 중" 칸으로 나온다.
    // 게시물 하나: { img: "assets/demo/cat-post-1.jpg", alt: "…", caption: "…", date: "11월 20일",
    //   metrics: [["도달", "123"], ["좋아요", "12"]], measuredAt: "11월 27일", source: "Instagram 인사이트(7일 기준)" }
    // 반응 숫자(metrics)는 실제로 잰 값만 넣고, 잰 날짜(measuredAt)와 출처(source)를 함께 적는다. 둘 중 하나라도 없으면 숫자가 화면에 안 나온다.
    // 게시물·반응은 instagram/instagram.json(준하 님 자료)에서 읽는다. 자료 형식·검사: instagram/README.md
    // 인스타 게시물은 이 칸이 아니라 아래 "콘텐츠" 칸(posts.instagram)에 보인다. 이 칸에는 고객사 사이트 화면을 넣는다(posts는 비워 둔다)
    items: instagram.casesItems.map((it) => ({ ...it, posts: [] })),
    emptyText: "사례 준비 중",
    // 숫자를 실제 측정값으로 채우는 때에는 이 문장을 "시연용 가상 고객사 계정에서 잰 실제 값이며, 실제 기업의 매출·성과를 뜻하지 않습니다."로 맞춘다(README 참고)
    note: "게시물과 반응 숫자는 시연 환경(가상 계정) 기준이며 실제 고객 성과가 아닙니다. 모든 게시물은 AI가 요청하고 사람이 승인한 뒤에 올라갑니다.",
  },

  customers: {
    kicker: "함께하는 고객사",
    title: ["업종이 달라도", "같은 AI 직원 구조"],
    items: [
      { name: "캣북스", badges: ["시연 가상 고객사"], body: "책과 문구를 파는 온라인 서점. 고객지원·재무·마케팅·데이터분석 AI가 판매 서버와 연결돼 일합니다.", img: "assets/shot-catbooks.webp", w: 1141, h: 860, alt: "캣북스 온라인 서점 화면" },
      { name: "누비투어", badges: ["시연 가상 고객사", "연결 준비 중"], body: "패키지여행 예약 사이트. AXcellent를 모르는 상태로 만든 두 번째 고객사로 연결을 준비하고 있습니다.", img: "assets/shot-nubi.webp", w: 1141, h: 860, alt: "누비투어 패키지여행 사이트 화면" },
      { name: "스터디온에듀", badges: ["시연 가상 고객사", "연결 준비 중"], body: "온라인 강의 사이트. 강의·강사는 모두 시연을 위해 만든 가상 데이터이고, AXcellent와 연결을 준비하고 있습니다.", img: "assets/shot-studyon.webp", w: 1141, h: 860, alt: "스터디온에듀 온라인 강의 사이트 화면" },
    ],
    note: "회사마다 설정(상품 정보·조직도)을 따로 두어 업종이 달라도 같은 AI 직원 구조를 붙일 수 있게 설계했습니다.",
  },

  // 홍보 영상·게시물 칸. items에 넣으면 칸이 채워지고, 비어 있으면 빈 칸(준비 중)을 emptySlots개 보여 준다.
  // item: { title, caption, src(mp4, assets/demo/ 아래), ratio(예: "9 / 16"), poster, postUrl(원 게시물 링크, 선택), customer(고객사 이름, 선택), aiMade(true면 "AI가 만든 영상" 표시) }
  posts: {
    kicker: "콘텐츠",
    title: ["AXcellent가", "만든 콘텐츠"],
    lead: "AXcellent 홍보 영상과, 시연용 가상 고객사 캣북스를 위해 만든 인스타그램 게시물을 모았습니다. 반응 수치는 실제로 측정한 값이 생긴 뒤에만 표시합니다.",
    // 고객사별 인스타 게시물(instagram/instagram.json에서 읽는다). 게시물이 있는 고객사만 보인다
    instagram: instagram.casesItems,
    emptyTitle: "콘텐츠 준비 중",
    // true면 영상이 없어도 빈 칸 "준비 중"을 보여 준다. 기본은 영상이 올라올 때까지 구역과 메뉴를 숨긴다
    showEmpty: false,
    emptySlots: 2,
    // 영상 묶음 이름(영상 카드 위에 보인다)
    itemsTitle: "AXcellent 홍보 영상",
    // 영상 추가: mp4를 assets/demo/에 넣고(영문 소문자·숫자·-, 15MB 이하) 아래에 한 줄을 더한다.
    // ratio는 영상 비율(세로 "9 / 16", 가로 "16 / 9"), 가로 영상은 wide: true로 두 칸 폭으로 보인다
    items: [
      { title: "AXcellent 홍보 영상 (30초, 세로)", customer: "AXcellent 홍보 영상", caption: "AI 직원들이 서로 알아서 일하고 돈과 외부 게시는 대표님이 승인합니다. 자막만 나오고 소리는 없습니다. 시연 환경(가상 데이터) 기준.", src: "assets/demo/axcellent-promo-30s.mp4", ratio: "9 / 16", aiMade: true },
      { title: "AXcellent × DejaFix (1분, 음악 포함)", customer: "AXcellent 홍보 영상", caption: "장애 하나에, AI 아홉이 일을 나눠 맡아요. 소리(배경음악)가 나옵니다. 시연 환경(가상 데이터) 기준. 영상 속 안전 관문의 “10분”은 쿠폰·환불 같은 일반 보류 기준이며, 판매자 정산 지급 보류는 24시간입니다.", src: "assets/demo/axcellent-promo-66s.mp4", ratio: "16 / 9", wide: true, aiMade: true },
    ],
    note: "시연용 가상 고객사의 홍보물입니다. AI로 만든 영상·게시물에는 “AI가 만든 영상” 표시를 붙입니다.",
  },

  cta: {
    title: ["AI끼리", "알아서 일하고", "돈과 외부 게시는", "대표님이 승인합니다"],
    emIndex: 1,
    body: "동네 책방 같은 작은 가게도 회사 안에서 AI 직원들과 함께 일할 수 있습니다.",
  },
};
