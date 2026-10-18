// Build first: snek build examples/library/greetings.py --library --export greet --export add --export twice
import { readFile } from "node:fs/promises";
import { instantiate } from "./greetings.js";

const library = await instantiate(await readFile(new URL("./greetings.wasm", import.meta.url)));
console.log(library.greet("Snek 🐍"));
console.log(library.add(20, 22));
console.log(library.twice(9007199254740993n));
