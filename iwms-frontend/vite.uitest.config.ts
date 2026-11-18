// Vite config used only by the UI tests (iwms-backend/tests/console/test_ui_console.py).
// Same app, but its own dependency cache, so a UI test run never disturbs
// the dev server you have open (`npm run dev` keeps node_modules/.vite).
import { mergeConfig } from "vite";
import base from "./vite.config";

export default mergeConfig(base, {
  cacheDir: "node_modules/.vite-uitest",
  server: { hmr: false },
  clearScreen: false,
});
