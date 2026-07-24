// Carga la UI real (index.html + app.js) en jsdom, con la API simulada, y
// comprueba la aritmética de una cotización de varios ítems: que el envío y el
// IVA se cobran UNA vez y no una por pieza, y que el PDF sale itemizado.
//
//   docker run --rm -v "$PWD":/w:ro -w /tmp node:20-slim sh -c \
//     "npm i --silent --no-save jsdom && cp /w/tests/quote_items.test.mjs . && \
//      PRINTCOST_ROOT=/w node quote_items.test.mjs"
//
// jsdom se instala en /tmp y el test se copia a su lado: los import de ESM se
// resuelven desde la carpeta del propio fichero, así que tienen que convivir.
// El proyecto se monta en solo lectura para no dejarle un node_modules dentro.
import { readFileSync } from "node:fs";
import assert from "node:assert";
import { JSDOM } from "jsdom";

const root = process.env.PRINTCOST_ROOT
  ? new URL(`file://${process.env.PRINTCOST_ROOT}/`)
  : new URL("..", import.meta.url);
const read = (p) => readFileSync(new URL(p, root), "utf8");

const API = {
  "/api/settings": { electricity_price_per_kwh: 3, currency: "$", company_name: "M3D",
                     company_logo: "", company_info: "", payment_info: "", quote_terms: "" },
  "/api/printers": [{ id: 1, name: "Voron 2.4" }, { id: 2, name: "Ender 3V2" }],
  "/api/materials": [
    { id: 1, name: "PLA gen", material_type: "PLA", price_per_kg: 300, density_g_cm3: 1.24 },
    { id: 2, name: "PETG gen", material_type: "PETG", price_per_kg: 450, density_g_cm3: 1.27 },
  ],
  "/api/stats": { currency: "$", total_jobs: 0, total_cost: 0, total_energy_kwh: 0,
                  total_filament_g: 0, cost_by_component: {}, by_printer: [], by_month: [],
                  cost_per_hour: [] },
  "/api/live": [],
};
const match = (url) =>
  Object.keys(API).find((k) => url === k || url.startsWith(k + "?"));

let printedHtml = null;

const dom = new JSDOM(read("app/web/static/index.html"), {
  runScripts: "outside-only", url: "http://localhost:8088/",
});
const { window } = dom;

window.fetch = async (url) => {
  const key = match(String(url));
  if (key) return { ok: true, status: 200, json: async () => API[key] };
  if (String(url).includes("/quote-defaults")) {
    return { ok: true, status: 200, json: async () => ({
      currency: "$", electricity_price_per_kwh: 3, avg_power_kw: 0.15,
      avg_power_source: "impresora", depreciation_per_h: 2.5, failure_rate_pct: 10 }) };
  }
  return { ok: true, status: 200, json: async () => [] };
};
window.open = () => ({
  document: { write: (h) => { printedHtml = h; }, close() {} },
  focus() {}, print() {},
});

window.eval(read("app/web/static/app.js"));
const doc = window.document;
const tick = () => new Promise((r) => setTimeout(r, 0));
await tick(); await tick();

let pass = 0;
const t = async (name, fn) => {
  try { await fn(); console.log("  ✓", name); pass++; }
  catch (e) { console.log("  ✗", name, "\n     ", e.message); process.exitCode = 1; }
};

// Abre la pestaña de cotización como haría el usuario.
doc.querySelector('nav button[data-tab="cotizacion"]').click();
await tick(); await tick();

const setH = (f, v) => {
  const el = doc.querySelector(`#cotiz-header [data-h="${f}"]`);
  el.value = String(v);
  el.dispatchEvent(new window.Event("input", { bubbles: true }));
};
const setI = (item, sel, v) => {
  const el = item.querySelector(sel);
  el.value = String(v);
  el.dispatchEvent(new window.Event("input", { bubbles: true }));
};
const items = () => [...doc.querySelectorAll("#cotiz-items .quote-item")];
const money = (s) => parseFloat(String(s).replace(/[^\d.-]/g, ""));

console.log("estructura");
await t("arranca con cabecera y un ítem", () => {
  assert.strictEqual(doc.querySelectorAll("#cotiz-header [data-h]").length, 5);
  assert.strictEqual(items().length, 1);
});
await t("la luz se propone desde Ajustes", () =>
  assert.strictEqual(doc.querySelector('#cotiz-header [data-h="luz"]').value, "3"));

console.log("aritmética con un ítem");
// Cabecera: luz 3/kWh, operador 100/h, fallos 10%, envío 200, IVA 16%
setH("luz", 3); setH("op", 100); setH("fallos", 10); setH("envio", 200); setH("iva", 16);
const it1 = items()[0];
// 0.1 kg × 300/kg = 30 material · 0.15 kW × 4 h × 3 = 1.8 luz
// prep 0.5 h × 100 = 50 · post 0 · amort 2.5/h × 4 h = 10  → subtotal 91.8
// +10% fallos = 100.98 por pieza
setI(it1, '[data-f="kg"]', 300); setI(it1, '[data-f="masa"]', 0.1);
setI(it1, '[data-f="tiempo"]', 4); setI(it1, '[data-f="prep"]', 0.5);
setI(it1, '[data-f="post"]', 0); setI(it1, '[data-f="kw"]', 0.15);
setI(it1, '[data-f="amort"]', 2.5); setI(it1, ".q-qty2", 1);

