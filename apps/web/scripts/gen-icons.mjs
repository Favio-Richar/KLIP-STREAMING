// Genera los iconos PNG de la PWA desde la misma geometría que `public/icon.svg`.
// Sin dependencias: rasteriza con sobremuestreo y escribe PNG con zlib.
// Uso: node scripts/gen-icons.mjs
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "public");
mkdirSync(OUT, { recursive: true });

const ACCENT = [0xb8, 0xf4, 0x3c];
const INK = [0x0b, 0x0d, 0x10];
// La "K" de Klick sobre una cuadrícula de 32 × 32.
const K = [
  [9, 7],
  [13, 7],
  [13, 14],
  [20, 7],
  [25, 7],
  [17, 15],
  [25, 25],
  [20, 25],
  [14, 17],
  [14, 25],
  [9, 25],
];

function inPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function inRoundedSquare(x, y, size, r) {
  if (x < 0 || y < 0 || x > size || y > size) return false;
  const cx = x < r ? r : x > size - r ? size - r : x;
  const cy = y < r ? r : y > size - r ? size - r : y;
  return (x - cx) ** 2 + (y - cy) ** 2 <= r * r;
}

/** `maskable`: fondo a sangre y letra dentro de la zona segura (80 % central). */
function render(px, { maskable }) {
  const SS = 4;
  const buf = Buffer.alloc(px * px * 4);
  const scale = maskable ? 0.58 : 1;
  const offset = ((1 - scale) / 2) * 32;
  const poly = K.map(([x, y]) => [x * scale + offset, y * scale + offset]);
  for (let py = 0; py < px; py++) {
    for (let pxx = 0; pxx < px; pxx++) {
      let bg = 0;
      let ink = 0;
      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const x = ((pxx + (sx + 0.5) / SS) / px) * 32;
          const y = ((py + (sy + 0.5) / SS) / px) * 32;
          if (maskable || inRoundedSquare(x, y, 32, 7)) {
            bg++;
            if (inPolygon(x, y, poly)) ink++;
          }
        }
      }
      const total = SS * SS;
      const o = (py * px + pxx) * 4;
      const cover = bg / total;
      const inkShare = bg ? ink / bg : 0;
      for (let c = 0; c < 3; c++)
        buf[o + c] = Math.round(ACCENT[c] * (1 - inkShare) + INK[c] * inkShare);
      buf[o + 3] = Math.round(cover * 255);
    }
  }
  return buf;
}

const crcTable = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (b) => {
  let c = 0xffffffff;
  for (const byte of b) c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
function png(px, rgba) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(px, 0);
  header.writeUInt32BE(px, 4);
  header[8] = 8; // profundidad
  header[9] = 6; // RGBA
  const rows = Buffer.alloc((px * 4 + 1) * px);
  for (let y = 0; y < px; y++) {
    rows[y * (px * 4 + 1)] = 0;
    rgba.copy(rows, y * (px * 4 + 1) + 1, y * px * 4, (y + 1) * px * 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(rows, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const targets = [
  ["icon-192.png", 192, false],
  ["icon-512.png", 512, false],
  ["icon-maskable-512.png", 512, true],
  ["apple-touch-icon.png", 180, true],
];
for (const [name, px, maskable] of targets) {
  writeFileSync(join(OUT, name), png(px, render(px, { maskable })));
  console.log(`${name} (${px}×${px}${maskable ? ", maskable" : ""})`);
}
