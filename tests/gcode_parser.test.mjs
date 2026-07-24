// Prueba el parser de gcode de app.js extrayendo su bloque puro y
// ejecutándolo con ficheros sintéticos de cada laminador.
import { readFileSync } from "node:fs";
import assert from "node:assert";

const src = readFileSync(new URL("../app/web/static/app.js", import.meta.url), "utf8");
const from = src.indexOf("// --- Lectura de gcode en el navegador");
const to = src.indexOf("async function loadCotizacion");
assert(from > 0 && to > from, "no se encontró el bloque del parser");

const materials = [{ material_type: "PLA", density_g_cm3: 1.24 }];
const mod = await import(
  "data:text/javascript," +
  encodeURIComponent(
    `export const materials = ${JSON.stringify(materials)};\n` +
    src.slice(from, to) +
    "\nexport { parseGcode, parseGcodeTime, extractGcodeThumb };"
  )
);
const { parseGcode, parseGcodeTime, extractGcodeThumb } = mod;

const gcodeFile = (name, text) => {
  const b = new Blob([text]);
  return { name, size: b.size, slice: (...a) => b.slice(...a) };
};
const PNG1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const thumbBlock = (w, h, b64, kind = "thumbnail") =>
  `; ${kind} begin ${w}x${h} ${b64.length}\n` +
  b64.match(/.{1,60}/g).map((l) => "; " + l).join("\n") +
  `\n; ${kind} end\n`;

let pass = 0;
const t = async (name, fn) => {
  try { await fn(); console.log("  ✓", name); pass++; }
  catch (e) { console.log("  ✗", name, "\n     ", e.message); process.exitCode = 1; }
};

console.log("parseGcodeTime");
await t("formato PrusaSlicer '2h 30m 12s'", () =>
  assert.strictEqual(parseGcodeTime("2h 30m 12s"), 9012));
await t("con días", () => assert.strictEqual(parseGcodeTime("1d 2h"), 93600));
await t("Cura: segundos crudos", () => assert.strictEqual(parseGcodeTime("5400"), 5400));

console.log("extractGcodeThumb");
await t("elige la miniatura más grande", () => {
  const txt = thumbBlock(16, 16, PNG1x1) + thumbBlock(220, 124, PNG1x1) + "G1 X0\n";
  const uri = extractGcodeThumb(txt);
  assert.match(uri, /^data:image\/png;base64,/);
});
await t("ignora QOI (el navegador no lo pinta)", () =>
  assert.strictEqual(extractGcodeThumb(thumbBlock(50, 50, PNG1x1, "thumbnail_QOI")), null));
await t("acepta JPG", () =>
  assert.match(extractGcodeThumb(thumbBlock(50, 50, PNG1x1, "thumbnail_JPG")), /^data:image\/jpeg/));
await t("descarta un bloque cortado a la mitad", () => {
  const cortado = "; thumbnail begin 220x124 999\n; " + PNG1x1.slice(0, 40) + "\n";
  assert.strictEqual(extractGcodeThumb(cortado), null);
});
await t("sin miniatura devuelve null", () =>
  assert.strictEqual(extractGcodeThumb("G1 X0 Y0\nG1 Z1\n"), null));

console.log("parseGcode — PrusaSlicer / OrcaSlicer");
await t("lee capa, tiempo, peso y material", async () => {
  const gcode =
    thumbBlock(220, 124, PNG1x1) +
    "G1 X0 Y0\n".repeat(500) +
    "; filament used [mm] = 4210.5\n" +
    "; filament used [g] = 12.56\n" +
    "; estimated printing time (normal mode) = 1h 47m 3s\n" +
    "; filament_type = PLA\n" +
    "; layer_height = 0.16\n";
  const d = await parseGcode(gcodeFile("pieza.gcode", gcode));
  assert.strictEqual(d.layer_height, 0.16);
  assert.strictEqual(d.filament_g, 12.56);
  assert.strictEqual(d.estimated_time_s, 6423);
  assert.strictEqual(d.filament_type, "PLA");
  assert.match(d.thumbnail_uri, /^data:image\/png/);
  assert.strictEqual(d.filename, "pieza.gcode");
});

console.log("parseGcode — Cura");
await t("lee su sintaxis distinta", async () => {
  const gcode =
    ";Layer height: 0.28\n;TIME:7200\n;Filament used: 3.5m\n" +
    "G1 X0\n".repeat(100);
  const d = await parseGcode(gcodeFile("cura.gcode", gcode));
  assert.strictEqual(d.layer_height, 0.28);
  assert.strictEqual(d.estimated_time_s, 7200);
  // 3.5 m de PLA 1.75 ≈ 10.4 g, deducidos de la longitud
  assert.ok(Math.abs(d.filament_g - 10.44) < 0.2, `peso ${d.filament_g}`);
});

console.log("parseGcode — casos límite");
await t("gcode sin metadatos no revienta", async () => {
  const d = await parseGcode(gcodeFile("pelado.gcode", "G28\nG1 X10 Y10\n"));
  assert.strictEqual(d.layer_height, null);
  assert.strictEqual(d.estimated_time_s, null);
  assert.strictEqual(d.thumbnail_uri, null);
});
await t("rechaza .bgcode con mensaje claro", async () => {
  await assert.rejects(() => parseGcode(gcodeFile("x.bgcode", "binario")), /binario/i);
});
await t("multi-material: se queda con el primer tipo", async () => {
  const d = await parseGcode(gcodeFile("mm.gcode", "; filament_type = PETG;PLA\n"));
  assert.strictEqual(d.filament_type, "PETG");
});
await t("archivo grande: solo lee cabecera y cola", async () => {
  const relleno = "G1 X1 Y1 E0.5\n".repeat(200000);   // ~2.8 MB en medio
  const gcode = thumbBlock(100, 100, PNG1x1) + relleno +
                "; layer_height = 0.2\n; estimated printing time = 45m\n";
  const f = gcodeFile("grande.gcode", gcode);
  assert.ok(f.size > 2_000_000, "el fixture debe superar los 2 MB");
  const d = await parseGcode(f);
  assert.strictEqual(d.layer_height, 0.2);          // vino de la cola
  assert.strictEqual(d.estimated_time_s, 2700);
  assert.match(d.thumbnail_uri, /^data:image\/png/); // vino de la cabecera
});

console.log(`\n${pass} comprobaciones OK`);
