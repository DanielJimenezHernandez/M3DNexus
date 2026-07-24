// printcost — SPA mínima en JS vanilla sobre la API REST.

const api = {
  async get(path) { return (await fetch(path)).json(); },
  async send(path, method, body) {
    const r = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.status === 204 ? null : r.json();
  },
};

let printers = [];
let materials = [];
let currency = "€";
let appSettings = {};

function money(v) { return `${(v ?? 0).toFixed(2)} ${currency}`; }
function fmtDate(s) { return s ? new Date(s).toLocaleString() : "—"; }
function fmtDur(s) {
  if (!s) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}
function fmtEta(s) {
  if (s == null) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}
function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; }

// Gráfica de barras horizontales (HTML, sin librerías externas)
function barChart(items, labelKey, valueKey, fmt) {
  if (!items || !items.length) return '<p class="muted">Sin datos todavía</p>';
  const max = Math.max(...items.map((i) => i[valueKey])) || 1;
  return '<div class="bars">' + items.map((i) => {
    const w = Math.max(2, (i[valueKey] / max) * 100);
    return `<div class="bar-row">
      <span class="bar-label" title="${i[labelKey]}">${i[labelKey]}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${w}%"></span></span>
      <span class="bar-val">${fmt(i[valueKey])}</span>
    </div>`;
  }).join("") + "</div>";
}

let toastTimer;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2500);
}

// --- Navegación por pestañas -------------------------------------------------
let currentTab = "dashboard";
document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    currentTab = b.dataset.tab;
    document.getElementById(b.dataset.tab).classList.add("active");
    loadTab(b.dataset.tab);
  });
});

function loadTab(name) {
  ({ dashboard: loadDashboard, pedidos: loadPedidos, jobs: loadJobs,
     printers: loadPrinters, materials: loadMaterials, calibracion: loadCalibracion,
     quote: loadQuote, cotizacion: loadCotizacion, settings: loadSettings })[name]?.();
}

// --- Dashboard ---------------------------------------------------------------
async function loadDashboard() {
  const s = await api.get("/api/stats");
  currency = s.currency || currency;
  const cards = [
    ["Coste total", money(s.total_cost)],
    ["Impresiones", s.total_jobs],
    ["Energía total", `${s.total_energy_kwh.toFixed(2)} kWh`],
    ["Filamento total", `${(s.total_filament_g / 1000).toFixed(2)} kg`],
  ];
  document.getElementById("stat-cards").innerHTML = cards
    .map(([l, v]) => `<div class="card"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("");

  const comp = s.cost_by_component;
  const labels = { energy: "Electricidad", filament: "Filamento", depreciation: "Amortización" };
  document.getElementById("component-table").innerHTML =
    `<tr><th>Componente</th><th class="num">Coste</th></tr>` +
    Object.keys(labels).map((k) => `<tr><td>${labels[k]}</td><td class="num">${money(comp[k])}</td></tr>`).join("");

  document.getElementById("printer-stats-table").innerHTML =
    `<tr><th>Impresora</th><th class="num">Impresiones</th><th class="num">Coste</th></tr>` +
    (s.by_printer.length ? s.by_printer.map((r) =>
      `<tr><td>${r.printer}</td><td class="num">${r.jobs}</td><td class="num">${money(r.cost)}</td></tr>`).join("")
      : `<tr><td colspan="3" class="muted">Sin datos todavía</td></tr>`);

  document.getElementById("chart-month").innerHTML =
    barChart(s.by_month, "month", "cost", money);
  document.getElementById("chart-material").innerHTML =
    renderCostPerHour(s.cost_per_hour);
}

// Etiqueta de cuán "afinada" está la potencia media usada para la luz/h.
const POWER_SOURCE_LABELS = {
  "impresora+tipo": "datos impresora+tipo", "tipo": "datos por tipo",
  "impresora": "datos por impresora", "defecto": "aún sin datos",
};
function powerSourceLabel(src) { return POWER_SOURCE_LABELS[src] || src; }

// Tabla €/h agrupada por impresora, con potencia media por material.
function renderCostPerHour(rows) {
  if (!rows.length) return `<p class="muted">Sin datos todavía</p>`;
  const groups = [];
  const idx = {};
  rows.forEach((r) => {
    if (!(r.printer in idx)) { idx[r.printer] = groups.length; groups.push({ printer: r.printer, rows: [] }); }
    groups[idx[r.printer]].rows.push(r);
  });
  let html = `<table>
    <tr><th>Tipo</th><th class="num">Potencia media</th><th class="num">Luz/h</th>
        <th class="num">Máquina/h</th><th class="num">Total/h</th><th>Base</th></tr>`;
  for (const g of groups) {
    html += `<tr class="group-row"><td colspan="6">${g.printer}</td></tr>`;
    html += g.rows.map((r) => `<tr>
      <td>${r.type}</td>
      <td class="num">${r.power_w != null ? r.power_w + " W" : "—"}</td>
      <td class="num">${money(r.electricity_per_h)}</td>
      <td class="num">${money(r.machine_per_h)}</td>
      <td class="num"><strong>${money(r.total_per_h)}/h</strong></td>
      <td class="muted">${r.jobs} impr · ${powerSourceLabel(r.power_source)}</td>
    </tr>`).join("");
  }
  return html + `</table>`;
}

// --- Impresiones en vivo (se refresca cada 5 s en el dashboard) --------------
async function renderLive() {
  const host = document.getElementById("live-section");
  if (!host) return;
  let live = [];
  try { live = await api.get("/api/live"); } catch (e) { return; }
  if (!live.length) { host.innerHTML = ""; return; }
  host.innerHTML = `<h2>Imprimiendo ahora</h2>` + live.map((l) => {
    const pct = Math.round((l.progress || 0) * 100);
    const c = l.cost || {};
    const thumb = l.thumbnail
      ? `<img class="live-thumb" alt="" onerror="this.style.display='none'"
            src="/api/thumbnail/${l.printer_id}?path=${encodeURIComponent(l.thumbnail)}">`
      : "";
    return `<div class="card live-card">
      ${thumb}
      <div class="live-body">
        <div class="live-head">
          <strong>${l.printer_name}</strong>
          <span class="muted">${(l.filename || "").split("/").pop()}</span>
          <span class="live-total">${money(c.total)}</span>
        </div>
        <div class="progress"><div class="bar" style="width:${pct}%"></div><span>${pct}%</span></div>
        <div class="live-metrics">
          <span>⏱ ${fmtDur(l.elapsed_s)} · faltan ${fmtEta(l.eta_s)}</span>
          <span>⚡ ${l.power_w != null ? l.power_w + " W" : "—"}</span>
          <span>🔌 ${l.energy_kwh != null ? l.energy_kwh.toFixed(3) + " kWh" : "—"}</span>
          <span>🧵 ${l.filament_g.toFixed(0)} g ${l.material ? "(" + l.material + ")" : ""}</span>
        </div>
        <div class="live-breakdown muted">luz ${money(c.energy)} · filam ${money(c.filament)} · amort ${money(c.depreciation)}</div>
      </div>
    </div>`;
  }).join("");
}
setInterval(() => { if (currentTab === "dashboard") renderLive(); }, 5000);

document.getElementById("sync-btn").addEventListener("click", async () => {
  document.getElementById("sync-status").textContent = "Sincronizando…";
  try {
    const r = await api.send("/api/sync", "POST");
    const n = (r.results || []).reduce((a, x) => a + x.processed, 0);
    document.getElementById("sync-status").textContent = `Listo (${n} procesados)`;
    loadDashboard();
  } catch (e) { toast("Error: " + e.message); document.getElementById("sync-status").textContent = ""; }
});

// --- Impresiones (búsqueda + filtros + ordenación, lado cliente) -------------
let allJobs = [];
let jobsSort = { key: "end_time", dir: "desc" };

async function loadJobs() {
  await ensureRefs();
  allJobs = await api.get("/api/jobs?limit=2000");
  populateJobsFilters();
  applyJobsView();
}

// Rellena los desplegables conservando la selección actual.
function populateJobsFilters() {
  const mTypeById = Object.fromEntries(materials.map((m) => [m.id, m.material_type]));
  const setOpts = (id, opts) => {
    const sel = document.getElementById(id);
    const cur = sel.value;
    sel.innerHTML = opts;
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  };
  setOpts("jobs-printer", `<option value="">Todas las impresoras</option>` +
    printers.map((p) => `<option value="${p.id}">${p.name}</option>`).join(""));
  const statuses = [...new Set(allJobs.map((j) => j.status))].sort();
  setOpts("jobs-status", `<option value="">Todos los estados</option>` +
    statuses.map((s) => `<option value="${s}">${s}</option>`).join(""));
  const types = [...new Set(allJobs.map((j) => mTypeById[j.material_id]).filter(Boolean))].sort();
  setOpts("jobs-type", `<option value="">Todos los tipos</option>` +
    types.map((t) => `<option value="${t}">${t}</option>`).join(""));
}

function applyJobsView() {
  const q = (document.getElementById("jobs-search").value || "").toLowerCase().trim();
  const fp = document.getElementById("jobs-printer").value;
  const fs = document.getElementById("jobs-status").value;
  const ft = document.getElementById("jobs-type").value;
  const onlyReview = document.getElementById("filter-review").checked;
  const pmap = Object.fromEntries(printers.map((p) => [p.id, p.name]));
  const mById = Object.fromEntries(materials.map((m) => [m.id, m]));

  let rows = allJobs.filter((j) => {
    if (fp && String(j.printer_id) !== fp) return false;
    if (fs && j.status !== fs) return false;
    if (ft && (mById[j.material_id]?.material_type) !== ft) return false;
    if (onlyReview && !j.needs_review) return false;
    if (q) {
      const hay = ((j.filename || "") + " " + (mById[j.material_id]?.name || "")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const { key, dir } = jobsSort;
  const sign = dir === "asc" ? 1 : -1;
  const val = (j) => ({
    end_time: j.end_time || "",
    printer: (pmap[j.printer_id] || "").toLowerCase(),
    filename: (j.filename || "").toLowerCase(),
    status: j.status,
    print_duration_s: j.print_duration_s || 0,
    filament_weight_g: j.filament_weight_g || 0,
    energy_kwh: j.energy_kwh == null ? -1 : j.energy_kwh,
    cost_total: j.cost_total || 0,
  }[key]);
  rows.sort((a, b) => { const x = val(a), y = val(b); return x < y ? -sign : x > y ? sign : 0; });

  renderJobsTable(rows, pmap, mById);
  document.getElementById("jobs-count").textContent =
    `${rows.length} de ${allJobs.length} impresiones`;
}

function renderJobsTable(rows, pmap, mById) {
  const arrow = (k) => (jobsSort.key === k ? (jobsSort.dir === "asc" ? " ▲" : " ▼") : "");
  const th = (k, label, cls = "") =>
    `<th class="sortable ${cls}" data-sort="${k}">${label}${arrow(k)}</th>`;
  const matOpts = (sel) => `<option value="">—</option>` +
    materials.map((m) => `<option value="${m.id}" ${m.id === sel ? "selected" : ""}>${m.name}</option>`).join("");

  const header = `<tr>
    ${th("end_time", "Fin")}${th("printer", "Impresora")}${th("filename", "Archivo")}
    ${th("status", "Estado")}${th("print_duration_s", "Duración", "num")}
    ${th("filament_weight_g", "Filam.", "num")}${th("energy_kwh", "Energía", "num")}
    <th>Material</th>${th("cost_total", "Coste", "num")}<th></th></tr>`;

  document.getElementById("jobs-table").innerHTML = header +
    (rows.length ? rows.map((j) => {
      const review = j.needs_review ? ` <span class="pill review">revisar</span>` : "";
      return `<tr>
        <td>${fmtDate(j.end_time)}</td>
        <td>${pmap[j.printer_id] || j.printer_id}</td>
        <td title="${j.filename || ""}">${(j.filename || "—").split("/").pop()}</td>
        <td><span class="pill ${j.status}">${j.status}</span>${review}</td>
        <td class="num">${fmtDur(j.print_duration_s)}</td>
        <td class="num">${j.filament_weight_g.toFixed(0)} g</td>
        <td class="num">${j.energy_kwh != null ? j.energy_kwh.toFixed(3) + " kWh" : "—"}</td>
        <td><select data-job="${j.id}" class="job-mat">${matOpts(j.material_id)}</select></td>
        <td class="num"><strong>${money(j.cost_total)}</strong></td>
        <td class="row-actions">
          <button class="btn ghost small" data-recompute="${j.id}">↻</button>
          <button class="btn danger small" data-del-job="${j.id}">✕</button>
        </td></tr>`;
    }).join("") : `<tr><td colspan="10" class="muted">Sin resultados con estos filtros.</td></tr>`);

  document.querySelectorAll("#jobs-table th.sortable").forEach((h) =>
    h.addEventListener("click", () => {
      const k = h.dataset.sort;
      if (jobsSort.key === k) jobsSort.dir = jobsSort.dir === "asc" ? "desc" : "asc";
      else { jobsSort.key = k; jobsSort.dir = ["filename", "printer", "status"].includes(k) ? "asc" : "desc"; }
      applyJobsView();
    }));
  document.querySelectorAll(".job-mat").forEach((sel) =>
    sel.addEventListener("change", async (e) => {
      const mid = e.target.value ? Number(e.target.value) : null;
      await api.send(`/api/jobs/${e.target.dataset.job}/assign-material`, "POST", { material_id: mid });
      toast("Material asignado"); loadJobs();
    }));
  document.querySelectorAll("[data-recompute]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api.send(`/api/jobs/${b.dataset.recompute}/recompute`, "POST");
      toast("Recalculado"); loadJobs();
    }));
  document.querySelectorAll("[data-del-job]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("¿Borrar esta impresión del registro?")) return;
      await api.send(`/api/jobs/${b.dataset.delJob}`, "DELETE");
      loadJobs();
    }));
}

// Listeners de los controles de filtro (estáticos, se registran una vez).
["jobs-search"].forEach((id) =>
  document.getElementById(id).addEventListener("input", applyJobsView));
["jobs-printer", "jobs-status", "jobs-type", "filter-review"].forEach((id) =>
  document.getElementById(id).addEventListener("change", applyJobsView));
document.getElementById("jobs-clear").addEventListener("click", () => {
  document.getElementById("jobs-search").value = "";
  ["jobs-printer", "jobs-status", "jobs-type"].forEach((id) => (document.getElementById(id).value = ""));
  document.getElementById("filter-review").checked = false;
  applyJobsView();
});

// --- Impresoras --------------------------------------------------------------
const PRINTER_FIELDS = [
  ["name", "Nombre", "text", ""],
  ["host", "IP / host", "text", ""],
  ["moonraker_port", "Puerto Moonraker", "number", 7125],
  ["ui_port", "Puerto interfaz Klipper (Mainsail/Fluidd)", "number", 80],
  ["ha_energy_entity", "Entidad energía HA (kWh)", "text", ""],
  ["ha_power_entity", "Entidad potencia HA (opcional)", "text", ""],
  ["purchase_price", "Precio de la impresora", "number", 0],
  ["amortization_years", "Tiempo amortización (años)", "number", 2],
  ["active_days_per_year", "Días activa al año", "number", 250],
  ["active_hours_per_day", "Horas por día", "number", 8],
];

// URL de la interfaz Klipper: host de la impresora + el puerto de la UI.
function klipperUrl(p) {
  if (!p.host) return null;
  const port = p.ui_port || 80;
  return `http://${p.host}${port === 80 ? "" : ":" + port}`;
}

let loadedByPrinter = {};

// Un hueco cargado: muestra de color + nombre, con el origen del dato.
function slotChip(s) {
  const sw = s.color_hex ? `<span class="swatch" style="background:${escHtml(s.color_hex)}"></span>` : "";
  const cls = s.source === "last-job" ? "muted" : "";
  const name = s.material ? escHtml(s.material) : "—";
  const tip = s.source === "last-job" ? " title=\"del último impreso\"" : "";
  return `<span class="slot-chip ${cls}"${tip}>${sw}${name}</span>`;
}

async function loadPrinters() {
  [printers, loadedByPrinter] = await Promise.all([
    api.get("/api/printers"),
    api.get("/api/printers/loaded").catch(() => ({})),
  ]);
  await ensureRefs();  // materiales, para los desplegables del editor de carga

  document.getElementById("printers-table").innerHTML =
    `<tr><th>Nombre</th><th>Cargado ahora</th><th class="muted">Moonraker</th>
     <th class="num">Precio</th><th>Activa</th><th></th></tr>` +
    (printers.length ? printers.map((p) => {
      const ku = klipperUrl(p);
      const name = ku
        ? `<a href="${ku}" target="_blank" rel="noopener" title="Abrir interfaz Klipper (${ku})">${escHtml(p.name)} ↗</a>`
        : escHtml(p.name);
      const slots = loadedByPrinter[p.id] || [];
      const multi = (p.slot_count || 1) > 1 ? ` <span class="pill">${p.slot_count} colores</span>` : "";
      const loaded = slots.length
        ? slots.map(slotChip).join(" ")
        : `<span class="muted">—</span>`;
      return `<tr>
        <td>${name}${multi}</td>
        <td class="loaded-cell">${loaded}
          <button class="btn ghost small" data-load-printer="${p.id}" title="Cambiar filamento cargado">✎</button></td>
        <td class="muted">${escHtml(p.moonraker_url)}</td>
        <td class="num">${money(p.purchase_price)}</td>
        <td>${p.enabled ? "sí" : "no"}</td>
        <td class="row-actions">
          <button class="btn ghost small" data-edit-printer="${p.id}">Editar</button>
          <button class="btn danger small" data-del-printer="${p.id}">✕</button>
        </td></tr>
        <tr class="load-editor-row" id="load-editor-${p.id}" hidden><td colspan="6"></td></tr>`;
    }).join("") : `<tr><td colspan="6" class="muted">Sin impresoras</td></tr>`);

  document.querySelectorAll("[data-edit-printer]").forEach((b) =>
    b.addEventListener("click", () => printerForm(printers.find((p) => p.id == b.dataset.editPrinter))));
  document.querySelectorAll("[data-del-printer]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("¿Borrar la impresora y TODAS sus impresiones registradas?")) return;
      await api.send(`/api/printers/${b.dataset.delPrinter}`, "DELETE"); loadPrinters();
    }));
  document.querySelectorAll("[data-load-printer]").forEach((b) =>
    b.addEventListener("click", () => toggleLoadEditor(+b.dataset.loadPrinter)));
}

