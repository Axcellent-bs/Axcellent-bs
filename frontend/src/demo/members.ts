// 일반회원을 회사 계정에 연결하는 규칙. 관리자(회사 계정)가 회원 연결(/system/members)에서 정한다.
// - 계정 연결: 회원 이메일을 회사에 묶는다 (가입 전이어도 미리 넣어 둘 수 있다)
// - IP 연결: 사내 PC IP(또는 대역)를 묶는다. 그 PC에서 가입·로그인하면 회사 회원이 된다
// 데모라 브라우저에만 기억한다. 실제로는 서버가 요청 IP를 보지만, 데모는 "이 PC" IP를 ?ip= 로 정한다.
import { useSyncExternalStore } from 'react'

export type MemberLinks = { emails: string[]; ips: string[] }

const KEY = 'axcellent-demo-member-links'
const EMPTY: MemberLinks = { emails: [], ips: [] }
const read = (): MemberLinks => {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? 'null')
    return raw && Array.isArray(raw.emails) && Array.isArray(raw.ips) ? raw : EMPTY
  } catch {
    return EMPTY
  }
}
let links = read()
const listeners = new Set<() => void>()
const write = (next: MemberLinks) => {
  links = next
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* 저장이 막힌 브라우저는 새로고침하면 처음 상태로 돌아간다 */
  }
  listeners.forEach((l) => l())
}
const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => listeners.delete(l)
}
export const getLinks = () => links
export const useLinks = () => useSyncExternalStore(subscribe, getLinks)

const norm = (email: string) => email.trim().toLowerCase()
export const linkEmail = (email: string) => {
  const e = norm(email)
  if (e && !links.emails.includes(e)) write({ ...links, emails: [...links.emails, e] })
}
export const unlinkEmail = (email: string) => write({ ...links, emails: links.emails.filter((x) => x !== norm(email)) })
export const linkIp = (rule: string) => {
  const r = rule.trim()
  if (r && !links.ips.includes(r)) write({ ...links, ips: [...links.ips, r] })
}
export const unlinkIp = (rule: string) => write({ ...links, ips: links.ips.filter((x) => x !== rule) })

const toNum = (ip: string) => ip.split('.').reduce((n, x) => n * 256 + Number(x), 0)
/** IP 하나 또는 CIDR 대역(192.168.10.0/24) */
export const validRule = (rule: string) => {
  const m = /^(\d{1,3}(?:\.\d{1,3}){3})(?:\/(\d{1,2}))?$/.exec(rule.trim())
  return !!m && m[1].split('.').every((x) => Number(x) <= 255) && (m[2] === undefined || Number(m[2]) <= 32)
}
export function ipMatches(ip: string, rule: string) {
  if (!validRule(rule) || !validRule(ip)) return false
  const [base, bits] = rule.trim().split('/')
  if (bits === undefined) return base === ip
  const shift = 32 - Number(bits)
  return Math.floor(toNum(ip) / 2 ** shift) === Math.floor(toNum(base) / 2 ** shift)
}

// 데모: 이 PC의 IP. ?ip=192.168.10.31 로 열면 그 IP로 기억한다 (실제로는 서버가 요청 IP로 안다)
const IP_KEY = 'axcellent-demo-this-ip'
const DEFAULT_IP = '192.168.10.88'
export function thisPcIp(): string {
  try {
    const q = new URLSearchParams(window.location.search).get('ip')
    if (q && validRule(q) && !q.includes('/')) {
      localStorage.setItem(IP_KEY, q)
      return q
    }
    return localStorage.getItem(IP_KEY) ?? DEFAULT_IP
  } catch {
    return DEFAULT_IP
  }
}

export const ipLinked = (ip: string, l = links) => l.ips.some((r) => ipMatches(ip, r))
/** 일반회원이 회사에 연결돼 있나: 계정(이메일)이 묶였거나 이 PC IP가 묶였거나 */
export const memberLinked = (email: string, ip = thisPcIp(), l = links) => l.emails.includes(norm(email)) || ipLinked(ip, l)