await t("coste de la pieza", () => {
  const txt = it1.querySelector(".q-itemcost").textContent;
  assert.ok(/100\.98/.test(txt), txt);
});
await t("total = coste ×1.5 + envío + IVA sobre ambos", () => {
  // 100.98 × 1.5 = 151.47 ; (151.47 + 200) × 0.16 = 56.235 ; total 407.705
  const txt = doc.querySelector("#cotiz-bar .q-total").textContent;
  assert.ok(/407\.7/.test(txt), txt);
});

console.log("varios ítems: lo que importa");
const it2 = window.eval("addItem()");
setI(it2, '[data-f="kg"]', 300); setI(it2, '[data-f="masa"]', 0.1);
setI(it2, '[data-f="tiempo"]', 4); setI(it2, '[data-f="prep"]', 0.5);
setI(it2, '[data-f="post"]', 0); setI(it2, '[data-f="kw"]', 0.15);
setI(it2, '[data-f="amort"]', 2.5); setI(it2, ".q-qty2", 1);

await t("el ítem nuevo hereda impresora y material del último", () => {
  const ultimo = items().at(-1);
  setI(ultimo, ".q-type", "PETG");
  const nuevo = window.eval("addItem()");
  try {
    assert.strictEqual(nuevo.querySelector(".q-type").value, "PETG");
  } finally {
    // Sin este finally, un fallo aquí deja el ítem suelto y descuadra el
    // recuento de todas las comprobaciones siguientes.
    nuevo.remove();
    setI(ultimo, ".q-type", "");
    window.eval("recalcQuote()");
  }
});

await t("EL PUNTO: el envío se cobra una vez, no una por ítem", () => {
  // Dos ítems idénticos: coste 201.96 → venta 302.94
  // IVA = (302.94 + 200) × 0.16 = 80.47 ; total = 583.41
  // Si el envío se cobrara por ítem, el total sería 200 más.
  const txt = doc.querySelector("#cotiz-bar .q-total").textContent;
  assert.ok(/583\.4/.test(txt), txt);
  assert.ok(/envío \$?\s*200/.test(txt) || /envío 200/.test(txt), txt);
});

await t("cantidades: x3 en un ítem multiplica solo ese", () => {
  setI(it2, ".q-qty2", 3);
  // coste 100.98 × 4 ud = 403.92 → venta 605.88 ; IVA (605.88+200)×.16 = 128.94
  const txt = doc.querySelector("#cotiz-bar .q-total").textContent;
  assert.ok(/934\.8/.test(txt), txt);
  assert.ok(/4 ud/.test(txt), txt);
  setI(it2, ".q-qty2", 1);
});

await t("quitar un ítem recalcula", () => {
  const antes = doc.querySelector("#cotiz-bar .q-total").textContent;
  it2.querySelector(".q-remove").click();
  const despues = doc.querySelector("#cotiz-bar .q-total").textContent;
  assert.notStrictEqual(antes, despues);
  assert.ok(/407\.7/.test(despues), despues);
});

console.log("PDF");
await t("una fila por ítem, numeradas", () => {
  // Se parte de cero para que la numeración no dependa de los tests previos.
  items().forEach((i) => i.querySelector(".q-remove").click());
  const a = window.eval("addItem()"); setI(a, ".q-desc", "soporte.gcode");
  const b = window.eval("addItem()"); setI(b, ".q-desc", "tapa.gcode");
  doc.getElementById("print-quote").click();
  assert.ok(printedHtml, "no se generó el documento");
  assert.ok(printedHtml.includes("1. soporte.gcode"), "falta el ítem 1");
  assert.ok(printedHtml.includes("2. tapa.gcode"), "falta el ítem 2");
});
await t("sin miniaturas no se cuela una columna vacía", () =>
  assert.ok(!printedHtml.includes('class="thumb"')));
await t("con miniatura aparece la columna", () => {
  items()[0]._thumb = "data:image/png;base64,AAA=";
  doc.getElementById("print-quote").click();
  assert.ok(printedHtml.includes('class="thumb"'));
  assert.ok(printedHtml.includes("data:image/png;base64,AAA="));
});
await t("usa la paleta de marca, no el azul de antes", () => {
  assert.ok(printedHtml.includes("#e0472a"), "falta el rojo M3D");
  assert.ok(!printedHtml.includes("#3b7dd8"), "quedó el azul viejo");
});
await t("el cliente sale si se rellena", () => {
  doc.getElementById("q-cliente").value = "ACME S.A.";
  doc.getElementById("print-quote").click();
  assert.ok(printedHtml.includes("ACME S.A."));
});
await t("sin ítems no imprime", () => {
  items().forEach((i) => i.querySelector(".q-remove").click());
  printedHtml = null;
  doc.getElementById("print-quote").click();
  assert.strictEqual(printedHtml, null);
});

console.log(`\n${pass} comprobaciones OK`);

// app.js deja vivo el setInterval del refresco en vivo, que en node mantiene el
// bucle de eventos abierto: sin esto el proceso no termina nunca.
window.close();
process.exit(process.exitCode || 0);
