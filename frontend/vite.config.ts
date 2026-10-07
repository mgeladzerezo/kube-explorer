import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backend = process.env.BACKEND_URL ?? "http://localhost:8105";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
    proxy: {
      "/api": backend,
      "/ws": { target: backend, ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
  },
});
