import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En dev, /api se proxea al backend local (mismo path que nginx en prod)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