// Editor inline: un desplegable de material por hueco.
function toggleLoadEditor(printerId) {
  const row = document.getElementById(`load-editor-${printerId}`);
  if (!row.hidden) { row.hidden = true; return; }
  const printer = printers.find((p) => p.id === printerId);
  const slots = loadedByPrinter[printerId] || [];
  const n = Math.max(1, printer.slot_count || 1);

  const matOpts = (sel) => `<option value="">— vacío —</option>` +
    materials.map((m) => `<option value="${m.id}" ${m.id === sel ? "selected" : ""}>${escHtml(m.name)}</option>`).join("");

  const selects = Array.from({ length: n }, (_, i) => {
    // Solo se pre-rellena lo fijado a mano; lo deducido se deja como sugerencia.
    const s = slots[i] || {};
    const val = s.source === "manual" ? s.material_id : "";
    return `<label class="field" style="margin:0">
      <span>${n > 1 ? `Hueco ${i + 1}` : "Filamento cargado"}</span>
      <select class="slot-sel">${matOpts(val)}</select></label>`;
  }).join("");

  row.querySelector("td").innerHTML = `<div class="load-editor">
    <div class="form-grid">${selects}</div>
    <div class="row-actions" style="margin-top:.6rem">
      <button class="btn small" data-save-load="${printerId}">Guardar carga</button>
      <button class="btn ghost small" data-cancel-load="${printerId}">Cerrar</button>
    </div></div>`;
  row.hidden = false;

  row.querySelector(`[data-save-load]`).onclick = async () => {
    const mats = [...row.querySelectorAll(".slot-sel")].map((s) => s.value ? +s.value : null);
    await api.send(`/api/printers/${printerId}/loaded`, "PUT", { materials: mats });
    toast("Carga actualizada");
    loadPrinters();
  };
  row.querySelector(`[data-cancel-load]`).onclick = () => (row.hidden = true);
}

function printerForm(p = {}) {
  const host = document.getElementById("printer-form-host");
  host.innerHTML = "";
  const fields = PRINTER_FIELDS.map(([k, lbl, t, def]) =>
    `<label class="field"><span>${lbl}</span><input data-k="${k}" type="${t}" value="${p[k] ?? def ?? ""}" /></label>`).join("");
  const multi = p.multicolor === true;
  const form = el(`<div class="card" style="margin-top:1rem">
    <h2 style="margin-top:0">${p.id ? "Editar" : "Nueva"} impresora</h2>
    <div class="form-grid">${fields}</div>
    <label class="field"><span>Activa</span><select data-k="enabled">
      <option value="true" ${p.enabled !== false ? "selected" : ""}>Sí</option>
      <option value="false" ${p.enabled === false ? "selected" : ""}>No</option></select></label>
    <label class="inline-check"><input type="checkbox" id="pf-multi" ${multi ? "checked" : ""}>
      Multicolor (varias bobinas a la vez, tipo CFS)</label>
    <label class="field" id="pf-slots-wrap" ${multi ? "" : "hidden"} style="max-width:220px">
      <span>Número de colores (1–16)</span>
      <input type="number" id="pf-slots" min="1" max="16" value="${p.slot_count || 1}"></label>
    <div class="row-actions"><button class="btn" id="save-printer">Guardar</button>
      <button class="btn ghost" id="cancel-printer">Cancelar</button></div></div>`);
  host.appendChild(form);

  const multiChk = form.querySelector("#pf-multi");
  const slotsWrap = form.querySelector("#pf-slots-wrap");
  const slotsInput = form.querySelector("#pf-slots");
  multiChk.addEventListener("change", () => {
    slotsWrap.hidden = !multiChk.checked;
    if (!multiChk.checked) slotsInput.value = 1;         // sin multicolor, un hueco
    else if (+slotsInput.value <= 1) slotsInput.value = 4;  // por defecto un CFS
  });

  form.querySelector("#cancel-printer").onclick = () => (host.innerHTML = "");
  form.querySelector("#save-printer").onclick = async () => {
    const body = {};
    form.querySelectorAll("[data-k]").forEach((i) => {
      const k = i.dataset.k;
      if (k === "enabled") body[k] = i.value === "true";
      else if (i.type === "number") body[k] = parseFloat(i.value) || 0;
      else body[k] = i.value || null;
    });
    body.multicolor = multiChk.checked;
    body.slot_count = multiChk.checked ? Math.min(16, Math.max(1, parseInt(slotsInput.value) || 1)) : 1;
    try {
      await api.send(p.id ? `/api/printers/${p.id}` : "/api/printers", p.id ? "PUT" : "POST", body);
      host.innerHTML = ""; toast("Guardado"); loadPrinters();
    } catch (e) { toast("Error: " + e.message); }
  };
}
document.getElementById("add-printer").addEventListener("click", () => printerForm());

// --- Materiales --------------------------------------------------------------
const MATERIAL_FIELDS = [
  ["name", "Nombre", "text"],
  ["material_type", "Tipo (PLA, PETG…)", "text"],
  ["brand", "Marca", "text"],
  ["color", "Color", "text"],
  ["price_per_kg", "Precio /kg", "number"],
  ["density_g_cm3", "Densidad g/cm³", "number"],
  ["stock_level", "Stock (bobina)", "stock"],
  ["purchase_url", "Enlace de compra", "url"],
];

// Nivel de bobina: etiqueta y color del indicador.
const STOCK = {
  full:    ["Completa", "var(--good)"],
  half:    ["A medias", "var(--warn)"],
  low:     ["< 100 g", "var(--danger)"],
  empty:   ["Agotada", "var(--muted)"],
  unknown: ["—", "var(--border)"],
};
const stockDot = (lvl) => {
  const [txt, col] = STOCK[lvl] || STOCK.unknown;
  return `<span class="swatch" style="background:${col}"></span>${txt}`;
};

async function loadMaterials() {
  materials = await api.get("/api/materials");

  // Desplegables poblados con lo que hay (mismo criterio que en Calibración).
  const opts = (sel, valores) => {
    const el = document.getElementById(sel);
    if (!el) return;
    const actual = el.value;
    el.innerHTML = el.querySelector("option").outerHTML +
      [...new Set(valores)].filter(Boolean).sort()
        .map((v) => `<option value="${escHtml(v)}" ${v === actual ? "selected" : ""}>${escHtml(v)}</option>`).join("");
  };
  opts("mat-type", materials.map((m) => m.material_type));
  opts("mat-brand", materials.map((m) => m.brand));
  renderMaterialsTable();
}

function renderMaterialsTable() {
  const q = (document.getElementById("mat-search")?.value || "").toLowerCase();
  const tipo = document.getElementById("mat-type")?.value || "";
  const marca = document.getElementById("mat-brand")?.value || "";
  const stock = document.getElementById("mat-stock")?.value || "";
  const filas = materials.filter((m) =>
    (!tipo || m.material_type === tipo) &&
    (!marca || m.brand === marca) &&
    (!stock || (m.stock_level || "unknown") === stock) &&
    (!q || `${m.name} ${m.brand || ""} ${m.color || ""}`.toLowerCase().includes(q)));

  const cnt = document.getElementById("mat-count");
  if (cnt) cnt.textContent = `${filas.length} de ${materials.length} materiales`;

  document.getElementById("materials-table").innerHTML =
    `<tr><th>Nombre</th><th>Tipo</th><th>Marca</th><th>Color</th><th class="num">Precio/kg</th>
     <th>Stock</th><th></th><th></th></tr>` +
    (filas.length ? filas.map((m) => {
      const noPrice = (m.price_per_kg || 0) <= 0
        ? ` <span class="pill review">poner precio</span>` : "";
      const auto = m.auto_created ? ` <span class="pill" style="background:rgba(79,140,255,.15);color:var(--accent)">auto</span>` : "";
      const buy = m.purchase_url
        ? `<a class="btn ghost small" href="${escHtml(m.purchase_url)}" target="_blank" rel="noopener" title="Recomprar">🛒</a>` : "";
      const sw = m.color_hex ? `<span class="swatch" style="background:${escHtml(m.color_hex)}"></span>` : "";
      return `<tr>
        <td>${sw}${escHtml(m.name)}${auto}</td><td>${escHtml(m.material_type)}</td>
        <td class="muted">${escHtml(m.brand || "—")}</td><td class="muted">${escHtml(m.color || "—")}</td>
        <td class="num">${money(m.price_per_kg)}${noPrice}</td>
        <td>${stockDot(m.stock_level)}</td>
        <td>${buy}</td>
        <td class="row-actions">
          <button class="btn ghost small" data-edit-mat="${m.id}">Editar</button>
          <button class="btn danger small" data-del-mat="${m.id}">✕</button>
        </td></tr>`;
    }).join("") : `<tr><td colspan="8" class="muted">Sin materiales con ese filtro</td></tr>`);

  document.querySelectorAll("[data-edit-mat]").forEach((b) =>
    b.addEventListener("click", () => materialForm(materials.find((m) => m.id == b.dataset.editMat))));
  document.querySelectorAll("[data-del-mat]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("¿Borrar material?")) return;
      await api.send(`/api/materials/${b.dataset.delMat}`, "DELETE"); loadMaterials();
    }));
}

