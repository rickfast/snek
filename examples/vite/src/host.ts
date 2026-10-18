import type { SnekImports } from "./generated/program.js";

export const imports: SnekImports = {
  fetch_text: async (url: string) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.text();
  },
};
