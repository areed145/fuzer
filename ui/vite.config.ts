import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In dev, forward API calls to `fuzer serve`. In production the API serves the built UI.
const API = "http://127.0.0.1:8000";
const API_PATHS = ["/entities", "/review", "/lookup", "/stats", "/health", "/rebuild", "/ontology", "/graph", "/network", "/events", "/documents", "/downtime", "/incidents", "/knowledge", "/lessons", "/assistant", "/feedback", "/figures", "/map", "/work", "/value", "/jobs", "/settings", "/sources", "/board", "/reports", "/studies", "/upsets", "/analytics"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Cytoscape (~600 kB) and plotly.js (~1.4 MB) are split into their own chunks, loaded only by the
  // graph views and by answers with charts.
  build: { chunkSizeWarningLimit: 1500 },
  server: {
    proxy: Object.fromEntries(API_PATHS.map((p) => [p, API])),
  },
});