function materialField(k, lbl, t, m) {
  if (t === "stock") {
    const cur = m[k] || "unknown";
    const ops = Object.entries(STOCK).map(([v, [txt]]) =>
      `<option value="${v}" ${v === cur ? "selected" : ""}>${txt}</option>`).join("");
    return `<label class="field"><span>${lbl}</span><select data-k="${k}">${ops}</select></label>`;
  }
  const type = t === "url" ? "url" : t;
  const ph = t === "url" ? ' placeholder="https://tienda.com/…"' : "";
  return `<label class="field"><span>${lbl}</span>
    <input data-k="${k}" type="${type}"${ph} value="${escHtml(m[k] ?? (t === "number" ? 0 : ""))}" /></label>`;
}

// --- Fotos del filamento -----------------------------------------------------
// Se reescala en el navegador antes de subir: una foto de móvil de 4 MB se
// queda en ~200 KB, así la BD no engorda y el envío es instantáneo.
function fileToDataUri(file, maxDim = 1280, quality = 0.82) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const escala = Math.min(1, maxDim / Math.max(img.width, img.height));
      const cv = document.createElement("canvas");
      cv.width = Math.round(img.width * escala);
      cv.height = Math.round(img.height * escala);
      cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
      URL.revokeObjectURL(img.src);
      resolve(cv.toDataURL("image/jpeg", quality));
    };
    img.onerror = () => reject(new Error("No se pudo leer la imagen"));
    img.src = URL.createObjectURL(file);
  });
}

async function renderPhotos(materialId, host) {
  let fotos = [];
  try { fotos = await api.get(`/api/materials/${materialId}/photos`); } catch { /* sin fotos */ }
  const spool = fotos.filter((f) => f.kind === "spool");
  const color = fotos.filter((f) => f.kind === "color");
  const tile = (f) => `<div class="photo-tile">
    <img src="${f.url}" alt="">
    <button type="button" class="photo-del" data-del-photo="${f.id}" title="Quitar">✕</button>
  </div>`;

  host.innerHTML = `
    <div class="photo-group">
      <div class="photo-head"><strong>Bobina</strong> <span class="muted">${spool.length}/2</span>
        <button type="button" class="btn ghost small" id="add-spool" ${spool.length >= 2 ? "disabled" : ""}>+ Fotos de bobina</button>
        <input type="file" id="spool-file" accept="image/*" multiple hidden>
      </div>
      <div class="photo-row">${spool.map(tile).join("") || '<span class="muted">Sin fotos</span>'}</div>
    </div>
    <div class="photo-group">
      <div class="photo-head"><strong>Color</strong> <span class="muted">${color.length}/1</span>
        <button type="button" class="btn ghost small" id="add-color">🎨 Foto para extraer color</button>
        <input type="file" id="color-file" accept="image/*" hidden>
      </div>
      <div class="photo-row">${color.map(tile).join("") || '<span class="muted">Sin foto de color</span>'}</div>
    </div>`;

  host.querySelectorAll("[data-del-photo]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api.send(`/api/materials/${materialId}/photos/${b.dataset.delPhoto}`, "DELETE");
      renderPhotos(materialId, host);
    }));

  // Botón 1: hasta 2 fotos de bobina, sin cuentagotas.
  const spoolBtn = host.querySelector("#add-spool");
  const spoolInput = host.querySelector("#spool-file");
  if (spoolBtn) spoolBtn.addEventListener("click", () => spoolInput.click());
  spoolInput.addEventListener("change", async () => {
    const libres = 2 - spool.length;
    const files = [...spoolInput.files].slice(0, libres);
    spoolInput.value = "";
    for (const f of files) {
      try {
        await api.send(`/api/materials/${materialId}/photos`, "POST",
          { kind: "spool", data_uri: await fileToDataUri(f) });
      } catch (e) { toast("Error subiendo: " + e.message); }
    }
    renderPhotos(materialId, host);
  });

  // Botón 2: foto de color → cuentagotas → guarda hex y sube la foto.
  const colorBtn = host.querySelector("#add-color");
  const colorInput = host.querySelector("#color-file");
  colorBtn.addEventListener("click", () => {
    if (color.length >= 1) { toast("Ya hay una foto de color; bórrala primero"); return; }
    colorInput.click();
  });
  colorInput.addEventListener("change", async () => {
    const file = colorInput.files[0];
    colorInput.value = "";
    if (!file) return;
    const dataUri = await fileToDataUri(file);
    openEyedropper(dataUri, async (hex) => {
      const hexInput = document.querySelector("#material-form-host [data-k='color_hex']");
      if (hexInput) { hexInput.value = hex; syncHexSwatch(); }
      try {
        await api.send(`/api/materials/${materialId}/photos`, "POST",
          { kind: "color", data_uri: dataUri });
      } catch (e) { toast("Error subiendo: " + e.message); }
      renderPhotos(materialId, host);
      toast(`Color aproximado: ${hex} — recuerda Guardar`);
    });
  });
}

// Cuentagotas: pinta la foto en un canvas y devuelve el hex del punto tocado,
// promediando un vecindario pequeño para que no dependa de un píxel con ruido.
function openEyedropper(dataUri, onPick) {
  const ov = el(`<div class="eyedrop-ov">
    <div class="eyedrop-box">
      <p class="muted">Toca sobre la foto el color de la bobina.</p>
      <div class="eyedrop-canvas-wrap"><canvas id="eyedrop-cv"></canvas></div>
      <div class="eyedrop-foot">
        <span class="eyedrop-preview"><span class="swatch" id="eyedrop-sw"></span><span id="eyedrop-hex">—</span></span>
        <span class="spacer"></span>
        <button class="btn ghost" id="eyedrop-cancel">Cancelar</button>
        <button class="btn" id="eyedrop-ok" disabled>Usar este color</button>
      </div>
    </div>
  </div>`);
  document.body.appendChild(ov);

  const cv = ov.querySelector("#eyedrop-cv");
  const ctx = cv.getContext("2d", { willReadFrequently: true });
  const img = new Image();
  let picked = null;

  img.onload = () => {
    const maxW = Math.min(560, img.width);
    const escala = maxW / img.width;
    cv.width = maxW;
    cv.height = Math.round(img.height * escala);
    ctx.drawImage(img, 0, 0, cv.width, cv.height);
  };
  img.src = dataUri;

  const muestrear = (ev) => {
    const r = cv.getBoundingClientRect();
    const pt = ev.touches ? ev.touches[0] : ev;
    const x = Math.round((pt.clientX - r.left) * (cv.width / r.width));
    const y = Math.round((pt.clientY - r.top) * (cv.height / r.height));
    // Media de un cuadrado de 5×5 alrededor del punto.
    let rs = 0, gs = 0, bs = 0, n = 0;
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
      const px = x + dx, py = y + dy;
      if (px < 0 || py < 0 || px >= cv.width || py >= cv.height) continue;
      const d = ctx.getImageData(px, py, 1, 1).data;
      rs += d[0]; gs += d[1]; bs += d[2]; n++;
    }
    if (!n) return;
    const hx = (v) => Math.round(v / n).toString(16).padStart(2, "0");
    picked = `#${hx(rs)}${hx(gs)}${hx(bs)}`;
    ov.querySelector("#eyedrop-sw").style.background = picked;
    ov.querySelector("#eyedrop-hex").textContent = picked;
    ov.querySelector("#eyedrop-ok").disabled = false;
  };
  cv.addEventListener("click", muestrear);
  cv.addEventListener("touchstart", (e) => { e.preventDefault(); muestrear(e); }, { passive: false });

  const cerrar = () => ov.remove();
  ov.querySelector("#eyedrop-cancel").onclick = cerrar;
  ov.querySelector("#eyedrop-ok").onclick = () => { if (picked) onPick(picked); cerrar(); };
  ov.addEventListener("click", (e) => { if (e.target === ov) cerrar(); });
}

function syncHexSwatch() {
  const inp = document.querySelector("#material-form-host [data-k='color_hex']");
  const sw = document.querySelector("#material-form-host .hex-swatch");
  if (inp && sw) sw.style.background = /^#[0-9a-fA-F]{6}$/.test(inp.value) ? inp.value : "transparent";
}

function materialForm(m = {}) {
  const host = document.getElementById("material-form-host");
  host.innerHTML = "";
  const fields = MATERIAL_FIELDS.map(([k, lbl, t]) => materialField(k, lbl, t, m)).join("");
  const hex = m.color_hex || "";
  const colorField = `<label class="field"><span>Color (hex)</span>
    <span class="hex-field"><span class="swatch hex-swatch" style="background:${/^#[0-9a-fA-F]{6}$/.test(hex) ? hex : "transparent"}"></span>
    <input data-k="color_hex" type="text" placeholder="#RRGGBB" value="${escHtml(hex)}"></span></label>`;
  const photos = m.id
    ? `<h3 class="pie-title">Fotos de referencia</h3><div id="mat-photos"></div>`
    : `<p class="muted" style="margin-top:1rem">Guarda el material para poder añadir fotos.</p>`;

  const form = el(`<div class="card" style="margin-top:1rem">
    <h2 style="margin-top:0">${m.id ? "Editar" : "Nuevo"} material</h2>
    <div class="form-grid">${fields}${colorField}</div>
    ${photos}
    <div class="row-actions" style="margin-top:1rem"><button class="btn" id="save-mat">Guardar</button>
      <button class="btn ghost" id="cancel-mat">Cancelar</button></div></div>`);
  host.appendChild(form);
  if (m.id) renderPhotos(m.id, form.querySelector("#mat-photos"));
  form.querySelector("[data-k='color_hex']").addEventListener("input", syncHexSwatch);
  form.querySelector("#cancel-mat").onclick = () => (host.innerHTML = "");
  form.querySelector("#save-mat").onclick = async () => {
    const body = { active: true };
    form.querySelectorAll("[data-k]").forEach((i) => {
      const k = i.dataset.k;
      body[k] = i.type === "number"
        ? (parseFloat(i.value) || 0)
        : (i.value || (["brand", "color", "purchase_url"].includes(k) ? null : ""));
    });
    try {
      await api.send(m.id ? `/api/materials/${m.id}` : "/api/materials", m.id ? "PUT" : "POST", body);
      host.innerHTML = ""; toast("Guardado"); loadMaterials();
    } catch (e) { toast("Error: " + e.message); }
  };
}
document.getElementById("add-material").addEventListener("click", () => materialForm());
document.getElementById("mat-search").addEventListener("input", renderMaterialsTable);
["mat-type", "mat-brand", "mat-stock"].forEach((id) =>
  document.getElementById(id).addEventListener("change", renderMaterialsTable));
document.getElementById("mat-clear").addEventListener("click", () => {
  ["mat-search", "mat-type", "mat-brand", "mat-stock"].forEach((id) =>
    (document.getElementById(id).value = ""));
  renderMaterialsTable();
});

// --- Estimación + Presupuesto ------------------------------------------------
let lastEstimate = null;

async function loadQuote() {
  if (!printers.length) printers = await api.get("/api/printers");
  const sel = document.getElementById("q-printer");
  sel.innerHTML = printers.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
  await loadQuoteFiles();
}

async function loadQuoteFiles() {
  const pid = document.getElementById("q-printer").value;
  const source = document.getElementById("q-source").value;
  const fsel = document.getElementById("q-file");
  fsel.innerHTML = `<option>cargando…</option>`;
  const endpoint = source === "live"
    ? `/api/printers/${pid}/files`               // en vivo desde Moonraker
    : `/api/printers/${pid}/history-files`;      // desde la BD (offline)
  let files = null;
  try { files = await api.get(endpoint); } catch (e) { files = null; }
  // Un 502 devuelve {detail:...} (no un array): la impresora está apagada/sin red.
  if (!Array.isArray(files)) {
    fsel.innerHTML = `<option value="">(impresora apagada o sin conexión)</option>`;
    return;
  }
  fsel.innerHTML = files.length
    ? files.map((f) => `<option value="${f.path}">${f.path}</option>`).join("")
    : `<option value="">${source === "live" ? "(sin gcodes en la impresora)" : "(sin impresiones en el historial)"}</option>`;
}
document.getElementById("q-printer").addEventListener("change", loadQuoteFiles);
document.getElementById("q-source").addEventListener("change", loadQuoteFiles);

document.getElementById("q-estimate").addEventListener("click", async () => {
  const pid = document.getElementById("q-printer").value;
  const file = document.getElementById("q-file").value;
  const source = document.getElementById("q-source").value;
  if (!file) return toast("Selecciona un archivo");
  const host = document.getElementById("q-estimate-result");
  host.innerHTML = `<p class="muted">Estimando…</p>`;
  try {
    const e = await api.get(`/api/printers/${pid}/estimate?filename=${encodeURIComponent(file)}&source=${source}`);
    if (!e || !e.cost) throw new Error(e && e.detail ? e.detail : "sin datos");
    lastEstimate = e;
    currency = e.currency;
    const c = e.cost;
    const srcLabels = { "impresora+tipo": "impresora + material", "tipo": "por material",
      "impresora": "por impresora", "defecto": "valor por defecto" };
    const pw = "(" + (srcLabels[e.avg_power_source] || e.avg_power_source) + ")";
    host.innerHTML = `<table style="margin-top:0.8rem">
      <tr><td>Tiempo estimado</td><td class="num">${fmtDur(e.estimated_time_s)}</td></tr>
      <tr><td>Filamento</td><td class="num">${e.filament_g.toFixed(0)} g ${e.material ? "(" + e.material + ")" : "(sin material)"}</td></tr>
      <tr><td>Energía (≈${e.avg_power_w} W ${pw})</td><td class="num">${e.energy_kwh} kWh</td></tr>
      <tr><td>Coste electricidad</td><td class="num">${money(c.energy)}</td></tr>
      <tr><td>Coste filamento</td><td class="num">${money(c.filament)}</td></tr>
      <tr><td>Amortización</td><td class="num">${money(c.depreciation)}</td></tr>
      <tr><td><strong>Coste base por unidad</strong></td><td class="num"><strong>${money(c.total)}</strong></td></tr>
    </table>`;
    document.getElementById("q-base").value = c.total;
  } catch (e) {
    host.innerHTML = `<p class="muted">No se pudo estimar (¿impresora accesible? ¿gcode con metadatos?)</p>`;
  }
});

