import type { Config } from 'tailwindcss'

// 색은 모두 styles.css의 CSS 변수(common-frontend 토큰 값)를 읽는다.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`
const scale = (name: string, shades: number[]) => Object.fromEntries(shades.map((s) => [s, v(`${name}-${s}`)]))

const config: Config = {
  // 몇몇 화면에 dark: 클래스가 남아 있다. 'class'로 두어 OS 다크 설정에 끌려가지 않게 한다(데모는 밝은 화면만)
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
