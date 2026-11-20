import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api/events": {
        target: `http://127.0.0.1:${process.env.CATALOG_PORT || "8002"}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/api/bookings": {
        target: `http://127.0.0.1:${process.env.BOOKING_PORT || "8001"}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