function computeQuote() {
  const base = parseFloat(document.getElementById("q-base").value) || 0;
  const qty = Math.max(1, parseInt(document.getElementById("q-qty").value) || 1);
  const labor = parseFloat(document.getElementById("q-labor").value) || 0;
  const post = parseFloat(document.getElementById("q-post").value) || 0;
  const failure = parseFloat(document.getElementById("q-failure").value) || 0;
  const margin = parseFloat(document.getElementById("q-margin").value) || 0;
  const baseFail = base * (1 + failure / 100);          // recargo por fallo
  const subtotal = baseFail + labor + post;             // + mano de obra y extras
  const unit = subtotal * (1 + margin / 100);           // + margen
  return { base, qty, labor, post, failure, margin, baseFail, subtotal, unit, total: unit * qty };
}

function renderQuote(q) {
  document.getElementById("q-quote-result").innerHTML = `<table style="margin-top:0.8rem">
    <tr><td>Coste base</td><td class="num">${money(q.base)}</td></tr>
    <tr><td>+ recargo fallo (${q.failure}%)</td><td class="num">${money(q.baseFail)}</td></tr>
    <tr><td>+ mano de obra</td><td class="num">${money(q.labor)}</td></tr>
    <tr><td>+ post-procesado</td><td class="num">${money(q.post)}</td></tr>
    <tr><td>Subtotal por unidad</td><td class="num">${money(q.subtotal)}</td></tr>
    <tr><td>+ margen (${q.margin}%)</td><td class="num">${money(q.unit)}</td></tr>
    <tr><td><strong>Precio unitario</strong></td><td class="num"><strong>${money(q.unit)}</strong></td></tr>
    <tr><td><strong>TOTAL (${q.qty} ud.)</strong></td><td class="num"><strong>${money(q.total)}</strong></td></tr>
  </table>`;
}
document.getElementById("q-calc").addEventListener("click", () => renderQuote(computeQuote()));

document.getElementById("q-print").addEventListener("click", () => {
  const q = computeQuote();
  const s = appSettings || {};
  const item = lastEstimate
    ? (lastEstimate.filename || "").split("/").pop()
    : "Trabajo de impresión 3D";

  printDoc("Presupuesto", `
    ${brandHead(s, "Impresión 3D", "Presupuesto")}
    <div class="meta">
      <span><strong>Trabajo:</strong> ${escHtml(item)}</span>
      <span><strong>Fecha:</strong> ${new Date().toLocaleDateString()}</span>
      <span><strong>Cantidad:</strong> ${q.qty}</span>
    </div>
    <table>
      <tr><th>Concepto</th><th class="num">Importe</th></tr>
      <tr><td>Coste base de fabricación</td><td class="num">${money(q.base)}</td></tr>
      <tr><td>Recargo por fallo (${q.failure}%)</td><td class="num">${money(q.baseFail - q.base)}</td></tr>
      <tr><td>Mano de obra</td><td class="num">${money(q.labor)}</td></tr>
      <tr><td>Post-procesado y extras</td><td class="num">${money(q.post)}</td></tr>
      <tr><td>Margen (${q.margin}%)</td><td class="num">${money(q.unit - q.subtotal)}</td></tr>
    </table>
    <table class="totals">
      <tr><td>Precio unitario</td><td class="num">${money(q.unit)}</td></tr>
      <tr><td>Cantidad</td><td class="num">${q.qty}</td></tr>
      <tr class="grand"><td>Total</td><td class="num">${money(q.total)}</td></tr>
    </table>
    ${brandTerms(s)}
    <div class="foot">${escHtml(s.company_name || "M3D Nexus")} — Transforma tus ideas en realidad con impresión 3D</div>
  `);
});

// --- Pedidos -----------------------------------------------------------------
// El estado de impresión lo deduce el servidor cruzando cada gcode con lo que
// imprime la máquina; aquí solo se pinta y se refresca en vivo.

const ORDER_STATUS = {
  draft:     ["Borrador", "var(--muted)"],
  queued:    ["En cola", "var(--accent)"],
  printing:  ["Imprimiendo", "var(--good)"],
  partial:   ["Parcial", "var(--warn)"],
  printed:   ["Listo", "var(--good)"],
  delivered: ["Entregado", "var(--muted)"],
  cancelled: ["Cancelado", "var(--danger)"],
  on_hold:   ["En espera", "var(--warn)"],
};
const ITEM_STATUS = {
  unassigned: ["sin asignar", "var(--muted)"],
  queued:     ["en cola", "var(--accent)"],
  printing:   ["imprimiendo", "var(--good)"],
  partial:    ["parcial", "var(--warn)"],
  printed:    ["impreso", "var(--good)"],
  failed:     ["fallido", "var(--danger)"],
  done:       ["hecho", "var(--good)"],
};
const PAY_STATUS = { pending: "Pendiente", deposit: "Anticipo", paid: "Completo" };
const DUE_RANK = { "vencido": 0, "vence hoy": 1, "mañana": 2 };

let orders = [];

function statusPill(map, key) {
  const [txt, col] = map[key] || ["—", "var(--muted)"];
  return `<span class="pill" style="background:${col}22;color:${col}">${txt}</span>`;
}

function dueLabel(due) {
  if (!due) return "";
  const days = Math.ceil((new Date(due) - new Date()) / 86400000);
  if (days < 0) return `<span class="due due-over">vencido ${-days}d</span>`;
  if (days === 0) return `<span class="due due-today">vence hoy</span>`;
  if (days === 1) return `<span class="due due-soon">mañana</span>`;
  if (days <= 3) return `<span class="due due-soon">en ${days} días</span>`;
  return `<span class="due muted">en ${days} días</span>`;
}
const dueSort = (o) => o.due_date ? new Date(o.due_date).getTime() : 8.64e15;

async function loadPedidos() {
  await ensureRefs();
  orders = await api.get("/api/orders");
  renderOrders();
}

function renderOrders() {
  const filter = document.getElementById("ped-filter").value;
  const q = (document.getElementById("ped-search").value || "").toLowerCase();
  const open = (s) => !["delivered", "cancelled"].includes(s);
  let rows = orders.filter((o) => {
    if (filter === "open" && !open(o.status)) return false;
    if (filter === "printing" && o.status !== "printing") return false;
    if (filter === "printed" && o.status !== "printed") return false;
    if (filter === "delivered" && o.status !== "delivered") return false;
    return !q || `${o.client} ${o.description || ""}`.toLowerCase().includes(q);
  });
  // Urgencia primero: vencidos y próximos arriba.
  rows.sort((a, b) => dueSort(a) - dueSort(b));

  const host = document.getElementById("orders-board");
  if (!rows.length) { host.innerHTML = `<p class="muted">Sin pedidos.</p>`; return; }

  host.innerHTML = rows.map((o) => {
    const cur = o.currency || currency;
    const items = o.items.map((it) => {
      const prog = it.status === "printing" && it.progress != null
        ? `<div class="progress mini"><div class="bar" style="width:${Math.round(it.progress * 100)}%"></div><span>${Math.round(it.progress * 100)}%</span></div>` : "";
      const copies = it.quantity > 1 ? ` <span class="muted">${it.printed}/${it.quantity}</span>` : "";
      return `<div class="order-item">
        <span class="oi-name" title="${escHtml(it.gcode_filename || "")}">${escHtml(it.label || (it.gcode_filename || "sin gcode").split("/").pop())}</span>
        <span class="muted">${escHtml(it.printer_name || "—")}</span>
        ${statusPill(ITEM_STATUS, it.status)}${copies}
        ${prog}
      </div>`;
    }).join("");

    const margin = o.margin
      ? `<span class="muted" title="coste estimado ${money2(o.margin.cost, cur)}">margen ${money2(o.margin.profit, cur)} (${o.margin.margin_pct}%)</span>`
      : (o.agreed_price != null ? `<span class="muted">precio ${money2(o.agreed_price, cur)}</span>` : "");

    return `<div class="card order-card">
      <div class="order-head">
        <strong>#${o.id} · ${escHtml(o.client)}</strong>
        ${statusPill(ORDER_STATUS, o.status)}
        <span class="pill" style="background:var(--panel-2)">${PAY_STATUS[o.payment_status] || o.payment_status}</span>
        ${dueLabel(o.due_date)}
        <span class="spacer"></span>
        ${margin}
        <button class="btn ghost small" data-edit-order="${o.id}">Editar</button>
        <button class="btn danger small" data-del-order="${o.id}">✕</button>
      </div>
      ${o.description ? `<div class="muted order-desc">${escHtml(o.description)}</div>` : ""}
      ${o.folder_path ? `<div class="order-folder muted">📁 <code>${escHtml(o.folder_path)}</code>
        <button class="btn ghost small" data-copy-path="${escHtml(o.folder_path)}" title="Copiar ruta">Copiar</button></div>`
        : (o.folder ? `<div class="order-folder muted">📁 carpeta ${escHtml(o.folder)} <span class="muted">(pon la ruta base en Ajustes)</span></div>` : "")}
      <div class="order-items">${items || '<span class="muted">Sin piezas</span>'}</div>
    </div>`;
  }).join("");

  host.querySelectorAll("[data-copy-path]").forEach((b) =>
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(b.dataset.copyPath); toast("Ruta copiada"); }
      catch { toast("No se pudo copiar"); }
    }));

  host.querySelectorAll("[data-edit-order]").forEach((b) =>
    b.addEventListener("click", () => orderForm(orders.find((o) => o.id == b.dataset.editOrder))));
  host.querySelectorAll("[data-del-order]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("¿Borrar pedido?")) return;
      await api.send(`/api/orders/${b.dataset.delOrder}`, "DELETE"); loadPedidos();
    }));
}

function money2(v, cur) { return `${(v ?? 0).toFixed(2)} ${cur || currency}`; }

