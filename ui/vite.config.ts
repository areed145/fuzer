import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, forward API calls to `fuzer serve`. In production the API serves the built UI.
const API = "http://127.0.0.1:8000";
const API_PATHS = ["/entities", "/review", "/lookup", "/stats", "/health", "/rebuild", "/ontology", "/graph", "/network", "/events", "/documents", "/downtime", "/incidents", "/knowledge"];

export default defineConfig({
  plugins: [react()],
  // Cytoscape (~600 kB) is split into its own chunk and only loaded by the graph views.
  build: { chunkSizeWarningLimit: 700 },
  server: {
    proxy: Object.fromEntries(API_PATHS.map((p) => [p, API])),
  },
});
