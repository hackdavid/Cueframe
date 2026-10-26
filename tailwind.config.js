/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/templates/**/*.html", "./app/static/js/**/*.js"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
      keyframes: {
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
      },
      animation: { shimmer: "shimmer 2s linear infinite" },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