// --- Formulario de pedido con editor de piezas -------------------------------
function orderForm(o = {}) {
  const host = document.getElementById("order-form-host");
  const pay = o.payment_status || "pending";
  const oms = o.manual_status || "";
  const due = o.due_date ? new Date(o.due_date).toISOString().slice(0, 10) : "";

  host.innerHTML = `<div class="card" style="margin-top:1rem">
    <h2 style="margin-top:0">${o.id ? "Editar" : "Nuevo"} pedido ${o.id ? "#" + o.id : ""}</h2>
    <div class="form-grid">
      <label class="field"><span>Cliente</span><input id="of-client" value="${escHtml(o.client || "")}"></label>
      <label class="field"><span>Descripción</span><input id="of-desc" value="${escHtml(o.description || "")}"></label>
      <label class="field"><span>Estado de pago</span><select id="of-pay">
        ${Object.entries(PAY_STATUS).map(([k, v]) => `<option value="${k}" ${k === pay ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="field"><span>Precio acordado</span><input type="number" step="0.01" id="of-price" value="${o.agreed_price ?? ""}"></label>
      <label class="field"><span>Fecha de entrega</span><input type="date" id="of-due" value="${due}"></label>
      <label class="field"><span>Carpeta local (nº)</span><input id="of-folder" placeholder="${o.id || "p.ej. 112"}" value="${escHtml(o.folder || "")}"></label>
      <label class="field"><span>Estado manual</span><select id="of-manual">
        <option value="">— automático —</option>
        <option value="on_hold" ${oms === "on_hold" ? "selected" : ""}>En espera</option>
        <option value="delivered" ${oms === "delivered" ? "selected" : ""}>Entregado</option>
        <option value="cancelled" ${oms === "cancelled" ? "selected" : ""}>Cancelado</option>
      </select></label>
    </div>
    <div class="toolbar" style="margin:.4rem 0"><strong>Piezas</strong>
      <span class="spacer"></span>
      <button class="btn ghost small" id="of-add-item">+ Añadir pieza</button></div>
    <div id="of-items"></div>
    <div class="row-actions" style="margin-top:1rem">
      <button class="btn" id="of-save">Guardar</button>
      <button class="btn ghost" id="of-cancel">Cancelar</button></div>
  </div>`;

  const itemsHost = host.querySelector("#of-items");
  (o.items && o.items.length ? o.items : [{}]).forEach((it) => addOrderItem(itemsHost, it));

  host.querySelector("#of-add-item").onclick = () => addOrderItem(itemsHost, {});
  host.querySelector("#of-cancel").onclick = () => (host.innerHTML = "");
  host.querySelector("#of-save").onclick = () => saveOrder(o.id, host);
  host.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function addOrderItem(host, it) {
  const row = el(`<div class="card oi-editor">
    <div class="form-grid">
      <label class="field"><span>Etiqueta (opcional)</span><input class="oi-label" value="${escHtml(it.label || "")}"></label>
      <label class="field"><span>Impresora</span><select class="oi-printer">
        <option value="">— elegir —</option>
        ${printers.map((p) => `<option value="${p.id}" ${p.id === it.printer_id ? "selected" : ""}>${escHtml(p.name)}</option>`).join("")}
      </select></label>
      <label class="field"><span>Gcode en la impresora</span><select class="oi-gcode"><option value="">— elige impresora —</option></select></label>
      <label class="field"><span>Cantidad</span><input type="number" min="1" class="oi-qty" value="${it.quantity || 1}"></label>
    </div>
    <div class="row-actions"><span class="oi-status muted"></span><span class="spacer"></span>
      <button type="button" class="btn ghost small oi-remove">Quitar pieza</button></div>
  </div>`);
  host.appendChild(row);

  const printerSel = row.querySelector(".oi-printer");
  const gcodeSel = row.querySelector(".oi-gcode");
  row._current = it.gcode_filename || "";
  printerSel.addEventListener("change", () => loadOrderGcodes(row));
  if (it.printer_id) loadOrderGcodes(row);
  row.querySelector(".oi-remove").onclick = () => {
    if (host.children.length > 1) row.remove();
    else toast("Un pedido necesita al menos una pieza");
  };
}

// Buscador de gcode de la impresora elegida (en vivo, con fallback al historial).
async function loadOrderGcodes(row) {
  const pid = row.querySelector(".oi-printer").value;
  const sel = row.querySelector(".oi-gcode");
  const status = row.querySelector(".oi-status");
  if (!pid) { sel.innerHTML = `<option value="">— elige impresora —</option>`; return; }
  sel.innerHTML = `<option value="">cargando…</option>`;
  const list = async (url) => { const r = await api.get(url); return Array.isArray(r) ? r : null; };
  let files = await list(`/api/printers/${pid}/files`);
  let src = "impresora";
  if (!files || !files.length) {
    const hist = await list(`/api/printers/${pid}/history-files`);
    if (hist && hist.length) { files = hist; src = "historial"; }
  }
  const cur = row._current;
  sel.innerHTML = `<option value="">— elige gcode —</option>` +
    (files || []).map((f) => `<option value="${escHtml(f.path)}" ${f.path === cur ? "selected" : ""}>${escHtml(f.path)}</option>`).join("");
  if (cur && !(files || []).some((f) => f.path === cur)) {
    // El gcode ligado ya no está en la impresora: se conserva igualmente.
    sel.innerHTML += `<option value="${escHtml(cur)}" selected>${escHtml(cur)} (ya no está en la impresora)</option>`;
  }
  status.textContent = files && files.length
    ? `${files.length} gcodes (${src})`
    : "impresora apagada y sin historial";
}

async function saveOrder(id, host) {
  const items = [...host.querySelectorAll(".oi-editor")].map((row) => ({
    label: row.querySelector(".oi-label").value || null,
    printer_id: row.querySelector(".oi-printer").value ? +row.querySelector(".oi-printer").value : null,
    gcode_filename: row.querySelector(".oi-gcode").value || null,
    quantity: Math.max(1, parseInt(row.querySelector(".oi-qty").value) || 1),
  }));
  const client = host.querySelector("#of-client").value.trim();
  if (!client) { toast("El pedido necesita un cliente"); return; }
  const due = host.querySelector("#of-due").value;
  const price = host.querySelector("#of-price").value;
  const body = {
    client,
    description: host.querySelector("#of-desc").value || null,
    payment_status: host.querySelector("#of-pay").value,
    agreed_price: price ? parseFloat(price) : null,
    due_date: due ? new Date(due).toISOString() : null,
    manual_status: host.querySelector("#of-manual").value || null,
    folder: host.querySelector("#of-folder").value.trim() || null,
    items,
  };
  try {
    await api.send(id ? `/api/orders/${id}` : "/api/orders", id ? "PUT" : "POST", body);
    host.innerHTML = ""; toast("Pedido guardado"); loadPedidos();
  } catch (e) { toast("Error: " + e.message); }
}

// --- Cola por impresora ------------------------------------------------------
async function toggleQueue() {
  const host = document.getElementById("ped-queue");
  if (!host.hidden) { host.hidden = true; return; }
  const q = await api.get("/api/orders/queue");
  host.innerHTML = q.length
    ? `<div class="cards">` + q.map((col) => `<div class="card">
        <strong>${escHtml(col.printer)}</strong>
        <div class="queue-items">${col.items.map((i) =>
          `<div class="order-item">${statusPill(ITEM_STATUS, i.status)}
            <span>#${i.order_id} ${escHtml(i.label || "")}</span>
            <span class="muted">${escHtml(i.client)}</span></div>`).join("")}</div>
      </div>`).join("") + `</div>`
    : `<p class="muted">Nada en cola.</p>`;
  host.hidden = false;
}

document.getElementById("add-order").addEventListener("click", () => orderForm());
document.getElementById("ped-filter").addEventListener("change", renderOrders);
document.getElementById("ped-search").addEventListener("input", renderOrders);
document.getElementById("ped-queue-toggle").addEventListener("click", toggleQueue);
// Refresco en vivo del tablero mientras se mira (mismo ritmo que el dashboard).
setInterval(() => { if (currentTab === "pedidos" && !document.getElementById("order-form-host").innerHTML) loadPedidos(); }, 8000);

// --- Calibración de filamentos ----------------------------------------------
// Matriz de qué filamento está afinado en qué impresora, y el import desde los
// perfiles de OrcaSlicer. Los ficheros solo se leen en el navegador y se mandan
// tal cual: el parseo vive en el servidor, donde está cubierto por tests.

const CAL_NIVEL = {
  FULL:       ["completo", "Pressure advance y/o flow ratio calibrados"],
  BASIC:      ["temperaturas", "Solo temperaturas y velocidades"],
  CALIBRATED: ["histórico", "Deducido del perfil usado en las impresiones"],
  NOT_TUNED:  ["sin calibrar", "El propio perfil se declara sin afinar"],
  NONE:       ["sin tocar", "Hereda el perfil genérico"],
  UNKNOWN:    ["desconocido", "Sin datos para decidirlo"],
};

let calibrations = [];
let orcaPlan = [];          // filas propuestas, con su JSON crudo al lado

async function loadCalibracion() {
  await ensureRefs();
  calibrations = await api.get("/api/calibrations");

  // Los desplegables se pueblan con lo que hay en la matriz, no con el catálogo
  // entero: así solo se ofrecen impresoras y tipos que de verdad tienen filas.
  const opts = (sel, valores, actual) => {
    const el = document.getElementById(sel);
    el.innerHTML = el.querySelector("option").outerHTML +
      [...new Set(valores)].filter(Boolean).sort()
        .map((v) => `<option value="${escHtml(v)}" ${v === actual ? "selected" : ""}>${escHtml(v)}</option>`).join("");
  };
  opts("cal-printer", calibrations.map((c) => c.printer), document.getElementById("cal-printer").value);
  opts("cal-type", calibrations.map((c) => c.material_type), document.getElementById("cal-type").value);

  renderCalTable();
}

function calPill(status) {
  const [txt, title] = CAL_NIVEL[status] || CAL_NIVEL.UNKNOWN;
  return `<span class="pill cal-${status}" title="${escHtml(title)}">${txt}</span>`;
}

function renderCalTable() {
  const q = (document.getElementById("cal-search").value || "").toLowerCase();
  const nivel = document.getElementById("cal-status").value;
  const impresora = document.getElementById("cal-printer").value;
  const tipo = document.getElementById("cal-type").value;
  const filas = calibrations.filter((c) =>
    (!impresora || c.printer === impresora) &&
    (!tipo || c.material_type === tipo) &&
    (!nivel || c.status === nivel) &&
    (!q || `${c.material} ${c.printer} ${c.orca_profile || ""}`.toLowerCase().includes(q)));

  // Contexto de lo filtrado, p.ej. "3 PLA en Ender 3V2".
  const partes = [tipo, impresora && `en ${impresora}`].filter(Boolean).join(" ");
  document.getElementById("cal-count").textContent = partes
    ? `${filas.length} ${partes}`
    : `${filas.length} de ${calibrations.length} combinaciones`;

  const head = `<tr><th>Filamento</th><th>Impresora</th><th class="num">Boq.</th>
    <th>Estado</th><th class="num">PA</th><th class="num">Flow</th>
    <th class="num">Temp</th><th>Perfil de Orca</th><th class="num">Jobs</th><th></th></tr>`;

  document.getElementById("cal-table").innerHTML = head + (filas.length
    ? filas.map((c) => `<tr>
        <td>${c.color_hex ? `<span class="swatch" style="background:${escHtml(c.color_hex)}"></span>` : ""}${escHtml(c.material)}</td>
        <td>${escHtml(c.printer)}</td>
        <td class="num">${c.nozzle_mm}</td>
        <td>${calPill(c.status)}</td>
        <td class="num">${c.pressure_advance ?? "—"}</td>
        <td class="num">${c.flow_ratio ?? "—"}</td>
        <td class="num">${c.nozzle_temp ? c.nozzle_temp + "°" : "—"}</td>
        <td class="muted" title="${escHtml(c.orca_profile || "")}">${escHtml((c.orca_profile || "—").slice(0, 34))}</td>
        <td class="num">${c.jobs_seen || "—"}</td>
        <td><button class="btn danger small" data-del-cal="${c.id}">Quitar</button></td>
      </tr>`).join("")
    : `<tr><td colspan="10" class="muted">Sin datos. Importa tus perfiles de Orca o deduce del historial.</td></tr>`);

  document.querySelectorAll("[data-del-cal]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api.send(`/api/calibrations/${b.dataset.delCal}`, "DELETE");
      loadCalibracion();
    }));
}

document.getElementById("cal-search").addEventListener("input", renderCalTable);
["cal-status", "cal-printer", "cal-type"].forEach((id) =>
  document.getElementById(id).addEventListener("change", renderCalTable));
document.getElementById("cal-clear").addEventListener("click", () => {
  ["cal-search", "cal-status", "cal-printer", "cal-type"].forEach((id) =>
    (document.getElementById(id).value = ""));
  renderCalTable();
});

// --- Import desde OrcaSlicer -------------------------------------------------
document.getElementById("orca-pick").addEventListener("click", () =>
  document.getElementById("orca-files").click());

document.getElementById("orca-files").addEventListener("change", async (ev) => {
  const ficheros = [...ev.target.files].filter((f) => f.name.endsWith(".json"));
  ev.target.value = "";
  if (!ficheros.length) { toast("Esa carpeta no tiene perfiles .json"); return; }

  const host = document.getElementById("orca-plan");
  host.innerHTML = `<p class="muted">Leyendo ${ficheros.length} perfiles…</p>`;

  const perfiles = [];
  for (const f of ficheros) {
    try { perfiles.push(JSON.parse(await f.text())); }
    catch { /* un JSON roto no debe tumbar el import entero */ }
  }
  if (!perfiles.length) { host.innerHTML = `<p class="muted">Ningún .json legible.</p>`; return; }

  try {
    const r = await api.send("/api/orca/preview", "POST", { profiles: perfiles });
    // Se guarda el JSON crudo junto a cada fila para devolverlo al confirmar.
    orcaPlan = r.plan.map((fila) => ({
      fila,
      json: perfiles.find((p) => (p.name || "").replace(/^"|"$/g, "") === fila.profile),
    }));
    renderOrcaPlan(ficheros.length, perfiles.length);
  } catch (e) {
    host.innerHTML = `<p class="muted">No se pudo analizar: ${escHtml(e.message)}</p>`;
  }
});

function renderOrcaPlan(nFicheros, nPerfiles) {
  const host = document.getElementById("orca-plan");
  if (!orcaPlan.length) {
    host.innerHTML = `<p class="muted">Ninguno de los ${nFicheros} ficheros era un perfil de filamento.</p>`;
    return;
  }
  const optImpresoras = (sel) => `<option value="">— sin identificar —</option>` +
    printers.map((p) => `<option value="${p.id}" ${p.id === sel ? "selected" : ""}>${escHtml(p.name)}</option>`).join("");
  const optMateriales = (sel) => `<option value="">➕ crear nuevo</option>` +
    materials.map((m) => `<option value="${m.id}" ${m.id === sel ? "selected" : ""}>${escHtml(m.name)}</option>`).join("");

  const filas = orcaPlan.map(({ fila }, i) => {
    const avisos = fila.warnings.length
      ? `<div class="cal-warn">⚠ ${fila.warnings.map(escHtml).join(" · ")}</div>` : "";
    const marca = { exact: "", similar: `<span class="pill review">parecido ${Math.round(fila.match_score * 100)}%</span>`,
                    new: `<span class="pill">nuevo</span>` }[fila.match] || "";
    return `<tr data-row="${i}">
      <td><input type="checkbox" class="orca-ok" ${fila.printer_id && fila.nozzle_mm ? "checked" : ""}></td>
      <td>${fila.colour ? `<span class="swatch" style="background:${escHtml(fila.colour)}"></span>` : ""}
          ${escHtml(fila.filament_name)}${avisos}</td>
      <td><select class="orca-printer">${optImpresoras(fila.printer_id)}</select></td>
      <td class="num">${fila.nozzle_mm ?? "—"}</td>
      <td>${calPill(fila.level)}</td>
      <td class="num">${fila.pressure_advance ?? "—"}</td>
      <td class="num">${fila.flow_ratio ?? "—"}</td>
      <td><select class="orca-material">${optMateriales(fila.material_id)}</select> ${marca}</td>
    </tr>`;
  }).join("");

  const conAviso = orcaPlan.filter(({ fila }) => fila.warnings.length).length;
  host.innerHTML = `<div class="card">
    <div class="toolbar">
      <strong>${orcaPlan.length} perfiles de filamento</strong>
      <span class="muted">de ${nFicheros} ficheros${conAviso ? ` · ${conAviso} necesitan revisión` : ""}</span>
      <span class="spacer"></span>
      <button class="btn ghost small" id="orca-cancel">Cancelar</button>
      <button class="btn" id="orca-apply">Importar seleccionados</button>
    </div>
    <p class="muted" style="font-size:.8rem">Revisa la impresora y el material de cada fila.
      «Crear nuevo» añade un material; elegir uno existente lo reutiliza sin tocar su precio.</p>
    <table>
      <tr><th></th><th>Filamento</th><th>Impresora</th><th class="num">Boq.</th>
          <th>Nivel</th><th class="num">PA</th><th class="num">Flow</th><th>Material en la app</th></tr>
      ${filas}
    </table>
  </div>`;

  document.getElementById("orca-cancel").addEventListener("click", () => {
    orcaPlan = []; host.innerHTML = "";
  });
  document.getElementById("orca-apply").addEventListener("click", aplicarOrca);
}

async function aplicarOrca() {
  const decisiones = [];
  document.querySelectorAll("#orca-plan tr[data-row]").forEach((tr) => {
    if (!tr.querySelector(".orca-ok").checked) return;
    const { fila, json } = orcaPlan[+tr.dataset.row];
    const pid = tr.querySelector(".orca-printer").value;
    const mid = tr.querySelector(".orca-material").value;
    decisiones.push({
      profile_json: json,
      printer_id: pid ? +pid : null,
      material_id: mid ? +mid : null,
      nozzle_mm: fila.nozzle_mm,
    });
  });
  if (!decisiones.length) { toast("No has seleccionado ninguna fila"); return; }

  try {
    const r = await api.send("/api/orca/import", "POST", { decisions: decisiones });
    toast(`${r.calibraciones_nuevas} calibraciones nuevas · ` +
          `${r.materiales_nuevos} materiales nuevos` +
          (r.omitidas ? ` · ${r.omitidas} omitidas` : ""));
    orcaPlan = [];
    document.getElementById("orca-plan").innerHTML = "";
    materials = await api.get("/api/materials");
    loadCalibracion();
  } catch (e) { toast("Error al importar: " + e.message); }
}

document.getElementById("cal-rescan").addEventListener("click", async () => {
  toast("Analizando el historial…");
  try {
    const r = await api.send("/api/calibrations/rescan", "POST");
    toast(`${r.combinaciones} combinaciones · ` +
          `${r.jobs_con_perfil_ajeno} impresiones con perfil de otra máquina`);
    loadCalibracion();
  } catch (e) { toast("Error: " + e.message); }
});

// --- Documentos imprimibles (presupuesto y cotización) -----------------------
// Paleta de marca M3D, en un solo sitio: si cambian los colores corporativos se
// tocan aquí y los dos PDFs quedan alineados.
const BRAND = {
  red:   "#e0472a",   // rojo M3D — logo, acentos, cabecera de tabla
  redDk: "#b23a20",   // rojo oscuro — hover y texto sobre crema
  ink:   "#1a1815",   // titulares, casi negro cálido
  body:  "#4a4440",   // texto corrido
  mute:  "#8c837c",   // secundario
  cream: "#fbf4ee",   // fondo cálido de la marca
  line:  "#e9ddd3",   // bordes suaves
};

function escHtml(t) {
  return String(t ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
// Igual, pero conservando los saltos de línea de los campos multilínea.
const escLines = (t) => escHtml(t).replace(/\n/g, "<br>");

function brandStyles() {
  return `
    @page { margin: 14mm; }
    /* Sin esto los navegadores descartan los fondos al imprimir y la marca
       se pierde: el PDF saldría en blanco y negro. */
    * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
           color: ${BRAND.body}; margin: 0; padding: 28px 32px; max-width: 800px;
           margin-inline: auto; line-height: 1.45; }
    .head { display: flex; justify-content: space-between; align-items: flex-start;
            gap: 24px; padding-bottom: 16px; border-bottom: 3px solid ${BRAND.red}; }
    .logo { max-width: 190px; max-height: 78px; display: block; }
    .logo-fallback { font-size: 1.6rem; font-weight: 800; color: ${BRAND.red};
                     letter-spacing: -.02em; }
    .company { margin-top: 10px; font-weight: 700; color: ${BRAND.ink}; font-size: .95rem; }
    .company small { display: block; margin-top: 3px; font-weight: 400;
                     color: ${BRAND.mute}; font-size: .72rem; white-space: pre-line; }
    .title { text-align: right; }
    .title h1 { margin: 0; font-size: 2rem; font-weight: 800; color: ${BRAND.ink};
                letter-spacing: -.02em; text-transform: uppercase; }
    .title .kicker { color: ${BRAND.red}; font-size: .72rem; font-weight: 700;
                     letter-spacing: .14em; text-transform: uppercase; }
    .meta { display: flex; flex-wrap: wrap; gap: 20px; background: ${BRAND.cream};
            border-radius: 8px; padding: 10px 14px; margin: 18px 0 22px;
            font-size: .82rem; color: ${BRAND.body}; }
    .meta strong { color: ${BRAND.ink}; }
    table { border-collapse: collapse; width: 100%; font-size: .85rem; }
    tr { break-inside: avoid; }
    th { background: ${BRAND.red}; color: #fff; padding: 10px 9px; text-align: left;
         font-weight: 600; font-size: .78rem; letter-spacing: .02em; }
    th:first-child { border-radius: 6px 0 0 0; }
    th:last-child  { border-radius: 0 6px 0 0; }
    td { padding: 10px 9px; border-bottom: 1px solid ${BRAND.line}; }
    th.num, td.num { text-align: right; }
    td.thumb { width: 76px; padding: 8px; }
    td.thumb img { width: 68px; height: 68px; object-fit: contain; display: block;
                   background: ${BRAND.cream}; border-radius: 6px; padding: 3px; }
    .totals { width: 340px; margin: 18px 0 0 auto; }
    .totals td { border: none; padding: 7px 12px; background: ${BRAND.cream}; }
    .totals tr:first-child td:first-child { border-radius: 6px 0 0 0; }
    .totals tr:first-child td:last-child  { border-radius: 0 6px 0 0; }
    .totals .grand td { background: ${BRAND.red}; color: #fff; font-weight: 800;
                        font-size: 1.05rem; }
    .totals .grand td:first-child { border-radius: 0 0 0 6px; }
    .totals .grand td:last-child  { border-radius: 0 0 6px 0; }
    .terms { margin-top: 38px; font-size: .72rem; color: ${BRAND.body};
             background: ${BRAND.cream}; border-left: 3px solid ${BRAND.red};
             padding: 14px 16px; border-radius: 0 8px 8px 0; white-space: pre-line;
             break-inside: avoid; }
    .terms h4 { margin: 0 0 6px; color: ${BRAND.redDk}; font-size: .76rem;
                letter-spacing: .08em; text-transform: uppercase; }
    .terms p { margin: 0 0 8px; }
    .foot { text-align: center; color: ${BRAND.mute}; font-size: .7rem;
            margin-top: 30px; padding-top: 12px; border-top: 1px solid ${BRAND.line}; }
  `;
}

/** Cabecera común: logo (o nombre si no hay), datos de empresa y título. */
function brandHead(s, kicker, title) {
  const logo = s.company_logo
    ? `<img class="logo" src="${escHtml(s.company_logo)}" alt="${escHtml(s.company_name || "Logo")}">`
    : `<div class="logo-fallback">${escHtml(s.company_name || "M3D")}</div>`;
  return `<div class="head">
    <div>
      ${logo}
      ${s.company_name || s.company_info
        ? `<div class="company">${escHtml(s.company_name)}<small>${escLines(s.company_info)}</small></div>`
        : ""}
    </div>
    <div class="title"><div class="kicker">${escHtml(kicker)}</div><h1>${escHtml(title)}</h1></div>
  </div>`;
}

function brandTerms(s) {
  if (!s.payment_info && !s.quote_terms) return "";
  return `<div class="terms">
    ${s.payment_info ? `<h4>Datos de pago</h4><p>${escLines(s.payment_info)}</p>` : ""}
    ${s.quote_terms ? `<h4>Términos y condiciones</h4><p>${escLines(s.quote_terms)}</p>` : ""}
  </div>`;
}

/** Abre el documento en una ventana nueva y lanza la impresión. */
function printDoc(title, bodyHtml) {
  const w = window.open("", "_blank");
  if (!w) { toast("El navegador bloqueó la ventana emergente"); return; }
  w.document.write(`<!doctype html><html lang="es"><head><meta charset="utf-8">
    <title>${escHtml(title)}</title><style>${brandStyles()}</style></head>
    <body>${bodyHtml}</body></html>`);
  w.document.close();
  w.focus();
  w.print();
}

// --- Cotización manual (varias a la vez) -------------------------------------
// Una cotización es un documento: datos comunes en la cabecera y N ítems, uno
// por impresión. El reparto no es estético — lo que es del documento (envío,
// IVA, margen) debe cobrarse UNA vez, no una por pieza; y lo que es de la pieza
// (masa, tiempo, impresora) tiene que poder variar de un ítem a otro.

// Cabecera del documento: [clave, etiqueta, paso, default]
const QUOTE_FIELDS = [
  ["luz", "Coste luz [/kWh]", "0.0001", 0],
  ["op", "Operador [/h]", "0.01", 0],
  ["fallos", "Tasa de fallos [%]", "1", 10],
  ["envio", "Servicio de envío", "0.01", 0],
  ["iva", "IVA [%]", "0.1", 16],
];

// Por ítem. 'kw' y 'amort' salen de la impresora de ESE ítem, así que una
// cotización puede mezclar piezas de máquinas distintas.
const ITEM_FIELDS = [
  ["kg", "Coste material [/kg]", "0.01", 0],
  ["masa", "Masa de la pieza [kg]", "0.001", 0],
  ["tiempo", "Tiempo de impresión [h]", "0.1", 0],
  ["prep", "Tiempo preparación [h]", "0.1", 0],
  ["post", "Tiempo postproducción [h]", "0.1", 0],
  ["kw", "Consumo medio [kW]", "0.01", 0],
  ["amort", "Amortización [/h]", "0.01", 0],
];

// --- Lectura de gcode en el navegador ----------------------------------------
// El archivo no se sube a ningún sitio: se leen solo la cabecera (miniaturas) y
// la cola (bloque de configuración del laminador), así un gcode de 200 MB se
// procesa igual de rápido que uno de 2 MB.
const GCODE_HEAD_BYTES = 1024 * 1024;
const GCODE_TAIL_BYTES = 256 * 1024;

/** "2h 30m 12s" o "5400" → segundos. */
function parseGcodeTime(raw) {
  const s = String(raw).trim();
  if (/^\d+(\.\d+)?$/.test(s)) return parseFloat(s);   // Cura: ya en segundos
  const unit = { d: 86400, h: 3600, m: 60, s: 1 };
  let total = 0, m;
  const re = /(\d+(?:\.\d+)?)\s*([dhms])/gi;
  while ((m = re.exec(s))) total += parseFloat(m[1]) * unit[m[2].toLowerCase()];
  return total;
}

/** Miniatura más grande incrustada en el gcode, como data URI. */
function extractGcodeThumb(text) {
  const MIME = { thumbnail: "image/png", thumbnail_jpg: "image/jpeg" };
  let best = null, cur = null;
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t.startsWith(";")) continue;
    const body = t.slice(1).trim();
    const beg = body.match(/^(thumbnail\w*)\s+begin\s+(\d+)\s*x\s*(\d+)/i);
    if (beg) {
      // QOI y demás formatos que el navegador no pinta: se ignoran.
      const mime = MIME[beg[1].toLowerCase()];
      cur = mime ? { mime, px: +beg[2] * +beg[3], parts: [] } : null;
      continue;
    }
    if (/^thumbnail\w*\s+end/i.test(body)) {
      // Solo se acepta al ver el cierre: si la cabecera cortó el bloque a la
      // mitad, el base64 estaría incompleto y saldría una imagen rota.
      if (cur && cur.parts.length && (!best || cur.px > best.px)) best = cur;
      cur = null;
      continue;
    }
    if (cur) cur.parts.push(body);
  }
  return best ? `data:${best.mime};base64,${best.parts.join("")}` : null;
}

/** Extrae altura de capa, tiempo, peso y material de un File de gcode. */
async function parseGcode(file) {
  if (/\.bgcode$/i.test(file.name)) {
    throw new Error("El gcode binario (.bgcode) no se puede leer; exporta en texto");
  }
  const head = await file.slice(0, GCODE_HEAD_BYTES).text();
  const tail = file.size > GCODE_TAIL_BYTES
    ? await file.slice(file.size - GCODE_TAIL_BYTES).text()
    : "";
  const text = head + "\n" + tail;

  const grab = (re) => { const m = text.match(re); return m ? m[1].trim() : null; };

  const layer = grab(/;\s*layer[_ ]height\s*[:=]\s*([\d.]+)/i);
  const grams = grab(/;\s*(?:total\s+)?filament[_ ]used\s*\[g\]\s*=\s*([\d.]+)/i)
             ?? grab(/;\s*filament[_ ]weight[_ ]total\s*[:=]\s*([\d.]+)/i);
  const mm = grab(/;\s*(?:total\s+)?filament[_ ]used\s*\[mm\]\s*=\s*([\d.]+)/i);
  const curaM = grab(/;Filament used:\s*([\d.]+)m/i);   // Cura: en metros
  const type = grab(/;\s*filament[_ ]type\s*[:=]\s*([A-Za-z0-9+\-. ]+)/i);
  const timeRaw = grab(/;\s*estimated printing time[^=]*=\s*(.+)/i)
               ?? grab(/;TIME:\s*(\d+)/i);

  let weightG = grams ? parseFloat(grams) : null;
  if (weightG == null) {
    // Sin peso del laminador: se deduce de la longitud, como en el registro.
    const lenMm = mm ? parseFloat(mm) : (curaM ? parseFloat(curaM) * 1000 : 0);
    if (lenMm > 0) {
      const dens = (materials.find((x) => x.material_type === type)?.density_g_cm3) || 1.24;
      weightG = Math.PI * Math.pow(0.175 / 2, 2) * (lenMm / 10) * dens;
    }
  }

  return {
    filename: file.name,
    layer_height: layer ? parseFloat(layer) : null,
    filament_g: weightG,
    estimated_time_s: timeRaw ? parseGcodeTime(timeRaw) : null,
    filament_type: type ? type.split(/[;,]/)[0].trim() : null,
    thumbnail_uri: extractGcodeThumb(head),
  };
}

const itemsHost = () => document.getElementById("cotiz-items");
const allItems = () => [...itemsHost().querySelectorAll(".quote-item")];

function hnum(f) {
  const el = document.querySelector(`#cotiz-header [data-h="${f}"]`);
  return el ? parseFloat(el.value) || 0 : 0;
}
function num(item, f) {
  const el = item.querySelector(`[data-f="${f}"]`);
  return el ? parseFloat(el.value) || 0 : 0;
}
const itemQty = (item) => Math.max(1, parseInt(item.querySelector(".q-qty2").value) || 1);

