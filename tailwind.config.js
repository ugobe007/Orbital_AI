/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./dashboard/**/*.{html,js}",
  ],
  theme: {
    // Square corners throughout; only dots and pills stay round.
    borderRadius: { none: "0", sm: "0", DEFAULT: "0", md: "0", lg: "0", xl: "0", "2xl": "0", full: "9999px" },
    extend: {
      // Orbital design system: ground #EEF0EC, ink #111418, rules #C9CCC6, accent #1D4ED8.
      colors: {
        brand: { DEFAULT: "#16A34A", 600: "#15803D", 700: "#166534" },
        azure: { DEFAULT: "#1D4ED8", light: "#1D4ED8" },
        cta: { DEFAULT: "#1D4ED8", 600: "#1E40AF" },
        surface: {
          DEFAULT: "#EEF0EC",
          panel: "#FFFFFF",
          raised: "#F6F7F4",
          input: "#ECEEEA",
        },
        line: { DEFAULT: "#A9ADA6", strong: "#6E736C" },
        ink: { DEFAULT: "#111418", mut: "#1F2329", dim: "#464B53" },
      },
      fontFamily: {
        sans: ["IBM Plex Sans", "ui-sans-serif", "system-ui"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
        display: ["Archivo", "ui-sans-serif", "system-ui"],
      },
    },
  },
  plugins: [],
};
