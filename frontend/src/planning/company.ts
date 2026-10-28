// 고객사(납품처)마다 달라지는 값. 고객사가 바뀌면 .env만 바꾼다.
// 기본값은 데모 고객사 캣북스(ax-frontend shop-web 기준).
export const COMPANY = {
  name: import.meta.env.VITE_COMPANY_NAME ?? '캣북스',
  tagline: import.meta.env.VITE_COMPANY_TAGLINE ?? '책과 문구, 고양이가 지키는 서점',
  shopUrl: import.meta.env.VITE_SHOP_WEB_URL ?? 'http://localhost:3000',
  adminUrl: import.meta.env.VITE_DASHBOARD_WEB_URL ?? 'http://localhost:3001', // 관리자(/admin/)로 돌아가는 링크
}

// 판매 사이트 파비콘을 브랜드 아이콘으로 쓴다. ① .env에 지정한 아이콘 ② 흔한 경로(logo.png·icon.svg·favicon.svg·favicon.ico)를
// 이미지로 불러 보기 ③ 사이트 HTML의 <link rel="icon"> (CORS가 열려 있을 때) ④ 다 안 되면 기본 로고
const FALLBACK_ICON = `${import.meta.env.BASE_URL}logo.png` // 하위 경로(/admin/planning/)로 배포해도 맞게
const COMMON_PATHS = ['/logo.png', '/icon.svg', '/favicon.svg', '/favicon.ico', '/favicon.png']

function loads(src: string, ms = 2500): Promise<boolean> {
  return new Promise((resolve) => {
    const img = new Image()
    const timer = setTimeout(() => resolve(false), ms)
    img.onload = () => { clearTimeout(timer); resolve(img.naturalWidth > 0) }
    img.onerror = () => { clearTimeout(timer); resolve(false) }
    img.src = src
  })
}

async function iconFromHtml(site: string): Promise<string | undefined> {
  try {
    const res = await fetch(site, { signal: AbortSignal.timeout(2500) })
    const doc = new DOMParser().parseFromString(await res.text(), 'text/html')
    const href = doc.querySelector('link[rel~="icon"]')?.getAttribute('href')
    return href ? new URL(href, site).href : undefined
  } catch {
    return undefined
  }
}

// 앞에서부터 먼저 불러지는 것을 쓴다 (경로 후보는 한꺼번에 확인)
export async function findSiteIcon(shopUrl = COMPANY.shopUrl): Promise<string> {
  // 게이트웨이(한 포트)에서는 판매 사이트 주소가 '/' 같은 상대 경로라 지금 주소 기준으로 바꾼다
  const site = new URL(shopUrl, window.location.href).href
  const explicit = import.meta.env.VITE_COMPANY_LOGO
  if (explicit && (await loads(explicit))) return explicit
  const paths = COMMON_PATHS.map((p) => new URL(p, site).href)
  const ok = await Promise.all(paths.map((p) => loads(p)))
  const hit = paths.find((_, i) => ok[i])
  if (hit) return hit
  const fromHtml = await iconFromHtml(site)
  if (fromHtml && (await loads(fromHtml))) return fromHtml
  return FALLBACK_ICON
}
