// Regresión: editar un pedido con gastos extra ya guardados renderizaba solo
// la primera pieza y dejaba muertos guardar/cancelar (expenseEditor hacía
// insertBefore contra un botón aún no insertado -> NotFoundError).
//
//   docker run --rm -v "$PWD":/w:ro -w /tmp node:20-slim sh -c \
//     "npm i --silent --no-save jsdom && cp /w/tests/order_edit.test.mjs . && \
//      PRINTCOST_ROOT=/w node order_edit.test.mjs"
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const root = new URL(`file://${process.env.PRINTCOST_ROOT || ".."}/`);
const read = (p) => readFileSync(new URL(p, root), "utf8");

const ORDER6 = {
  id: 6, client: "Monica Jimenez", description: "Recorditos",
  payment_status: "pending", agreed_price: 2200, currency: "$",
  due_date: null, manual_status: null, folder: "6", deposit_amount: null,
  extra_expenses: [], status: "printing", folder_path: null, margin: null,
  items: [
    { id: 1, label: "4 Conejitos", printer_id: 1, printer_name: "Creality Hi",
      gcode_filename: "Elysia Semati Crucesitas.gcode", quantity: 2,
      manual_status: null, unit_cost: null, extra_expenses: [{label:"Velitas",amount:6},{label:"Celofan",amount:2}],
      copy_status: ["done","done"], status: "printed", printed: 2, progress: null, eta_s: null, failed_seen: 0 },
    { id: 2, label: "10 Crucesitas", printer_id: 1, printer_name: "Creality Hi",
      gcode_filename: "Elysia Semati Crucesitas.gcode", quantity: 3,
      manual_status: null, unit_cost: null, extra_expenses: [], copy_status: [],
      status: "queued", printed: 0, progress: null, eta_s: null, failed_seen: 0 },
    { id: 3, label: "10 Angelitos", printer_id: 1, printer_name: "Creality Hi",
      gcode_filename: "Elysia Semati Angelitos.gcode", quantity: 3,
      manual_status: null, unit_cost: null, extra_expenses: [], copy_status: [],
      status: "queued", printed: 0, progress: null, eta_s: null, failed_seen: 0 },
    { id: 4, label: "10 Conejitos", printer_id: 1, printer_name: "Creality Hi",
      gcode_filename: "OSpj-Elysia_Semati-recuerdito-Modelo_PLA_3h7m.gcode", quantity: 3,
      manual_status: null, unit_cost: null, extra_expenses: [], copy_status: [],
      status: "queued", printed: 0, progress: null, eta_s: null, failed_seen: 0 },
  ],
};
const API = {
  "/api/settings": { electricity_price_per_kwh: 3, currency: "$", company_name: "", company_logo: "", company_info: "", payment_info: "", quote_terms: "", orders_folder_base: "" },
  "/api/printers": [{ id: 1, name: "Creality Hi", host: "10.0.0.1", ui_port: 80 }],
  "/api/materials": [], "/api/materials/usage": {}, "/api/calibrations": [],
  "/api/stats": { currency: "$", total_jobs: 0, total_cost: 0, total_energy_kwh: 0, total_filament_g: 0, cost_by_component: {}, by_printer: [], by_month: [], cost_per_hour: [] },
  "/api/live": [], "/api/orders": [ORDER6],
};
const match = (u) => Object.keys(API).find((k) => u === k || u.startsWith(k + "?"));

const dom = new JSDOM(read("app/web/static/index.html"), { runScripts: "outside-only", url: "http://localhost:8088/" });
const { window } = dom;
const errors = [];
window.addEventListener("error", (e) => errors.push("window.onerror: " + e.message));
window.fetch = async (url) => {
  const k = match(String(url));
  if (k) return { ok: true, status: 200, json: async () => API[k] };
  if (String(url).includes("/files") || String(url).includes("/history-files"))
    return { ok: true, status: 200, json: async () => [{ path: "Elysia Semati Crucesitas.gcode" }] };
  return { ok: true, status: 200, json: async () => [] };
};

try { window.eval(read("app/web/static/app.js")); }
catch (e) { console.log("THROW al cargar app.js:", e.message); process.exit(1); }
const doc = window.document;
const tick = () => new Promise((r) => setTimeout(r, 0));
await tick(); await tick();

doc.querySelector('nav button[data-tab="pedidos"]').click();
await tick(); await tick(); await tick();

// Click "Editar" del pedido 6.
const editBtn = doc.querySelector('[data-edit-order="6"]');
console.log("botón Editar encontrado:", !!editBtn);
try { editBtn.click(); } catch (e) { console.log("THROW al hacer click Editar:", e.message, "\n", e.stack); }
await tick(); await tick(); await tick();

const rows = doc.querySelectorAll(".oi-editor").length;
let ok = true;
const check = (c, m) => { if (!c) { console.log("  ✗", m); ok = false; } else console.log("  ✓", m); };
check(errors.length === 0, "sin errores de JS al editar (" + JSON.stringify(errors) + ")");
check(rows === 4, `las 4 piezas se renderizan (fueron ${rows})`);
check(!!doc.querySelector("#of-save"), "botón Guardar presente");
// Las filas de gastos de la pieza 0 se renderizan (Velitas, Celofan).
const p0exp = doc.querySelector(".oi-editor .oi-exp-host");
check(p0exp && p0exp.querySelectorAll(".expense-row").length === 2, "gastos de la pieza 0 con 2 filas");
window.close();
process.exit(ok ? 0 : 1);