async function loadCotizacion() {
  await ensureRefs();
  buildQuoteHeader();
  if (!allItems().length) addItem();
  recalcQuote();
}

/** Coste de fabricación de UNA pieza del ítem (las tarifas comunes vienen de la cabecera). */
function calcItem(item) {
  const t = num(item, "tiempo");
  const op = hnum("op");
  const material = num(item, "masa") * num(item, "kg");
  const electricidad = hnum("luz") * num(item, "kw") * t;
  const preparacion = op * num(item, "prep");
  const postprod = op * num(item, "post");
  const amortizacion = num(item, "amort") * t;
  const subtotal = material + electricidad + preparacion + postprod + amortizacion;
  const cFallos = subtotal * (hnum("fallos") / 100);
  const pieza = subtotal + cFallos;
  return {
    material, electricidad, preparacion, postprod, amortizacion,
    subtotal, cFallos, pieza, qty: itemQty(item),
  };
}

const COST_KEYS = ["material", "electricidad", "preparacion", "postprod",
                   "amortizacion", "subtotal", "cFallos", "pieza"];

/** Suma de todos los ítems, cada componente ya multiplicado por su cantidad. */
function quoteAggregate() {
  const acc = Object.fromEntries(COST_KEYS.map((k) => [k, 0]));
  acc.unidades = 0;
  const lines = allItems().map((el) => {
    const c = calcItem(el);
    COST_KEYS.forEach((k) => { acc[k] += c[k] * c.qty; });
    acc.unidades += c.qty;
    return { el, c };
  });
  return { lines, acc };
}

