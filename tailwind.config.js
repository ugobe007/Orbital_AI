/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./site/**/*.html",
    "./dashboard/**/*.{html,js}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Marketing site
        emerald: { DEFAULT: "#3ecf8e", 400: "#4ade9a", 600: "#22b573", dim: "#164a37" },
        amber: { DEFAULT: "#ffb020", 400: "#ffc24d", 600: "#e8930c" },
        // Dashboard console
        brand: { DEFAULT: "#00be7d", 600: "#00a06a", 700: "#00855a" },
        azure: { DEFAULT: "#00a5da", light: "#3dbfe2" },
        cta: { DEFAULT: "#ffa01f", 600: "#e8890c" },
        // Shared surfaces / ink (dashboard values; marketing uses same names)
        surface: {
          DEFAULT: "#0a0d14",
          panel: "#0c0f17",
          raised: "#161c28",
          input: "#1b2230",
        },
        line: { DEFAULT: "#1e2530", strong: "#2a3340" },
        ink: { DEFAULT: "#e8ecf1", mut: "#9aa4b2", dim: "#697585" },
      },
      fontFamily: {
        sans: ["Space Grotesk", "ui-sans-serif", "system-ui"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
