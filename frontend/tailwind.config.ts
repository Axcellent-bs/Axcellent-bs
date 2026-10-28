import type { Config } from 'tailwindcss'

// catbooks에서 옮긴 설정. 색은 모두 styles.css의 CSS 변수를 읽어 라이트/다크를 변수 교체로 처리한다.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`
const scale = (name: string, shades: number[]) => Object.fromEntries(shades.map((s) => [s, v(`${name}-${s}`)]))

const config: Config = {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Pretendard Variable', 'Pretendard', '-apple-system', 'BlinkMacSystemFont', 'Apple SD Gothic Neo', 'Noto Sans KR', 'Segoe UI', 'sans-serif'],
      },
      colors: {
        canvas: v('canvas'),
        app: v('app'),
        surface: { DEFAULT: v('surface'), 2: v('surface-2') },
        accent: { DEFAULT: v('accent'), 50: v('accent-50') },
        chart: { 1: v('chart-1'), 2: v('chart-2') },
        hero: { from: v('hero-from'), to: v('hero-to') },
        ink: scale('ink', [50, 100, 300, 500, 700, 900]),
        brand: { ...scale('brand', [50, 100, 500, 600, 700]), strong: v('brand-strong') },
        red: scale('red', [50, 100, 700, 800]),
        amber: scale('amber', [50, 100, 700, 800]),
        emerald: scale('emerald', [50, 100, 700, 800]),
        violet: scale('violet', [50, 700]),
      },
    },
  },
  plugins: [],
}
export default config