/** Totales del documento: margen sobre el coste sumado, luego envío e IVA. */
function quoteTotals() {
  const { lines, acc } = quoteAggregate();
  const m = selectedMargin();
  const subtotal = acc.pieza * (1 + m / 100);
  // La ganancia se reparte proporcionalmente: el material lleva su parte del
  // margen (no que el servicio se lo lleve todo y quede desproporcionado).
  const materialTotal = acc.material * (1 + m / 100);
  const servicioTotal = subtotal - materialTotal;
  const envio = hnum("envio");
  const ivaPct = hnum("iva");
  const iva = (subtotal + envio) * ivaPct / 100;
  return { lines, acc, m, subtotal, materialTotal, servicioTotal,
           envio, ivaPct, iva, total: subtotal + envio + iva };
}

let cotizUid = 0;

// Pie chart SVG (sin librerías) a partir de segmentos {label, value, color}.
function pieChartSVG(segments) {
  const segs = segments.filter((s) => s.value > 0);
  const total = segs.reduce((a, s) => a + s.value, 0);
  if (total <= 0) return '<p class="muted">Sin datos todavía</p>';
  const cx = 90, cy = 90, r = 85;
  let body;
  if (segs.length === 1) {
    body = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${segs[0].color}"></circle>`;
  } else {
    let a = -Math.PI / 2;
    body = segs.map((s) => {
      const a2 = a + (s.value / total) * 2 * Math.PI;
      const x1 = cx + r * Math.cos(a), y1 = cy + r * Math.sin(a);
      const x2 = cx + r * Math.cos(a2), y2 = cy + r * Math.sin(a2);
      const large = (a2 - a) > Math.PI ? 1 : 0;
      a = a2;
      return `<path d="M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z" fill="${s.color}"></path>`;
    }).join("");
  }
  const legend = segs.map((s) =>
    `<div class="pie-leg"><span class="pie-dot" style="background:${s.color}"></span>
      ${s.label} <strong>${money(s.value)}</strong>
      <span class="muted">(${Math.round(s.value / total * 100)}%)</span></div>`).join("");
  return `<div class="pie-wrap">
    <svg width="180" height="180" viewBox="0 0 180 180">${body}</svg>
    <div class="pie-legend">${legend}</div></div>`;
}

/** Pinta los campos comunes de la cabecera (una sola vez por sesión). */
function buildQuoteHeader() {
  const grid = document.querySelector("#cotiz-header .form-grid");
  if (grid.querySelector("[data-h]")) return;   // ya construida
  grid.insertAdjacentHTML("beforeend", QUOTE_FIELDS.map(([f, label, step, def]) =>
    `<label class="field"><span>${label}</span>
      <input type="number" step="${step}" data-h="${f}" value="${def}"></label>`).join(""));

  // El precio de la luz es el configurado en Ajustes: se propone y se puede
  // pisar a mano para un caso puntual, pero no se recarga después.
  const luz = grid.querySelector('[data-h="luz"]');
  if (appSettings && appSettings.electricity_price_per_kwh != null) {
    luz.value = appSettings.electricity_price_per_kwh;
  }
  grid.querySelectorAll("[data-h]").forEach((i) =>
    i.addEventListener("input", recalcQuote));

  document.querySelector("#cotiz-summary .q-sale").innerHTML = saleTableHTML();
  wireSaleTable();
}

function saleTableHTML() {
  const row = (val, label) => `<tr data-m="${val}">
    <td><input type="radio" name="quote-margin" value="${val}" style="width:auto" ${val === "50" ? "checked" : ""}></td>
    <td>${label}</td><td class="num" data-price="${val}"></td><td class="num" data-gain="${val}"></td></tr>`;
  return `<table>
      <tr><th></th><th>Venta</th><th class="num">Precio</th><th class="num">Ganancia</th></tr>
      ${row("30", "+30%")}${row("50", "+50%")}${row("100", "+100%")}
      <tr data-m="custom">
        <td><input type="radio" name="quote-margin" value="custom" style="width:auto"></td>
        <td>+<input class="q-custom" type="number" value="40" style="width:62px"> %</td>
        <td class="num" data-price="custom"></td><td class="num" data-gain="custom"></td></tr>
    </table>
    <p class="muted" style="font-size:.78rem;margin:.4rem 0 0">El margen marcado es el único que sale en el PDF.</p>`;
}

function wireSaleTable() {
  const sale = document.querySelector("#cotiz-summary .q-sale");
  sale.querySelectorAll('input[name="quote-margin"]').forEach((r) =>
    r.addEventListener("change", updateSale));
  sale.querySelector(".q-custom").addEventListener("input", updateSale);
  sale.querySelectorAll("tr[data-m]").forEach((tr) =>
    tr.addEventListener("click", (ev) => {
      if (ev.target.tagName === "INPUT") return;
      tr.querySelector('input[type="radio"]').checked = true;
      updateSale();
    }));
}

/** Recalcula el resumen del documento entero (desglose, tarta y totales). */
function recalcQuote() {
  const { acc } = quoteAggregate();
  const row = (label, val, strong) =>
    `<tr><td>${strong ? `<strong>${label}</strong>` : label}</td>
      <td class="num">${strong ? `<strong>${money(val)}</strong>` : money(val)}</td></tr>`;

  document.querySelector("#cotiz-summary .q-breakdown").innerHTML =
    `<h3 class="pie-title">Coste de la cotización (${allItems().length} ítems · ${acc.unidades} ud.)</h3>
     <table>${row("Material", acc.material)}${row("Electricidad", acc.electricidad)}
      ${row("Preparación", acc.preparacion)}${row("Postproducción", acc.postprod)}
      ${row("Amortización", acc.amortizacion)}
      ${row("Subtotal", acc.subtotal)}${row("Fallos", acc.cFallos)}${row("Coste total", acc.pieza, true)}
    </table>`;

  document.querySelector("#cotiz-summary .q-pie").innerHTML =
    `<h3 class="pie-title">Componentes del precio</h3>` +
    pieChartSVG([
      { label: "Plástico", value: acc.material, color: "#4f8cff" },
      { label: "Electricidad", value: acc.electricidad, color: "#ff6b6b" },
      { label: "Operario", value: acc.preparacion + acc.postprod, color: "#ffb454" },
      { label: "Amortización", value: acc.amortizacion, color: "#3ecf8e" },
      { label: "Tasa de fallos", value: acc.cFallos, color: "#ff8c42" },
    ]);

  allItems().forEach(renderItemCost);
  updateSale();
}

/** Línea de coste bajo cada ítem, para ver qué aporta al total. */
function renderItemCost(item) {
  const c = calcItem(item);
  const m = selectedMargin();
  item.querySelector(".q-itemcost").innerHTML =
    `Coste pieza <strong>${money(c.pieza)}</strong> · ${c.qty} ud → ` +
    `venta ${money(c.pieza * (1 + m / 100) * c.qty)}`;
}

// Porcentaje de margen seleccionado en la tabla de venta ('custom' lee su input).
function selectedMargin() {
  const sale = document.querySelector("#cotiz-summary .q-sale");
  const checked = sale && sale.querySelector('input[type="radio"]:checked');
  if (!checked) return 0;
  if (checked.value === "custom") return parseFloat(sale.querySelector(".q-custom").value) || 0;
  return parseFloat(checked.value);
}

// Actualiza precios de venta y total sin re-renderizar (así no se pierde el foco).
function updateSale() {
  const sale = document.querySelector("#cotiz-summary .q-sale");
  if (!sale) return;
  const coste = quoteAggregate().acc.pieza;
  const marginOf = (k) =>
    k === "custom" ? (parseFloat(sale.querySelector(".q-custom").value) || 0) : parseFloat(k);
  sale.querySelectorAll("[data-price]").forEach((cell) => {
    cell.textContent = money(coste * (1 + marginOf(cell.dataset.price) / 100));
  });
  sale.querySelectorAll("[data-gain]").forEach((cell) => {
    cell.textContent = money(coste * (marginOf(cell.dataset.gain) / 100));
  });
  const sel = sale.querySelector('input[type="radio"]:checked')?.value;
  sale.querySelectorAll("tr[data-m]").forEach((tr) => tr.classList.toggle("sel", tr.dataset.m === sel));

  const q = quoteTotals();
  document.querySelector("#cotiz-bar .q-total").innerHTML =
    `<span class="bar-total">${money(q.total)}</span>` +
    `<span class="bar-detail muted"> · ${allItems().length} ítems · ${q.acc.unidades} ud` +
    ` · venta ${money(q.subtotal)} + envío ${money(q.envio)} + IVA(${q.ivaPct}%) ${money(q.iva)}</span>`;
  allItems().forEach(renderItemCost);
}

// Consumo y amortización de la impresora DE ESE ÍTEM; el consumo depende del
// tipo de material si está elegido (impresora × tipo), si no de la máquina.
async function loadItemDefaults(item) {
  const pid = item.querySelector(".q-printer2").value;
  if (!pid) { item.querySelector(".q-kwsrc").textContent = ""; return; }
  const type = item.querySelector(".q-type").value;
  const url = `/api/printers/${pid}/quote-defaults` +
    (type ? `?material_type=${encodeURIComponent(type)}` : "");
  try {
    const d = await api.get(url);
    currency = d.currency;
    item.querySelector('[data-f="kw"]').value = d.avg_power_kw;
    item.querySelector('[data-f="amort"]').value = d.depreciation_per_h;
    item.querySelector(".q-kwsrc").textContent =
      `${d.avg_power_kw} kW · ${powerSourceLabel(d.avg_power_source)}`;
    recalcQuote();
  } catch (err) { toast("No se pudieron cargar los datos de la impresora"); }
}

// --- Ítems: rellenar desde un gcode ------------------------------------------
// La miniatura vive en el propio nodo del ítem (no en un atributo): es dato de
// esa línea, y así no se mete un data URI de 20 KB dentro del HTML.

function gstatus(item, msg) { item.querySelector(".q-gstatus").textContent = msg; }

/** Muestra la miniatura del ítem (y la guarda para el PDF). */
function setItemThumb(item, uri) {
  item._thumb = uri || null;
  const host = item.querySelector(".q-thumbwrap");
  host.innerHTML = uri
    ? `<img class="q-thumb" src="${uri}" alt="">
       <button type="button" class="btn ghost small q-thumbdel">Quitar</button>`
    : `<div class="q-thumb placeholder"></div>`;
  const del = host.querySelector(".q-thumbdel");
  if (del) del.addEventListener("click", () => setItemThumb(item, null));
}

/** Descarga una imagen y la convierte a data URI (para incrustarla en el PDF). */
async function urlToDataUri(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    const blob = await r.blob();
    return await new Promise((res) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = () => res(null);
      fr.readAsDataURL(blob);
    });
  } catch { return null; }
}

/** Vuelca los datos de un gcode en los campos del ítem. */
async function applyGcodeData(item, d) {
  const setF = (f, v) => { item.querySelector(`[data-f="${f}"]`).value = v; };

  if (d.filename) item.querySelector(".q-desc").value = d.filename.split("/").pop();
  if (d.layer_height) item.querySelector(".q-res").value = `${(+d.layer_height).toFixed(2)} mm`;
  if (d.filament_g) setF("masa", (d.filament_g / 1000).toFixed(4));   // el campo va en kg
  if (d.estimated_time_s) setF("tiempo", (d.estimated_time_s / 3600).toFixed(2));

  // Tipo de material: "PLA+" del laminador casa con el "PLA" del desplegable.
  if (d.filament_type) {
    const sel = item.querySelector(".q-type");
    const want = d.filament_type.toLowerCase().replace(/\+$/, "");
    const opt = [...sel.options].find((o) => o.value.toLowerCase() === want);
    if (opt) sel.value = opt.value;
  }
  // Precio/kg del material elegido, si alguno de ese tipo lo tiene puesto.
  const type = item.querySelector(".q-type").value;
  const mat = materials.find((m) => m.material_type === type && m.price_per_kg > 0);
  if (mat) setF("kg", mat.price_per_kg);

  await loadItemDefaults(item);   // consumo y amortización de su impresora
  recalcQuote();

  const falta = [
    !d.layer_height && "altura de capa",
    !d.filament_g && "masa",
    !d.estimated_time_s && "tiempo",
    !item.querySelector(".q-printer2").value && "impresora",
  ].filter(Boolean);
  gstatus(item, falta.length
    ? `Cargado. Falta: ${falta.join(", ")}.`
    : "Datos cargados del gcode ✓");
}

