// Chips de estado por copia interactivos en el tablero de pedidos:
// clic cicla el estado, aparece "Guardar", y al pulsarlo hace PUT con el
// copy_status actualizado. Correr:
//   docker run --rm -v "$PWD":/w:ro -w /tmp node:20-slim sh -c \
//     "npm i --silent --no-save jsdom && cp /w/tests/board_chips.test.mjs . && \\
//      PRINTCOST_ROOT=/w node board_chips.test.mjs"
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
const root = new URL(`file://${process.env.PRINTCOST_ROOT}/`);
const read = (p) => readFileSync(new URL(p, root), "utf8");
const ORDER = { id: 9, client: "Ana", description: "", payment_status: "pending",
  agreed_price: 100, currency: "$", due_date: null, manual_status: null, folder: null,
  deposit_amount: null, extra_expenses: [], status: "queued", folder_path: null, margin: null,
  items: [{ id: 1, label: "Pieza", printer_id: 1, printer_name: "Voron", gcode_filename: "a.gcode",
    quantity: 3, manual_status: null, unit_cost: null, extra_expenses: [], copy_status: [],
    status: "queued", printed: 0, progress: null, eta_s: null, failed_seen: 0 }] };
const API = { "/api/settings": {electricity_price_per_kwh:3,currency:"$",company_name:"",company_logo:"",company_info:"",payment_info:"",quote_terms:"",orders_folder_base:""},
  "/api/printers": [{id:1,name:"Voron",host:"10.0.0.1",ui_port:80}], "/api/materials": [], "/api/materials/usage": {}, "/api/calibrations": [],
  "/api/stats": {currency:"$",total_jobs:0,total_cost:0,total_energy_kwh:0,total_filament_g:0,cost_by_component:{},by_printer:[],by_month:[],cost_per_hour:[]},
  "/api/live": [], "/api/orders": [ORDER] };
const match = (u) => Object.keys(API).find((k) => u===k || u.startsWith(k+"?"));
const dom = new JSDOM(read("app/web/static/index.html"), { runScripts: "outside-only", url: "http://localhost:8088/" });
const { window } = dom; const errors = [];
window.addEventListener("error", (e) => errors.push(e.message));
let putBody = null, putUrl = null;
window.fetch = async (url, opts) => {
  if (opts && opts.method === "PUT") { putUrl = String(url); putBody = JSON.parse(opts.body); return { ok:true, status:200, json: async()=>ORDER }; }
  const k = match(String(url));
  if (k) return { ok:true, status:200, json: async()=>API[k] };
  return { ok:true, status:200, json: async()=>[] };
};
window.eval(read("app/web/static/app.js"));
const doc = window.document; const tick=()=>new Promise(r=>setTimeout(r,0));
await tick();await tick();
doc.querySelector('nav button[data-tab="pedidos"]').click();
await tick();await tick();await tick();
let ok=true; const chk=(c,m)=>{ if(!c){console.log("  ✗",m);ok=false;} else console.log("  ✓",m); };
const chips = doc.querySelectorAll(".board-chip");
chk(chips.length===3, `3 chips interactivos en el tablero (fueron ${chips.length})`);
const save = doc.querySelector(".board-save");
chk(save && save.hidden, "botón Guardar oculto al inicio");
// clic en el primer chip -> pending -> printing
chips[0].click(); await tick();
chk(chips[0].dataset.s==="printing", "clic cicla pending→printing");
chk(!save.hidden, "botón Guardar aparece tras el clic");
chk(doc.querySelector(".board-copies.dirty")!==null, "fila marcada dirty (pausa refresco)");
// clic de nuevo -> done
chips[0].click(); await tick();
chk(chips[0].dataset.s==="done", "segundo clic printing→done");
// guardar
save.click(); await tick(); await tick();
chk(putUrl && putUrl.endsWith("/api/orders/9"), "PUT al pedido correcto");
chk(putBody && JSON.stringify(putBody.items[0].copy_status)===JSON.stringify(["done","pending","pending"]), `copy_status enviado correcto (${putBody && JSON.stringify(putBody.items[0].copy_status)})`);
chk(errors.length===0, `sin errores JS (${JSON.stringify(errors)})`);
window.close(); process.exit(ok?0:1);
