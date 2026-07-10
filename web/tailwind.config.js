/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Brand palette pulled from the Hook AI logo (navy + teal on cream).
        cream: "#f4f3ee",
        paper: "#ffffff",
        navy: { DEFAULT: "#1f3a5f", dark: "#16304e", 600: "#27507f" },
        teal: { DEFAULT: "#4f8d80", soft: "#6aa597" },
        line: "#e5e3da",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
