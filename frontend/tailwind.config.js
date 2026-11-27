/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        cream: {
          50: '#FFFDF9',
          100: '#FFFBF5',
          200: '#FAF3EA',
          300: '#F4E9DC',
          400: '#EAE0D3',
          500: '#DDD0C0',
        },
        mint: {
          50: '#F2F8EE',
          100: '#E5F2DF',
          200: '#CCE4C0',
          300: '#A9CFA0',
          400: '#74A882',
          500: '#4F8B6B',
          600: '#3D7358',
          700: '#2F5E47',
          800: '#244937',
          900: '#1A3629',
        },
        ink: {
          900: '#482418',
          700: '#634235',
          500: '#7E5E4F',
          400: '#9A7C6D',
          300: '#B59B8C',
        },
        peach: {
          50: '#FFF5F0',
          100: '#FCE4D8',
          200: '#F8D0BE',
        },
        cinnabar: {
          50: '#FDF0EE',
          200: '#F0C5BE',
          600: '#B54638',
          700: '#96362A',
        },
      },
      fontFamily: {
        display: ['"Noto Serif SC"', '"Songti SC"', 'Georgia', 'serif'],
        sans: ['"HarmonyOS Sans SC"', '"PingFang SC"', '"Microsoft YaHei"', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"SF Mono"', 'ui-monospace', 'Menlo', 'monospace'],
      },
      boxShadow: {
        paper: '0 2px 10px -2px rgba(72, 36, 24, 0.06), 0 1px 3px rgba(72, 36, 24, 0.04)',
        'paper-lg': '0 14px 36px -10px rgba(72, 36, 24, 0.12), 0 4px 12px rgba(72, 36, 24, 0.05)',
        dock: '0 14px 32px -6px rgba(26, 54, 41, 0.32)',
      },
    },
  },
  plugins: [],
}
