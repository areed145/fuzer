import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, forward API calls to `fuzer serve`. In production the API serves the built UI.
const API = "http://127.0.0.1:8000";
const API_PATHS = ["/entities", "/review", "/lookup", "/stats", "/health", "/rebuild"];

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(API_PATHS.map((p) => [p, API])),
  },
});
