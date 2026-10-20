export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"SFMono-Regular"', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
      boxShadow: {
        inset: 'var(--shadow-well)',
        emboss: 'var(--shadow-emboss)',
        panel: 'var(--shadow-panel)',
      },
    },
  },
  plugins: [],
};
