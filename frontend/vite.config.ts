import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In compose the API is http://api:8000; on the host http://localhost:8000.
const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:8000";
// Docker Desktop bind mounts on Windows do not deliver file events, so poll there.
const polling = process.env.CHOKIDAR_USEPOLLING === "true";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: apiTarget, changeOrigin: true } },
    watch: polling ? { usePolling: true, interval: 300 } : undefined,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
