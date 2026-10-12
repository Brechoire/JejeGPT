/** Configuration Tailwind JejeGPT (build précompilé).
 *
 * Reproduit à l'identique la config inline de l'ancien CDN Play :
 * couleurs ink/accent + police Roboto. Rebuild après ajout de classes :
 *   npm run build:css
 */
module.exports = {
  content: ["./templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        ink: "#0d0d0d",
        accent: "#ff4d00",
      },
      fontFamily: {
        sans: [
          "Roboto",
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          '"Helvetica Neue"',
          "Arial",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
