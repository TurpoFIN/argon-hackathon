import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve, extname, sep } from "node:path";

const root = dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 5173);
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
const server = createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const path = resolve(root, `.${pathname === "/" ? "/index.html" : pathname}`);
    if (!path.startsWith(root + sep) || !["index.html", "style.css", "scene.js"].includes(path.slice(root.length + 1))) {
      response.writeHead(404).end();
      return;
    }
    const body = await readFile(path);
    response.writeHead(200, { "Content-Type": `${types[extname(path)]}; charset=utf-8`, "Cache-Control": "no-cache" });
    response.end(body);
  } catch {
    response.writeHead(404).end();
  }
});
server.listen(port, "127.0.0.1", () => console.log(`Local: http://localhost:${port}`));
