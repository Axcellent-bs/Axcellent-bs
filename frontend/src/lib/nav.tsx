// Next.js(next/link, next/navigation)에서 옮긴 화면이 그대로 동작하도록 react-router에 맞춘 얇은 층.
// http(s)로 시작하는 href는 다른 앱(쇼핑몰 ↔ 대시보드)이라 일반 <a>로 연다.
import { useMemo, type ComponentProps } from 'react'
import {
  Link as RouterLink,
  useLocation,
  useNavigate,
  useParams as useRouterParams,
  useSearchParams as useRouterSearchParams,
} from 'react-router'

export function Link({ href, ...rest }: Omit<ComponentProps<'a'>, 'href'> & { href: string }) {
  if (/^https?:\/\//.test(href)) return <a href={href} {...rest} />
  return <RouterLink to={href} {...rest} />
}

export function useRouter() {
  const navigate = useNavigate()
  return useMemo(
    () => ({
      push: (to: string) => navigate(to),
      replace: (to: string) => navigate(to, { replace: true }),
      back: () => navigate(-1),
    }),
    [navigate],
  )
}

export const usePathname = () => useLocation().pathname
export const useSearchParams = () => useRouterSearchParams()[0]
export const useParams = <T extends Record<string, string>>() => useRouterParams() as T
