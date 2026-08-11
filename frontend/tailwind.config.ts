import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: "#0b0c0f",
        panel: {
          DEFAULT: "#14161b",
          2: "#101216",
          3: "#191c22",
        },
        line: {
          DEFAULT: "#23262e",
          2: "#2e323c",
        },
        muted: {
          DEFAULT: "#9aa1ac",
          2: "#767d89",
        },
        accent: {
          DEFAULT: "#ff7a1a",
          deep: "#e05a00",
          soft: "#ffb277",
        },
        hospital: "#ff5c5c",
        patient: "#5aa9ff",
        settled: "#3ddc84",
      },
      fontFamily: {
        sans: ["Plus Jakarta Sans", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      borderRadius: {
        sm: "8px",
        md: "12px",
        lg: "16px",
      },
    },
  },
  plugins: [],
};

export default config;