/** Lista los gcodes de la impresora: en vivo si responde, si no del historial. */
async function loadItemGcodeFiles(item) {
  const pid = item.querySelector(".q-printer2").value;
  const sel = item.querySelector(".q-gfile");
  if (!pid) { sel.innerHTML = `<option value="">— elige impresora —</option>`; return; }

  sel.innerHTML = `<option value="">cargando…</option>`;
  // api.get no distingue error de éxito: un 502 devuelve {detail}, no una lista.
  const list = async (url) => { const r = await api.get(url); return Array.isArray(r) ? r : null; };

  let files = await list(`/api/printers/${pid}/files`);
  let src = "live";
  if (!files || !files.length) {
    const hist = await list(`/api/printers/${pid}/history-files`);
    if (hist && hist.length) { files = hist; src = "history"; }
  }
  item._gsource = src;

  sel.innerHTML = files && files.length
    ? `<option value="">— elige gcode —</option>` +
      files.map((f) => `<option value="${escHtml(f.path)}">${escHtml(f.path)}</option>`).join("")
    : `<option value="">(impresora apagada y sin historial)</option>`;
  if (files && files.length && src === "history") {
    gstatus(item, "Impresora no accesible: lista tomada del historial.");
  }
}

async function fillFromPrinterGcode(item, path) {
  const pid = item.querySelector(".q-printer2").value;
  gstatus(item, "Leyendo metadatos…");
  const e = await api.get(
    `/api/printers/${pid}/estimate?filename=${encodeURIComponent(path)}` +
    `&source=${item._gsource || "history"}`);
  if (!e || e.detail) { gstatus(item, "Ese gcode no tiene metadatos del laminador."); return; }

  await applyGcodeData(item, e);
  if (e.thumbnail) {
    setItemThumb(item, await urlToDataUri(
      `/api/thumbnail/${pid}?path=${encodeURIComponent(e.thumbnail)}`));
  }
}

/** Crea un ítem por cada gcode elegido; el primero puede reutilizar uno vacío. */
async function addItemsFromFiles(files, firstItem) {
  const list = [...files];
  if (!list.length) return;
  for (let i = 0; i < list.length; i++) {
    const item = (i === 0 && firstItem) ? firstItem : addItem();
    gstatus(item, "Leyendo gcode…");
    try {
      const d = await parseGcode(list[i]);
      await applyGcodeData(item, d);
      if (d.thumbnail_uri) setItemThumb(item, d.thumbnail_uri);
    } catch (err) {
      gstatus(item, err.message || "No se pudo leer el gcode.");
    }
  }
  recalcQuote();
  if (list.length > 1) toast(`${list.length} gcodes añadidos`);
}

function wireGcodeBar(item) {
  const srcSel = item.querySelector(".q-gsrc");
  const fileSel = item.querySelector(".q-gfile");
  const pick = item.querySelector(".q-gpick");
  const upload = item.querySelector(".q-gup");

  const syncBar = () => {
    const isUpload = srcSel.value === "upload";
    fileSel.hidden = isUpload;
    pick.hidden = !isUpload;
  };
  srcSel.addEventListener("change", syncBar);
  syncBar();
  loadItemGcodeFiles(item);

  fileSel.addEventListener("change", () => {
    if (fileSel.value) fillFromPrinterGcode(item, fileSel.value)
      .catch(() => gstatus(item, "No se pudo leer ese gcode."));
  });

  pick.addEventListener("click", () => upload.click());
  upload.addEventListener("change", async () => {
    await addItemsFromFiles(upload.files, item);
    upload.value = "";   // permite volver a elegir el mismo archivo
  });
}

// --- Alta y borrado de ítems -------------------------------------------------
function addItem() {
  const fields = ITEM_FIELDS.map(([f, label, step, def]) =>
    `<label class="field"><span>${label}</span>
      <input type="number" step="${step}" data-f="${f}" value="${def}"></label>`).join("");

  const item = el(`<div class="card quote-item">
    <div class="gcode-bar">
      <select class="q-printer2"></select>
      <select class="q-type"></select>
      <select class="q-gsrc">
        <option value="printer">Gcode de la impresora</option>
        <option value="upload">Subir gcode…</option>
      </select>
      <select class="q-gfile"></select>
      <button type="button" class="btn ghost small q-gpick" hidden>Elegir archivo…</button>
      <input type="file" class="q-gup" accept=".gcode,.gco,.g" multiple hidden>
      <span class="spacer"></span>
      <button type="button" class="btn danger small q-remove">Quitar</button>
    </div>
    <div class="item-body">
      <div class="q-thumbwrap"></div>
      <div class="item-fields">
        <div class="form-grid">
          <label class="field"><span>Descripción / archivo</span><input class="q-desc" placeholder="pieza.stl"></label>
          <label class="field"><span>Resolución</span><input class="q-res" value="0.20 mm"></label>
          <label class="field"><span>Cantidad</span><input class="q-qty2" type="number" min="1" value="1"></label>
        </div>
        <div class="form-grid">${fields}</div>
        <small class="q-gstatus muted"></small>
        <small class="q-kwsrc muted"></small>
      </div>
    </div>
    <div class="q-itemcost muted"></div>
  </div>`);

  const printerSel = item.querySelector(".q-printer2");
  printerSel.innerHTML = `<option value="">— impresora —</option>` +
    printers.map((p) => `<option value="${p.id}">${escHtml(p.name)}</option>`).join("");

  const typeSel = item.querySelector(".q-type");
  const types = [...new Set(materials.map((m) => m.material_type).filter(Boolean))].sort();
  typeSel.innerHTML = `<option value="">— tipo material —</option>` +
    types.map((t) => `<option value="${escHtml(t)}">${escHtml(t)}</option>`).join("");

  // Al añadir en lote lo normal es seguir con la misma máquina y material que
  // el ítem anterior, así que se heredan en vez de empezar en blanco.
  const prev = allItems().pop();
  if (prev) {
    printerSel.value = prev.querySelector(".q-printer2").value;
    typeSel.value = prev.querySelector(".q-type").value;
    item.querySelector('[data-f="kg"]').value = prev.querySelector('[data-f="kg"]').value;
    item.querySelector('[data-f="kw"]').value = prev.querySelector('[data-f="kw"]').value;
    item.querySelector('[data-f="amort"]').value = prev.querySelector('[data-f="amort"]').value;
  }

  printerSel.addEventListener("change", () => { loadItemDefaults(item); loadItemGcodeFiles(item); });
  typeSel.addEventListener("change", () => loadItemDefaults(item));
  item.querySelectorAll("input[data-f]").forEach((i) => i.addEventListener("input", recalcQuote));
  item.querySelector(".q-qty2").addEventListener("input", recalcQuote);
  item.querySelector(".q-remove").addEventListener("click", () => { item.remove(); recalcQuote(); });

  setItemThumb(item, null);
  itemsHost().appendChild(item);
  wireGcodeBar(item);
  recalcQuote();
  return item;
}

document.getElementById("add-item").addEventListener("click", addItem);
document.getElementById("add-gcodes").addEventListener("click", () =>
  document.getElementById("bulk-gcode").click());
document.getElementById("bulk-gcode").addEventListener("change", async (ev) => {
  // Si el único ítem sigue vacío, el primer gcode lo reutiliza.
  const items = allItems();
  const vacio = items.length === 1 && !items[0].querySelector(".q-desc").value ? items[0] : null;
  await addItemsFromFiles(ev.target.files, vacio);
  ev.target.value = "";
});
document.getElementById("print-quote").addEventListener("click", printCotiz);

function printCotiz() {
  const items = allItems();
  if (!items.length) { toast("Añade al menos un ítem"); return; }

  const q = quoteTotals();
  const s = appSettings || {};
  const cliente = document.getElementById("q-cliente").value;
  const qnum = "Q-" + Math.random().toString(16).slice(2, 8).toUpperCase();
  // La columna de miniaturas solo aparece si algún ítem tiene: una columna
  // vacía en todas las filas queda peor que no tenerla.
  const conThumb = items.some((it) => it._thumb);

  const filas = q.lines.map(({ el: item, c }, i) => {
    const unit = c.pieza * (1 + q.m / 100);
    const thumb = item._thumb;
    return `<tr>
      ${conThumb ? `<td class="thumb">${thumb ? `<img src="${escHtml(thumb)}" alt="">` : ""}</td>` : ""}
      <td>${i + 1}. ${escHtml(item.querySelector(".q-desc").value || "Pieza de impresión 3D")}</td>
      <td>${escHtml(item.querySelector(".q-type").value || "—")}</td>
      <td>${escHtml(item.querySelector(".q-res").value || "—")}</td>
      <td class="num">${money(unit)}</td>
      <td class="num">${c.qty}</td>
      <td class="num">${money(unit * c.qty)}</td>
    </tr>`;
  }).join("");

  printDoc(`Cotización ${qnum}`, `
    ${brandHead(s, "Impresión 3D", "Cotización")}
    <div class="meta">
      <span><strong>Cotización #:</strong> ${qnum}</span>
      ${cliente ? `<span><strong>Cliente:</strong> ${escHtml(cliente)}</span>` : ""}
      <span><strong>Fecha:</strong> ${new Date().toLocaleDateString()}</span>
      <span><strong>Piezas:</strong> ${q.acc.unidades}</span>
    </div>
    <table>
      <tr>${conThumb ? "<th></th>" : ""}<th>Descripción</th><th>Material</th><th>Resolución</th>
          <th class="num">Costo unitario</th><th class="num">Cantidad</th><th class="num">Total</th></tr>
      ${filas}
    </table>
    <table class="totals">
      <tr><td>Costo de material</td><td class="num">${money(q.materialTotal)}</td></tr>
      <tr><td>Servicio y operario</td><td class="num">${money(q.servicioTotal)}</td></tr>
      <tr><td>Sub-Total</td><td class="num">${money(q.subtotal)}</td></tr>
      <tr><td>Servicio de envío</td><td class="num">${money(q.envio)}</td></tr>
      <tr><td>IVA (${q.ivaPct}%)</td><td class="num">${money(q.iva)}</td></tr>
      <tr class="grand"><td>Total</td><td class="num">${money(q.total)}</td></tr>
    </table>
    ${brandTerms(s)}
    <div class="foot">${escHtml(s.company_name || "M3D Nexus")} — Transforma tus ideas en realidad con impresión 3D</div>
  `);
}

// --- Ajustes -----------------------------------------------------------------
async function loadSettings() {
  const s = await api.get("/api/settings");
  appSettings = s; currency = s.currency;
  document.getElementById("set-price").value = s.electricity_price_per_kwh;
  document.getElementById("set-currency").value = s.currency;
  document.getElementById("set-company-name").value = s.company_name || "";
  document.getElementById("set-company-info").value = s.company_info || "";
  document.getElementById("set-payment-info").value = s.payment_info || "";
  document.getElementById("set-quote-terms").value = s.quote_terms || "";
  document.getElementById("set-orders-folder").value = s.orders_folder_base || "";
  showLogo(s.company_logo || "");
}

// El logo se guarda como data URI para poder incrustarlo en el PDF: si fuera una
// URL, la impresión se dispara antes de que la imagen cargue y sale el hueco.
let logoDataUri = "";
const MAX_LOGO_BYTES = 512 * 1024;

function showLogo(uri) {
  logoDataUri = uri || "";
  const img = document.getElementById("set-logo-preview");
  const clear = document.getElementById("set-logo-clear");
  img.hidden = clear.hidden = !logoDataUri;
  if (logoDataUri) img.src = logoDataUri;
}

document.getElementById("set-logo-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > MAX_LOGO_BYTES) {
    toast("El logo no puede pasar de 512 KB");
    e.target.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => { showLogo(reader.result); toast("Logo listo — pulsa Guardar"); };
  reader.onerror = () => toast("No se pudo leer la imagen");
  reader.readAsDataURL(file);
});

document.getElementById("set-logo-clear").addEventListener("click", () => {
  showLogo("");
  document.getElementById("set-logo-file").value = "";
});

document.getElementById("save-settings").addEventListener("click", async () => {
  try {
    appSettings = await api.send("/api/settings", "PUT", {
      electricity_price_per_kwh: parseFloat(document.getElementById("set-price").value) || 0,
      currency: document.getElementById("set-currency").value || "€",
      company_name: document.getElementById("set-company-name").value,
      company_logo: logoDataUri,
      company_info: document.getElementById("set-company-info").value,
      payment_info: document.getElementById("set-payment-info").value,
      quote_terms: document.getElementById("set-quote-terms").value,
      orders_folder_base: document.getElementById("set-orders-folder").value,
    });
    currency = appSettings.currency;
    toast("Ajustes guardados");
  } catch (e) {
    toast("Error: " + e.message);
  }
});

// --- Util --------------------------------------------------------------------
async function ensureRefs() {
  if (!printers.length) printers = await api.get("/api/printers");
  if (!materials.length) materials = await api.get("/api/materials");
  appSettings = await api.get("/api/settings");
  currency = appSettings.currency;
}

// Carga inicial
ensureRefs().then(() => { loadDashboard(); renderLive(); });
