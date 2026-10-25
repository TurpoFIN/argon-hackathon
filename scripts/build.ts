// Node 26 executes this TypeScript build entry directly.
import { build } from 'esbuild';
import { copyFile, mkdir } from 'node:fs/promises';
await mkdir('dist', { recursive: true });
await build({ entryPoints: ['frontend/main.ts'], bundle: true, outfile: 'dist/main.js', format: 'esm', target: 'es2022', sourcemap: true });
await Promise.all(['index.html', 'style.css'].map(name => copyFile(`frontend/${name}`, `dist/${name}`)));
