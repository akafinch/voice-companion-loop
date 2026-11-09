import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [svelte()],
  // One entry per page, each an HTML file that the bot serves from web/dist.
  input: {
    main: "index.html",
    character: "character.html",
  },
});
