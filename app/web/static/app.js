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

// Cada sección tiene su URL propia (/pedidos, /impresiones…). El id interno de
// la sección no siempre coincide con la ruta, así que se mapean aquí. El
// servidor devuelve el index para estas rutas, y aquí se resuelve cuál abrir.
const TAB_ROUTES = {
  dashboard: "dashboard", pedidos: "trabajos", jobs: "impresiones",
  printers: "impresoras", materials: "materiales", calibracion: "calibracion",
  quote: "estimacion", cotizacion: "cotizacion", settings: "ajustes",
};
const ROUTE_TABS = Object.fromEntries(Object.entries(TAB_ROUTES).map(([t, r]) => [r, t]));
ROUTE_TABS.pedidos = "pedidos";   // alias histórico: /pedidos → Trabajos
const tabFromPath = () => ROUTE_TABS[(location.pathname.split("/")[1] || "").toLowerCase()] || "dashboard";

// Activa una pestaña en la UI (sin cargarla). Reutilizable para navegar por
// código, p.ej. saltar de una pieza de un pedido a sus impresiones.
function showTab(name) {
  document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
  const btn = document.querySelector(`nav button[data-tab="${name}"]`);
  if (btn) btn.classList.add("active");
  currentTab = name;
  document.getElementById(name).classList.add("active");
  document.body.classList.remove("side-open");   // en móvil, cierra el panel
}

// Navega a una sección: actualiza la URL (sin recargar) y carga sus datos.
function navigate(name, push = true) {
  const route = TAB_ROUTES[name];
  if (!route) return;
  if (push && location.pathname !== `/${route}`) {
    history.pushState({ tab: name }, "", `/${route}`);
  }
  showTab(name);
  loadTab(name);
}

document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => navigate(b.dataset.tab));
});
// Atrás/adelante del navegador.
window.addEventListener("popstate", () => navigate(tabFromPath(), false));

// Panel lateral: colapsar (escritorio) y abrir/cerrar (móvil). Se recuerda.
document.getElementById("side-toggle").addEventListener("click", () => {
  if (window.matchMedia("(max-width: 820px)").matches) {
    document.body.classList.remove("side-open");
    return;
  }
  const collapsed = document.body.classList.toggle("side-collapsed");
  localStorage.setItem("sideCollapsed", collapsed ? "1" : "");
});
document.getElementById("side-open").addEventListener("click", () =>
  document.body.classList.add("side-open"));
if (localStorage.getItem("sideCollapsed")) document.body.classList.add("side-collapsed");

// Salta a Materiales para arreglar un precio que falta: abre el editor de ese
// filamento; si no se sabe cuál es, filtra la lista por su tipo.
async function goToMaterialFix(materialId, materialType) {
  navigate("materials");
  await ensureRefs();
  const m = materialId ? materials.find((x) => x.id === +materialId) : null;
  if (m) {
    materialForm(m);
    const host = document.getElementById("material-form-host");
    host.scrollIntoView({ block: "center", behavior: "smooth" });
    const price = host.querySelector('[data-k="price_per_kg"]');
    if (price) { price.focus(); price.select?.(); }
    toast(`Pon el precio/kg de ${m.name}`);
  } else if (materialType) {
    matFilter.type.clear(); matFilter.type.add(materialType);
    renderMaterialsTable();
    toast(`Filtrado por ${materialType}: pon el precio/kg`);
  }
}

// Salta a Impresiones filtrando por un gcode, con la ÚLTIMA impresión arriba.
async function goToJobsForGcode(gcodeFilename) {
  const base = (gcodeFilename || "").split("/").pop();
  if (!base) return;
  showTab("jobs");
  await loadJobs();                 // carga allJobs antes de filtrar
  document.getElementById("jobs-search").value = base;
  jobsSort = { key: "end_time", dir: "desc" };   // la última impresión, primera
  applyJobsView();
  const first = document.querySelector("#jobs-table tr:nth-child(2)");  // 1ª = cabecera
  if (first) {
    first.classList.add("row-flash");
    first.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function loadTab(name) {
  ({ dashboard: loadDashboard, pedidos: () => setPedMode(projMode), jobs: loadJobs,
     printers: loadPrinters, materials: loadMaterials, calibracion: loadCalibracion,
     quote: loadEstimacion, cotizacion: loadCotizacion, settings: loadSettings })[name]?.();
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
  const labels = { energy: "Electricidad", filament: "Filamento", depreciation: "Amortización", maintenance: "Mantenimiento" };
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
  "impresora×térmico": "estimado por temperatura", "defecto×térmico": "estimado por temperatura",
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
        <div class="live-breakdown muted">luz ${money(c.energy)} · filam ${money(c.filament)} · amort ${money(c.depreciation)}${c.maintenance ? " · mant " + money(c.maintenance) : ""}</div>
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

// Impresiones desplegadas (ids): sobreviven al re-render de la tabla.
const openJobs = new Set();

// Detalle completo de una impresión + calificación (compartido tabla/tarjetas).
function jobDetailHTML(j, pmap, mById) {
  const mat = j.material_id != null ? mById[j.material_id] : null;
  const drow = (k, v) => v == null || v === "" ? "" : `<dt>${k}</dt><dd>${v}</dd>`;
  const bigThumb = j.has_thumbnail
    ? `<img class="oi-detail-thumb" src="/api/jobs/${j.id}/thumbnail" loading="lazy" alt="">`
    : `<div class="oi-detail-thumb ph"></div>`;
  const energia = j.energy_kwh != null
    ? `${j.energy_estimated ? "≈ " : ""}${j.energy_kwh.toFixed(3)} kWh${j.energy_estimated ? " (estimada)" : " (medida)"}`
    : "sin datos";
  const stars = Array.from({ length: 5 }, (_, i) =>
    `<span class="${i < (j.rating || 0) ? "on" : ""}" data-star="${i + 1}">★</span>`).join("");
  const badges = (j.in_use && j.in_use.length)
    ? j.in_use.map((u) => `<span class="use-badge ${u.kind}" data-use-kind="${u.kind}">${escHtml(u.label)}</span>`).join(" ") : null;
  return `<div class="oi-detail job-detail">
    ${bigThumb}
    <div class="oi-detail-body">
      <dl class="oi-dl">
        ${drow("Gcode", escHtml(j.filename || "—"))}
        ${drow("Impresora", escHtml(pmap[j.printer_id] || String(j.printer_id)))}
        ${drow("Estado", `<span class="pill ${j.status}">${j.status}</span>${j.needs_review ? ' <span class="pill review">revisar</span>' : ""}`)}
        ${drow("Inicio", fmtDate(j.start_time))}
        ${drow("Fin", fmtDate(j.end_time))}
        ${drow("Duración", fmtDur(j.print_duration_s))}
        ${drow("Filamento", `${j.filament_weight_g.toFixed(1)} g (${(j.filament_used_mm / 1000).toFixed(1)} m)`)}
        ${drow("Material", mat ? `${escHtml(mat.name)} · ${money(mat.price_per_kg)}/kg` : "—")}
        ${drow("Energía", energia)}
        ${drow("Tarifa luz", `${money(j.tariff_per_kwh)}/kWh`)}
        ${drow("Coste luz", money(j.cost_energy))}
        ${drow("Coste filamento", money(j.cost_filament))}
        ${drow("Amortización", money(j.cost_depreciation))}
        ${drow("Mantenimiento", money(j.cost_maintenance))}
        ${drow("Coste TOTAL", `<strong>${money(j.cost_total)}</strong>`)}
        ${drow("En", badges)}
      </dl>
      <div class="job-review" data-job="${j.id}" data-rating="${j.rating || 0}">
        <div class="review-head"><strong>Calidad de la impresión</strong>
          <span class="muted">— ¿quedó como esperabas o necesita ajuste?</span></div>
        <div class="stars">${stars}</div>
        <textarea class="review-notes" rows="3" placeholder="Notas: primera capa, stringing, tolerancias, qué ajustar la próxima vez…">${escHtml(j.review_notes || "")}</textarea>
        <button class="btn small review-save">Guardar calificación</button>
      </div>
    </div>
  </div>`;
}

function renderJobsTable(rows, pmap, mById) {
  const arrow = (k) => (jobsSort.key === k ? (jobsSort.dir === "asc" ? " ▲" : " ▼") : "");
  const th = (k, label, cls = "") =>
    `<th class="sortable ${cls}" data-sort="${k}">${label}${arrow(k)}</th>`;

  const header = `<tr>
    <th class="thumb-col"></th>
    ${th("filename", "Archivo")}${th("cost_total", "Coste", "num")}<th></th>
    ${th("printer", "Impresora")}${th("status", "Estado")}${th("end_time", "Fin")}
    ${th("print_duration_s", "Duración", "num")}${th("filament_weight_g", "Filam.", "num")}
    ${th("energy_kwh", "Energía", "num")}<th>Material</th><th></th></tr>`;

  document.getElementById("jobs-table").innerHTML = header +
    (rows.length ? rows.map((j) => {
      const review = j.needs_review ? ` <span class="pill review">revisar</span>` : "";
      const thumb = j.has_thumbnail
        ? `<img class="job-thumb" src="/api/jobs/${j.id}/thumbnail" loading="lazy" alt=""
             onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'job-thumb ph'}))">`
        : `<div class="job-thumb ph"></div>`;
      return `<tr data-job-row="${j.id}" class="${openJobs.has(j.id) ? "open" : ""}">
        <td class="thumb-col">${thumb}</td>
        ${(() => {
          const short = escHtml((j.filename || "—").split("/").pop());
          const pr = printers.find((p) => p.id === j.printer_id);
          const url = pr && j.filename ? klipperJobsUrl(pr) : null;
          const rate = j.rating ? ` <span class="rate-ind" title="Calificación">★${j.rating}</span>` : "";
          const nameEl = url
            ? `<span class="job-name gcode-open" data-open-url="${escHtml(url)}" data-gcode-name="${escHtml((j.filename || "").split("/").pop())}" title="Abrir en ${escHtml(pr.name)} y copiar el nombre">${short} ↗</span>`
            : `<span class="job-name" title="${escHtml(j.filename || "")}">${short}</span>`;
          const badges = (j.in_use && j.in_use.length)
            ? `<div class="use-badges">${j.in_use.map((u) => `<span class="use-badge ${u.kind}" data-use-kind="${u.kind}" title="${u.kind === "order" ? "Pedido" : "Proyecto"}: ${escHtml(u.label)}">${escHtml(u.label)}</span>`).join("")}</div>`
            : "";
          return `<td class="job-name-cell">${nameEl}${rate}${badges}</td>`;
        })()}
        ${(() => {
          // Coste en ámbar si el filamento no se está cobrando (material sin
          // precio/kg o sin material): avisa de que el número está incompleto.
          const mat = j.material_id != null ? mById[j.material_id] : null;
          const falta = (j.filament_weight_g || 0) > 0 && (!mat || (mat.price_per_kg || 0) <= 0);
          if (!falta)
            return `<td class="num"><strong class="cost-val ok">${money(j.cost_total)}</strong></td>`;
          const attrs = mat
            ? `data-fix-mat="${mat.id}" title="Falta el precio/kg de ${escHtml(mat.name)} — clic para ponerlo"`
            : `data-fix-mat="" title="Sin material asignado: asígnalo para cobrar el filamento"`;
          return `<td class="num"><strong class="cost-val warn" ${attrs}>${money(j.cost_total)}</strong></td>`;
        })()}
        <td class="use-cell"><button class="btn ghost small add-to-btn" data-add-order="${j.id}" title="Añadir a un pedido o proyecto">Agregar a:</button></td>
        <td>${escHtml(pmap[j.printer_id] || String(j.printer_id))}</td>
        <td><span class="pill ${j.status}">${j.status}</span>${review}</td>
        <td class="muted">${fmtDate(j.end_time)}</td>
        <td class="num">${fmtDur(j.print_duration_s)}</td>
        <td class="num">${j.filament_weight_g.toFixed(0)} g</td>
        <td class="num"${j.energy_estimated ? ' title="Energía estimada con la potencia media de otra impresora (no medida)"' : ""}>${j.energy_kwh != null ? (j.energy_estimated ? "≈ " : "") + j.energy_kwh.toFixed(3) + " kWh" : "—"}</td>
        <td><div class="mat-cell">${jobMatButton(j, mById)}
          <button class="btn ghost small" data-gcode-fil="${j.id}" title="Filamentos del gcode (elegir cuál se usó / crear)">🧵</button></div></td>
        <td class="row-actions">
          <button class="btn ghost small" data-recompute="${j.id}">↻</button>
          <button class="btn danger small" data-del-job="${j.id}">✕</button>
        </td></tr>
        <tr class="job-detail-tr" data-detail="${j.id}" ${openJobs.has(j.id) ? "" : "hidden"}>
          <td colspan="12">${openJobs.has(j.id) ? jobDetailHTML(j, pmap, mById) : ""}</td>
        </tr>`;
    }).join("") : `<tr><td colspan="12" class="muted">Sin resultados con estos filtros.</td></tr>`);

  // Vista móvil: las mismas filas como tarjetas apiladas (12 columnas no caben
  // en un teléfono). Mismos data-* que la tabla: el cableado de abajo, con
  // ámbito #jobs, engancha ambas vistas a la vez.
  document.getElementById("jobs-cards").innerHTML = rows.length ? rows.map((j) => {
    const short = escHtml((j.filename || "—").split("/").pop());
    const pr = printers.find((p) => p.id === j.printer_id);
    const url = pr && j.filename ? klipperJobsUrl(pr) : null;
    const thumb = j.has_thumbnail
      ? `<img class="jc-thumb" src="/api/jobs/${j.id}/thumbnail" loading="lazy" alt="">`
      : `<div class="jc-thumb ph"></div>`;
    const name = url
      ? `<span class="job-name gcode-open" data-open-url="${escHtml(url)}" data-gcode-name="${escHtml((j.filename || "").split("/").pop())}">${short} ↗</span>`
      : `<span class="job-name">${short}</span>`;
    const mat = j.material_id != null ? mById[j.material_id] : null;
    const falta = (j.filament_weight_g || 0) > 0 && (!mat || (mat.price_per_kg || 0) <= 0);
    const cost = falta
      ? `<strong class="cost-val warn" data-fix-mat="${mat ? mat.id : ""}">${money(j.cost_total)}</strong>`
      : `<strong class="cost-val ok">${money(j.cost_total)}</strong>`;
    const badges = (j.in_use && j.in_use.length)
      ? `<div class="use-badges">${j.in_use.map((u) => `<span class="use-badge ${u.kind}" data-use-kind="${u.kind}">${escHtml(u.label)}</span>`).join("")}</div>` : "";
    return `<div class="job-card" data-job-card="${j.id}">
      <div class="jc-top">${thumb}
        <div class="jc-title">${name}${j.rating ? ` <span class="rate-ind">★${j.rating}</span>` : ""}${badges}</div>
        ${cost}
      </div>
      <div class="jc-meta muted">
        ${escHtml(pmap[j.printer_id] || "")} · <span class="pill ${j.status}">${j.status}</span>${j.needs_review ? ` <span class="pill review">revisar</span>` : ""}<br>
        ${fmtDate(j.end_time)} · ${fmtDur(j.print_duration_s)} · ${j.filament_weight_g.toFixed(0)} g · ${j.energy_kwh != null ? (j.energy_estimated ? "≈ " : "") + j.energy_kwh.toFixed(3) + " kWh" : "— kWh"}
      </div>
      <div class="jc-actions">
        <div class="mat-cell">${jobMatButton(j, mById)}
          <button class="btn ghost small" data-gcode-fil="${j.id}">🧵</button></div>
        <span class="spacer"></span>
        <button class="btn ghost small" data-add-order="${j.id}">Agregar a:</button>
        <button class="btn ghost small" data-recompute="${j.id}">↻</button>
        <button class="btn danger small" data-del-job="${j.id}">✕</button>
      </div>
      <div class="jc-detail" hidden></div>
    </div>`;
  }).join("") : `<p class="muted">Sin resultados con estos filtros.</p>`;

  document.querySelectorAll("#jobs-table th.sortable").forEach((h) =>
    h.addEventListener("click", () => {
      const k = h.dataset.sort;
      if (jobsSort.key === k) jobsSort.dir = jobsSort.dir === "asc" ? "desc" : "asc";
      else { jobsSort.key = k; jobsSort.dir = ["filename", "printer", "status"].includes(k) ? "asc" : "desc"; }
      applyJobsView();
    }));
  document.querySelectorAll("[data-pick-open]").forEach((b) =>
    b.addEventListener("click", () =>
      openMaterialPicker(+b.dataset.pickOpen, b.dataset.current ? +b.dataset.current : null)));
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
  document.querySelectorAll("[data-gcode-fil]").forEach((b) =>
    b.addEventListener("click", () => pickGcodeFilament(b)));
  document.querySelectorAll("#jobs [data-open-url]").forEach((el) =>
    el.addEventListener("click", () => openGcodeInUI(el)));
  document.querySelectorAll("[data-add-order]").forEach((b) =>
    b.addEventListener("click", () => openAddPicker(+b.dataset.addOrder)));
  document.querySelectorAll("#jobs [data-fix-mat]").forEach((b) =>
    b.addEventListener("click", () => {
      if (b.dataset.fixMat) goToMaterialFix(b.dataset.fixMat);
      else toast("Asigna primero un material a esta impresión");
    }));
  document.querySelectorAll("#jobs [data-use-kind]").forEach((b) =>
    b.addEventListener("click", () => {
      showTab("pedidos");
      setPedMode(b.dataset.useKind === "order" ? "orders" : "projects");
    }));

  // --- Detalle desplegable + calificación -----------------------------------
  const fillDetail = (container, id) => {
    const j = allJobs.find((x) => x.id === id);
    if (!j) return;
    container.innerHTML = jobDetailHTML(j, pmap, mById);
    wireReview(container);
  };
  const wireReview = (scope) => {
    const box = scope.querySelector(".job-review");
    if (!box) return;
    const paint = (r) => box.querySelectorAll(".stars span").forEach((s, i) =>
      s.classList.toggle("on", i < r));
    box.querySelectorAll(".stars span").forEach((s, i) =>
      s.addEventListener("click", () => {
        // Pulsar la estrella actual la quita (vuelve a "sin calificar").
        const next = (i + 1 === (+box.dataset.rating || 0)) ? 0 : i + 1;
        box.dataset.rating = next; paint(next);
      }));
    box.querySelector(".review-save").addEventListener("click", async () => {
      const id = +box.dataset.job;
      try {
        const j = await api.send(`/api/jobs/${id}/review`, "POST",
          { rating: +box.dataset.rating || 0, notes: box.querySelector(".review-notes").value });
        const loc = allJobs.find((x) => x.id === id);
        if (loc) { loc.rating = j.rating; loc.review_notes = j.review_notes; }
        toast("Calificación guardada");
        applyJobsView();   // refresca el ★ de la fila; lo abierto se conserva
      } catch (e) { toast("Error: " + e.message); }
    });
  };
  // Filas ya abiertas (vienen renderizadas): cablear su calificación.
  document.querySelectorAll("#jobs-table tr.job-detail-tr:not([hidden])").forEach(wireReview);
  // Clic en cualquier parte del renglón (menos controles) despliega el detalle.
  document.querySelectorAll("#jobs-table tr[data-job-row]").forEach((row) =>
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, a, select, input, textarea, .gcode-open, .use-badge, .cost-val.warn")) return;
      const id = +row.dataset.jobRow;
      const det = document.querySelector(`tr[data-detail="${id}"]`);
      if (!det) return;
      const willOpen = det.hidden;
      if (willOpen && !det.firstElementChild.innerHTML.trim()) fillDetail(det.firstElementChild, id);
      det.hidden = !willOpen;
      row.classList.toggle("open", willOpen);
      willOpen ? openJobs.add(id) : openJobs.delete(id);
    }));
  // Tarjetas móviles: mismo detalle, tocando la tarjeta.
  document.querySelectorAll("#jobs-cards .job-card").forEach((card) => {
    const id = +card.dataset.jobCard;
    const det = card.querySelector(".jc-detail");
    if (!det) return;
    if (openJobs.has(id)) { det.hidden = false; fillDetail(det, id); }
    card.addEventListener("click", (e) => {
      if (e.target.closest("button, a, select, input, textarea, .gcode-open, .use-badge, .cost-val.warn, .job-review")) return;
      const willOpen = det.hidden;
      if (willOpen && !det.innerHTML.trim()) fillDetail(det, id);
      det.hidden = !willOpen;
      willOpen ? openJobs.add(id) : openJobs.delete(id);
    });
  });
}

// --- Añadir una impresión a un PEDIDO o PROYECTO (modal con toggle) ----------
function closeOrderPicker() { document.getElementById("order-picker").hidden = true; }

async function openAddPicker(jobId) {
  const job = allJobs.find((j) => j.id === jobId);
  if (!job) return;
  let ordersData, projectsData;
  try {
    [ordersData, projectsData] = await Promise.all([api.get("/api/orders"), api.get("/api/projects")]);
  } catch (e) { return toast("No se pudo cargar pedidos/proyectos"); }
  const orders = Array.isArray(ordersData) ? ordersData : (ordersData.orders || []);
  const projects = projectsData || [];
  const gname = (job.filename || "").split("/").pop() || "sin gcode";
  const pmap = Object.fromEntries(printers.map((p) => [p.id, p.name]));
  const ov = document.getElementById("order-picker");
  let mode = "orders";
  ov.innerHTML = `<div class="modal-box op-modal">
    <div class="op-head">
      <strong>Añadir a…</strong><span class="spacer"></span>
      <button class="btn ghost small" id="op-close">✕</button>
    </div>
    <div class="seg op-seg" id="ap-mode">
      <button data-m="orders" class="active">Pedido</button>
      <button data-m="projects">Proyecto</button>
    </div>
    <input type="search" id="op-q" class="op-search" placeholder="Buscar…">
    <p class="muted op-added">Se añade como pieza: <b>${escHtml(gname)}</b>${job.printer_id ? " · " + escHtml(pmap[job.printer_id] || "") : ""}</p>
    <div class="op-list" id="op-list"></div>
  </div>`;
  ov.hidden = false;

  const render = () => {
    const q = (document.getElementById("op-q").value || "").toLowerCase().trim();
    const rows = mode === "orders"
      ? orders.filter((o) => !q || ((o.client || "") + " " + (o.description || "")).toLowerCase().includes(q))
          .map((o) => ({ id: o.id, title: o.client || "—", extra: o.description,
            sub: `${(ORDER_STATUS[o.status] || [o.status])[0]} · ${o.items.length} pieza(s)` }))
      : projects.filter((p) => !q || (p.name || "").toLowerCase().includes(q))
          .map((p) => ({ id: p.id, title: p.name, extra: p.notes,
            sub: `${p.parts.length} parte(s) · ${money2(p.cost_total, currency)}` }));
    document.getElementById("op-list").innerHTML = rows.length
      ? rows.map((x) => `<button class="op-card" data-id="${x.id}">
          <div><strong>${escHtml(x.title)}</strong> ${x.extra ? `<span class="muted">${escHtml(x.extra)}</span>` : ""}</div>
          <div class="muted">${escHtml(x.sub)}</div></button>`).join("")
      : `<p class="muted">Nada con ese filtro. ${mode === "projects" ? "Crea un proyecto en Pedidos → Proyectos." : ""}</p>`;
    document.querySelectorAll(".op-card").forEach((c) =>
      c.addEventListener("click", async () => {
        try {
          if (mode === "orders")
            await api.send(`/api/orders/${c.dataset.id}/items`, "POST",
              { printer_id: job.printer_id, gcode_filename: job.filename, quantity: 1 });
          else
            await api.send(`/api/projects/${c.dataset.id}/add-job`, "POST", { job_id: job.id });
          closeOrderPicker();
          toast(mode === "orders" ? "Añadido al pedido" : "Añadido al proyecto");
          loadJobs();   // refresca para que el badge de pertenencia salga ya
        } catch (e) { toast("No se pudo añadir"); }
      }));
  };
  render();
  ov.querySelector("#op-close").onclick = closeOrderPicker;
  ov.onclick = (e) => { if (e.target === ov) closeOrderPicker(); };
  ov.querySelector("#op-q").addEventListener("input", render);
  ov.querySelectorAll("#ap-mode button").forEach((b) =>
    b.addEventListener("click", () => {
      mode = b.dataset.m;
      ov.querySelectorAll("#ap-mode button").forEach((x) => x.classList.toggle("active", x === b));
      render();
    }));
}

// Filamentos del gcode: para multi-material, elegir con cuál se imprimió; si no
// existe como material, se crea al elegirlo. Resuelve el caso en que el gcode
// lista dos filamentos y el auto-detector se quedó con el primero.
async function pickGcodeFilament(btn) {
  const jid = btn.dataset.gcodeFil;
  const cell = btn.closest(".mat-cell");
  let d;
  try { d = await api.get(`/api/jobs/${jid}/filaments`); }
  catch (e) { return toast("No se pudieron leer los filamentos del gcode"); }
  if (!d.filaments.length) return toast("El gcode no lista filamentos con nombre");
  const opts = d.filaments.map((f) => {
    const exists = f.material_id != null;
    const tag = exists
      ? (f.material_id === d.current_material_id ? ` <span class="pill">actual</span>` : "")
      : ` <span class="pill review">crear</span>`;
    return `<button class="btn ghost small gcode-fil-opt" data-fil="${escHtml(f.name)}" data-job="${jid}">${escHtml(f.name)}${tag}</button>`;
  }).join("");
  cell.innerHTML = `<div class="gcode-fils">${opts}
    <button class="btn ghost small" data-fil-cancel>✕</button></div>`;
  cell.querySelectorAll(".gcode-fil-opt").forEach((ob) =>
    ob.addEventListener("click", async () => {
      await api.send(`/api/jobs/${ob.dataset.job}/set-filament`, "POST", { name: ob.dataset.fil });
      toast("Filamento asignado"); loadJobs();
    }));
  cell.querySelector("[data-fil-cancel]").addEventListener("click", loadJobs);
}

// --- Selector de filamento con fotos y filtros (modal) -----------------------
// Botón por fila que muestra el material actual (muestra de color + nombre) y
// abre un modal para elegir entre toda la biblioteca, con foto de bobina y
// filtros por tipo, marca y color.
function jobMatButton(j, mById) {
  const m = j.material_id != null ? mById[j.material_id] : null;
  const inner = m
    ? `${m.color_hex ? `<span class="swatch" style="background:${escHtml(m.color_hex)}"></span>` : ""}${escHtml(m.name)}`
    : `<span class="muted">— material —</span>`;
  return `<button class="btn ghost small mat-pick-btn" data-pick-open="${j.id}" data-current="${j.material_id ?? ""}">${inner}</button>`;
}

let matPickJob = null;
const matPick = { type: new Set(), brand: new Set(), color: new Set(), q: "" };

async function openMaterialPicker(jobId, currentId) {
  matPickJob = jobId;
  matPick.type.clear(); matPick.brand.clear(); matPick.color.clear(); matPick.q = "";
  await ensureRefs();
  const types = [...new Set(materials.map((m) => m.material_type).filter(Boolean))].sort();
  const brands = [...new Set(materials.map((m) => m.brand).filter(Boolean))].sort();
  const colors = [...new Set(materials.map((m) => m.color_hex).filter(Boolean))];
  const chip = (dim, val, label, extra = "") =>
    `<button class="mp-chip" data-dim="${dim}" data-val="${escHtml(val)}">${extra}${escHtml(label)}</button>`;

  const ov = document.getElementById("mat-picker");
  ov.innerHTML = `<div class="modal-box mp-modal">
    <div class="mp-top">
      <strong>Elegir filamento</strong>
      <input type="search" id="mp-q" placeholder="Buscar nombre, marca o color…">
      <button class="btn ghost small" id="mp-close">✕</button>
    </div>
    <div class="mp-filters">
      <div class="mp-frow"><span class="muted">Tipo</span>${types.map((t) => chip("type", t, t)).join("")}</div>
      <div class="mp-frow"><span class="muted">Marca</span>${brands.map((b) => chip("brand", b, b)).join("")}</div>
      <div class="mp-frow"><span class="muted">Color</span>${colors.map((c) => chip("color", c, "", `<span class="swatch" style="background:${escHtml(c)}"></span>`)).join("")}</div>
    </div>
    <div class="mp-grid" id="mp-grid"></div>
  </div>`;
  ov.hidden = false;
  renderMatPickGrid(currentId);

  ov.querySelector("#mp-close").onclick = closeMaterialPicker;
  ov.onclick = (e) => { if (e.target === ov) closeMaterialPicker(); };
  ov.querySelector("#mp-q").addEventListener("input", (e) => {
    matPick.q = e.target.value.toLowerCase().trim(); renderMatPickGrid(currentId);
  });
  ov.querySelectorAll(".mp-chip").forEach((c) =>
    c.addEventListener("click", () => {
      const set = matPick[c.dataset.dim];
      set.has(c.dataset.val) ? set.delete(c.dataset.val) : set.add(c.dataset.val);
      c.classList.toggle("on");
      renderMatPickGrid(currentId);
    }));
}

function matPickPass(m) {
  const f = matPick;
  if (f.type.size && !f.type.has(m.material_type)) return false;
  if (f.brand.size && !f.brand.has(m.brand)) return false;
  if (f.color.size && !f.color.has(m.color_hex)) return false;
  if (f.q) {
    const hay = ((m.name || "") + " " + (m.brand || "") + " " + (m.color || "")).toLowerCase();
    if (!hay.includes(f.q)) return false;
  }
  return true;
}

function renderMatPickGrid(currentId) {
  const grid = document.getElementById("mp-grid");
  const list = materials.filter(matPickPass);
  const card = (m) => {
    const sel = m.id === currentId ? " sel" : "";
    const img = m.photo_url
      ? `<img class="mp-photo" src="${m.photo_url}" loading="lazy" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'mp-photo ph'}))">`
      : `<div class="mp-photo ph" style="background:${escHtml(m.color_hex || "")}"></div>`;
    const sw = m.color_hex ? `<span class="swatch" style="background:${escHtml(m.color_hex)}"></span>` : "";
    const price = m.price_per_kg > 0 ? `${money(m.price_per_kg)}/kg` : `<span class="pill review">sin precio</span>`;
    return `<button class="mp-card${sel}" data-pick="${m.id}">${img}
      <div class="mp-cinfo">
        <div class="mp-cname">${sw}${escHtml(m.name)}</div>
        <div class="muted mp-cmeta">${escHtml(m.material_type || "")}${m.brand ? " · " + escHtml(m.brand) : ""} · ${price}</div>
      </div></button>`;
  };
  grid.innerHTML =
    `<button class="mp-card${currentId == null ? " sel" : ""}" data-pick=""><div class="mp-photo ph"></div>
       <div class="mp-cinfo"><div class="mp-cname">— sin material —</div></div></button>`
    + (list.length ? list.map(card).join("") : `<p class="muted" style="grid-column:1/-1">Ningún filamento con esos filtros.</p>`);
  grid.querySelectorAll("[data-pick]").forEach((b) =>
    b.addEventListener("click", async () => {
      const mid = b.dataset.pick ? +b.dataset.pick : null;
      await api.send(`/api/jobs/${matPickJob}/assign-material`, "POST", { material_id: mid });
      closeMaterialPicker(); toast("Material asignado"); loadJobs();
    }));
}

function closeMaterialPicker() {
  document.getElementById("mat-picker").hidden = true;
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
  ["resale_value", "Valor de reventa estimado", "number", 0],
  ["amortization_years", "Tiempo amortización (años)", "number", 2],
  ["active_days_per_year", "Días activa al año", "number", 250],
  ["active_hours_per_day", "Horas por día", "number", 8],
  ["maintenance_per_hour", "Mantenimiento /h", "number", 0],
];

// URL de la interfaz Klipper: host de la impresora + el puerto de la UI.
function klipperUrl(p) {
  if (!p.host) return null;
  const port = p.ui_port || 80;
  return `http://${p.host}${port === 80 ? "" : ":" + port}`;
}

// Ruta de archivos/jobs según la interfaz Klipper de la impresora.
const KLIPPER_JOBS_PATH = { mainsail: "/files", fluidd: "/#/jobs" };
function klipperJobsUrl(p) {
  const base = p && klipperUrl(p);
  return base ? base + (KLIPPER_JOBS_PATH[p.ui_type] || KLIPPER_JOBS_PATH.mainsail) : null;
}
// Abre el gcode en la UI de la impresora y copia su nombre (Mainsail/Fluidd no
// aceptan un buscador precargado por URL, así que se copia para pegar).
function openGcodeInUI(el) {
  const name = el.dataset.gcodeName || "";
  if (navigator.clipboard) navigator.clipboard.writeText(name).catch(() => {});
  toast(`Abriendo la impresora · busca: ${name}`);
  window.open(el.dataset.openUrl, "_blank", "noopener");
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

  // "Seguir el último impreso" solo aplica a un color: desfija lo manual y la
  // carga vuelve a deducirse de la última impresión. En multicolor no hay de
  // dónde deducir por hueco, así que no se ofrece.
  const pinned = slots.some((s) => s.source === "manual");
  const autoBtn = (n === 1 && pinned)
    ? `<button class="btn ghost small" data-auto-load="${printerId}">↻ Seguir el último impreso</button>` : "";

  row.querySelector("td").innerHTML = `<div class="load-editor">
    <div class="form-grid">${selects}</div>
    <div class="row-actions" style="margin-top:.6rem">
      <button class="btn small" data-save-load="${printerId}">Guardar carga</button>
      ${autoBtn}
      <button class="btn ghost small" data-cancel-load="${printerId}">Cerrar</button>
    </div></div>`;
  row.hidden = false;

  row.querySelector(`[data-save-load]`).onclick = async () => {
    const mats = [...row.querySelectorAll(".slot-sel")].map((s) => s.value ? +s.value : null);
    await api.send(`/api/printers/${printerId}/loaded`, "PUT", { materials: mats });
    toast("Carga actualizada");
    loadPrinters();
  };
  const auto = row.querySelector(`[data-auto-load]`);
  if (auto) auto.onclick = async () => {
    await api.send(`/api/printers/${printerId}/loaded`, "PUT", { materials: [] });
    toast("Volverá a seguir el último impreso");
    loadPrinters();
  };
  row.querySelector(`[data-cancel-load]`).onclick = () => (row.hidden = true);
}

// Refresco en vivo del "cargado ahora" mientras se mira la pestaña. Se salta si
// hay un formulario de impresora o un editor de carga abierto, para no cerrarlo.
setInterval(() => {
  if (currentTab !== "printers") return;
  if (document.getElementById("printer-form-host").innerHTML) return;
  if ([...document.querySelectorAll(".load-editor-row")].some((r) => !r.hidden)) return;
  loadPrinters();
}, 15000);

function printerForm(p = {}) {
  const host = document.getElementById("printer-form-host");
  host.innerHTML = "";
  const fields = PRINTER_FIELDS.map(([k, lbl, t, def]) =>
    `<label class="field"><span>${lbl}</span><input data-k="${k}" type="${t}" value="${p[k] ?? def ?? ""}" /></label>`).join("");
  const multi = p.multicolor === true;
  // Desplegable de "energía prestada": otras impresoras, excluida ella misma.
  const refOpts = `<option value="">— Mide su propia energía —</option>` +
    printers.filter((o) => o.id !== p.id).map((o) =>
      `<option value="${o.id}" ${p.power_ref_printer_id === o.id ? "selected" : ""}>${escHtml(o.name)}</option>`).join("");
  const form = el(`<div class="card" style="margin-top:1rem">
    <h2 style="margin-top:0">${p.id ? "Editar" : "Nueva"} impresora</h2>
    <div class="form-grid">${fields}</div>
    <div class="muted" id="pf-amort-hint" style="margin:.2rem 0 .6rem"></div>
    <label class="field"><span>Energía: tomar prestada de…</span>
      <select data-k="power_ref_printer_id" id="pf-powerref">${refOpts}</select>
      <small class="muted">Solo si NO tiene sensor propio: estima la luz con la potencia media de otra máquina (marca sus impresiones como estimadas).</small></label>
    <label class="field"><span>Interfaz Klipper</span><select data-k="ui_type">
      <option value="mainsail" ${p.ui_type !== "fluidd" ? "selected" : ""}>Mainsail (/files)</option>
      <option value="fluidd" ${p.ui_type === "fluidd" ? "selected" : ""}>Fluidd (/#/jobs)</option></select>
      <small class="muted">Para abrir el gcode en la UI de la impresora desde Impresiones.</small></label>
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

  // Pista en vivo del coste de máquina por hora: (precio − reventa)/vida +
  // mantenimiento. Es el número que de verdad importa para el coste real.
  const getF = (k) => parseFloat(form.querySelector(`[data-k="${k}"]`)?.value) || 0;
  const hint = form.querySelector("#pf-amort-hint");
  const refreshHint = () => {
    const life = getF("amortization_years") * getF("active_days_per_year") * getF("active_hours_per_day");
    const dep = life > 0 ? Math.max(0, getF("purchase_price") - getF("resale_value")) / life : 0;
    const maint = getF("maintenance_per_hour");
    hint.innerHTML = life > 0
      ? `Coste de máquina ≈ <b>${money(dep + maint)}/h</b> (amortización ${money(dep)} + mantenimiento ${money(maint)}) · vida útil ${Math.round(life)} h`
      : `Define años × días × horas para calcular el coste de máquina por hora.`;
  };
  ["purchase_price", "resale_value", "amortization_years", "active_days_per_year",
   "active_hours_per_day", "maintenance_per_hour"].forEach((k) => {
    const inp = form.querySelector(`[data-k="${k}"]`);
    if (inp) inp.addEventListener("input", refreshHint);
  });
  refreshHint();

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
    // La referencia de energía es id de impresora o null (nunca cadena ni 0).
    body.power_ref_printer_id = body.power_ref_printer_id ? +body.power_ref_printer_id : null;
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

// Calibración por material: { material_id: { impresora: mejorNivel } }.
// Solo cuenta como "calibrado" lo que representa una calibración real.
let calByMaterial = {};
const CAL_RANK = { FULL: 3, BASIC: 2, CALIBRATED: 1 };

let matUsage = {};   // { material_id: { count, last_used } }

async function loadMaterials() {
  const [mats, cals, usage] = await Promise.all([
    api.get("/api/materials"),
    api.get("/api/calibrations").catch(() => []),
    api.get("/api/materials/usage").catch(() => ({})),
  ]);
  materials = mats;
  matUsage = usage || {};

  calByMaterial = {};
  for (const c of cals) {
    if (!(c.status in CAL_RANK)) continue;   // NOT_TUNED / UNKNOWN no cuentan
    const byPrinter = (calByMaterial[c.material_id] ||= {});
    const prev = byPrinter[c.printer];
    if (!prev || CAL_RANK[c.status] > CAL_RANK[prev]) byPrinter[c.printer] = c.status;
  }

  // Desplegables poblados con lo que hay (mismo criterio que en Calibración).
  const opts = (sel, valores) => {
    const el = document.getElementById(sel);
    if (!el) return;
    const actual = el.value;
    el.innerHTML = el.querySelector("option").outerHTML +
      [...new Set(valores)].filter(Boolean).sort()
        .map((v) => `<option value="${escHtml(v)}" ${v === actual ? "selected" : ""}>${escHtml(v)}</option>`).join("");
  };
  buildMatFilters();
  renderMaterialsTable();
}

// Filtros multi-selección (tipo/marca/stock/color), estilo Excel. Cada uno es
// un conjunto: vacío = sin filtrar; con elementos = solo esos.
const matFilter = { type: new Set(), brand: new Set(), stock: new Set(), color: new Set() };
const STOCK_LABEL = { full: "Completa", half: "A medias", low: "< 100 g",
                      empty: "Agotada", unknown: "Sin indicar" };
const NO_BRAND = " nobrand";   // centinela para materiales sin marca
const NO_COLOR = " nocolor";

function buildMatFilters() {
  const host = document.getElementById("mat-filters");
  // Se limpian los desplegables previos (se reconstruyen con los valores al día).
  host.querySelectorAll(".filter-dd").forEach((n) => n.remove());

  const types = [...new Set(materials.map((m) => m.material_type).filter(Boolean))].sort();
  const brands = [...new Set(materials.map((m) => m.brand))];
  const brandOpts = brands.filter(Boolean).sort().map((b) => ({ value: b, label: b }));
  if (brands.some((b) => !b)) brandOpts.push({ value: NO_BRAND, label: "(sin marca)" });
  const stocks = [...new Set(materials.map((m) => m.stock_level || "unknown"))];

  // Se insertan antes del botón Color; para que queden Tipo, Marca, Stock en la
  // barra, se añaden en ese orden (cada uno queda a la izquierda del anterior).
  const anchor = document.getElementById("mat-color-btn");
  makeCheckDropdown(host, anchor, "Tipo", "type",
    types.map((t) => ({ value: t, label: t })));
  makeCheckDropdown(host, anchor, "Marca", "brand", brandOpts);
  makeCheckDropdown(host, anchor, "Stock", "stock",
    stocks.sort().map((s) => ({ value: s, label: STOCK_LABEL[s] || s })));
}

// Desplegable de checkboxes. Inserta antes del botón de Color para mantener orden.
function makeCheckDropdown(host, before, label, key, options) {
  const dd = el(`<div class="filter-dd">
    <button type="button" class="btn ghost small filter-btn">${label} ▾</button>
    <div class="filter-panel" hidden>
      ${options.length ? `<label class="filter-opt filter-all">
        <input type="checkbox" class="filter-master"> <strong>(Seleccionar todo)</strong></label>
        <div class="filter-sep"></div>` : ""}
      ${options.map((o) => `<label class="filter-opt">
        <input type="checkbox" value="${escHtml(o.value)}" ${matFilter[key].has(o.value) ? "checked" : ""}>
        ${escHtml(o.label)}</label>`).join("") || '<span class="muted">Sin opciones</span>'}
    </div>
  </div>`);
  host.insertBefore(dd, before);

  const btn = dd.querySelector(".filter-btn");
  const panel = dd.querySelector(".filter-panel");
  const boxes = [...panel.querySelectorAll("input:not(.filter-master)")];
  const master = panel.querySelector(".filter-master");

  const refreshLabel = () => {
    const n = matFilter[key].size;
    btn.textContent = n ? `${label} (${n}) ▾` : `${label} ▾`;
    btn.classList.toggle("filter-active", n > 0);
  };
  const refreshMaster = () => {
    if (!master) return;
    const n = matFilter[key].size, total = boxes.length;
    master.checked = n === total && total > 0;
    master.indeterminate = n > 0 && n < total;   // algunos, no todos
  };
  refreshLabel();
  refreshMaster();

  btn.onclick = (e) => {
    e.stopPropagation();
    const wasOpen = !panel.hidden;
    closeAllPanels();
    panel.hidden = wasOpen;
  };
  panel.onclick = (e) => e.stopPropagation();

  boxes.forEach((chk) =>
    chk.addEventListener("change", () => {
      chk.checked ? matFilter[key].add(chk.value) : matFilter[key].delete(chk.value);
      refreshLabel(); refreshMaster();
      renderMaterialsTable();
    }));

  if (master) master.addEventListener("change", () => {
    // Excel: si no está todo marcado, marca todo; si lo está, lo quita.
    const selectAll = matFilter[key].size < boxes.length;
    matFilter[key].clear();
    boxes.forEach((chk) => {
      chk.checked = selectAll;
      if (selectAll) matFilter[key].add(chk.value);
    });
    refreshLabel(); refreshMaster();
    renderMaterialsTable();
  });
}

function closeAllPanels() {
  document.querySelectorAll(".filter-panel").forEach((p) => (p.hidden = true));
}
document.addEventListener("click", closeAllPanels);

// Tags de las impresoras donde el material está calibrado, coloreados por nivel.
const CAL_TAG_TITLE = { FULL: "calibrado a fondo (PA/flow)", BASIC: "solo temperaturas",
                        CALIBRATED: "deducido del historial" };
function calibratedTags(materialId) {
  const byPrinter = calByMaterial[materialId];
  if (!byPrinter) return `<span class="muted">—</span>`;
  return Object.entries(byPrinter)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([printer, level]) =>
      `<span class="pill cal-${level}" title="${escHtml(printer)}: ${CAL_TAG_TITLE[level]}">${escHtml(printer)}</span>`)
    .join(" ");
}

// Fecha relativa compacta para "último uso".
function agoLabel(iso) {
  if (!iso) return `<span class="muted">nunca</span>`;
  const d = new Date(iso), days = Math.floor((Date.now() - d) / 86400000);
  let txt;
  if (days <= 0) txt = "hoy";
  else if (days === 1) txt = "ayer";
  else if (days < 30) txt = `hace ${days} d`;
  else if (days < 365) txt = `hace ${Math.floor(days / 30)} mes`;
  else txt = `hace ${Math.floor(days / 365)} año${days >= 730 ? "s" : ""}`;
  return `<span title="${d.toLocaleString()}">${txt}</span>`;
}

// Orden de la tabla de materiales. key null = orden natural (nombre, del API).
let matSort = { key: null, dir: "asc" };
function matSortValue(m, key) {
  const u = matUsage[m.id] || {};
  return ({
    name: m.name.toLowerCase(),
    price: m.price_per_kg || 0,
    last_used: u.last_used ? new Date(u.last_used).getTime() : -1,  // nunca = al fondo
  })[key];
}

function renderMaterialsTable() {
  const q = (document.getElementById("mat-search")?.value || "").toLowerCase();
  const f = matFilter;
  const filas = materials.filter((m) =>
    (!f.type.size || f.type.has(m.material_type)) &&
    (!f.brand.size || f.brand.has(m.brand || NO_BRAND)) &&
    (!f.stock.size || f.stock.has(m.stock_level || "unknown")) &&
    (!f.color.size || f.color.has(m.color_hex || NO_COLOR)) &&
    (!q || `${m.name} ${m.brand || ""} ${m.color || ""}`.toLowerCase().includes(q)));

  if (matSort.key) {
    const sign = matSort.dir === "asc" ? 1 : -1;
    filas.sort((a, b) => {
      const x = matSortValue(a, matSort.key), y = matSortValue(b, matSort.key);
      return x < y ? -sign : x > y ? sign : 0;
    });
  }

  const cnt = document.getElementById("mat-count");
  if (cnt) cnt.textContent = `${filas.length} de ${materials.length} materiales`;

  const arrow = (k) => matSort.key === k ? (matSort.dir === "asc" ? " ▲" : " ▼") : "";
  const th = (k, label, cls = "") =>
    `<th class="sortable ${cls}" data-msort="${k}">${label}${arrow(k)}</th>`;

  document.getElementById("materials-table").innerHTML =
    `<tr>${th("name", "Nombre")}<th>Impresoras</th><th>Tipo</th><th>Marca</th><th>Color</th>
     ${th("price", "Precio/kg", "num")}<th>Stock</th>${th("last_used", "Último uso")}<th></th><th></th></tr>` +
    (filas.length ? filas.map((m) => {
      const noPrice = (m.price_per_kg || 0) <= 0
        ? ` <span class="pill review">poner precio</span>` : "";
      const auto = m.auto_created ? ` <span class="pill" style="background:rgba(79,140,255,.15);color:var(--accent)">auto</span>` : "";
      const buy = m.purchase_url
        ? `<a class="btn ghost small" href="${escHtml(m.purchase_url)}" target="_blank" rel="noopener" title="Recomprar">🛒</a>` : "";
      const sw = m.color_hex ? `<span class="swatch" style="background:${escHtml(m.color_hex)}"></span>` : "";
      const u = matUsage[m.id] || {};
      return `<tr>
        <td>${sw}${escHtml(m.name)}${auto}</td>
        <td class="cal-tags">${calibratedTags(m.id)}</td>
        <td>${escHtml(m.material_type)}</td>
        <td class="muted">${escHtml(m.brand || "—")}</td><td class="muted">${escHtml(m.color || "—")}</td>
        <td class="num">${money(m.price_per_kg)}${noPrice}</td>
        <td>${stockDot(m.stock_level)}</td>
        <td title="${u.count ? u.count + " impresiones" : "sin impresiones"}">${agoLabel(u.last_used)}</td>
        <td>${buy}</td>
        <td class="row-actions">
          <button class="btn ghost small" data-edit-mat="${m.id}">Editar</button>
          <button class="btn danger small" data-del-mat="${m.id}">✕</button>
        </td></tr>`;
    }).join("") : `<tr><td colspan="10" class="muted">Sin materiales con ese filtro</td></tr>`);

  // Vista móvil: tarjetas con los mismos data-* (el cableado engancha ambas).
  document.getElementById("materials-cards").innerHTML = filas.length ? filas.map((m) => {
    const sw = m.color_hex ? `<span class="swatch" style="background:${escHtml(m.color_hex)}"></span>` : "";
    const noPrice = (m.price_per_kg || 0) <= 0 ? ` <span class="pill review">poner precio</span>` : "";
    const u = matUsage[m.id] || {};
    return `<div class="job-card">
      <div class="jc-top">
        <div class="jc-title"><span class="job-name">${sw}${escHtml(m.name)}</span>
          <div class="muted">${escHtml(m.material_type)}${m.brand ? " · " + escHtml(m.brand) : ""}${m.color ? " · " + escHtml(m.color) : ""}</div></div>
        <strong class="cost-val ${(m.price_per_kg || 0) > 0 ? "ok" : "warn"}">${money(m.price_per_kg)}/kg</strong>
      </div>
      <div class="jc-meta muted">${stockDot(m.stock_level)} · ${agoLabel(u.last_used)}${noPrice}</div>
      <div class="jc-actions">
        ${m.purchase_url ? `<a class="btn ghost small" href="${escHtml(m.purchase_url)}" target="_blank" rel="noopener">🛒</a>` : ""}
        <span class="spacer"></span>
        <button class="btn ghost small" data-edit-mat="${m.id}">Editar</button>
        <button class="btn danger small" data-del-mat="${m.id}">✕</button>
      </div>
    </div>`;
  }).join("") : `<p class="muted">Sin materiales con ese filtro</p>`;

  document.querySelectorAll("#materials-table th[data-msort]").forEach((h) =>
    h.addEventListener("click", () => {
      const k = h.dataset.msort;
      if (matSort.key === k) matSort.dir = matSort.dir === "asc" ? "desc" : "asc";
      else matSort = { key: k, dir: k === "last_used" ? "desc" : "asc" };  // fecha: reciente primero
      renderMaterialsTable();
    }));

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

// Editor de fotos del producto terminado, para un pedido o proyecto.
async function entityPhotoEditor(host, entityType, entityId) {
  let fotos = [];
  try { fotos = await api.get(`/api/entity-photos?entity_type=${entityType}&entity_id=${entityId}`); } catch { /* sin fotos */ }
  host.innerHTML = `
    <div class="photo-head"><span class="muted">${fotos.length}/8</span>
      <button type="button" class="btn ghost small" id="ep-add" ${fotos.length >= 8 ? "disabled" : ""}>+ Foto</button>
      <input type="file" id="ep-file" accept="image/*" multiple hidden>
    </div>
    <div class="photo-row">${fotos.map((f) => `<div class="photo-tile">
      <img src="${f.url}" alt="" onclick="window.open('${f.url}','_blank')" title="Ver grande">
      <button type="button" class="photo-del" data-del-ephoto="${f.id}" title="Quitar">✕</button>
    </div>`).join("") || '<span class="muted">Sin fotos</span>'}</div>`;

  host.querySelectorAll("[data-del-ephoto]").forEach((b) =>
    b.addEventListener("click", async () => {
      await api.send(`/api/entity-photos/${b.dataset.delEphoto}`, "DELETE");
      entityPhotoEditor(host, entityType, entityId);
    }));
  const addBtn = host.querySelector("#ep-add");
  const fileInput = host.querySelector("#ep-file");
  if (addBtn) addBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const files = [...fileInput.files].slice(0, 8 - fotos.length);
    fileInput.value = "";
    for (const f of files) {
      try {
        await api.send("/api/entity-photos", "POST",
          { entity_type: entityType, entity_id: entityId, data_uri: await fileToDataUri(f) });
      } catch (e) { toast("Error subiendo: " + e.message); }
    }
    entityPhotoEditor(host, entityType, entityId);
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
document.getElementById("mat-clear").addEventListener("click", () => {
  document.getElementById("mat-search").value = "";
  Object.values(matFilter).forEach((set) => set.clear());
  buildMatFilters();            // reconstruye los desplegables (desmarca todo)
  updateColorBtn();
  renderMaterialsTable();
});
document.getElementById("mat-color-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  openColorModal();
});

// --- Filtro por color: modal con las muestras de la BD -----------------------
function updateColorBtn() {
  const n = matFilter.color.size;
  const btn = document.getElementById("mat-color-btn");
  btn.textContent = n ? `Color (${n}) ▾` : "Color ▾";
  btn.classList.toggle("filter-active", n > 0);
}

function openColorModal() {
  // Muestras únicas por hex, con un nombre representativo.
  const byHex = new Map();
  let hasNoColor = false;
  for (const m of materials) {
    if (m.color_hex) {
      if (!byHex.has(m.color_hex)) byHex.set(m.color_hex, m.color || m.name);
    } else hasNoColor = true;
  }
  const swatches = [...byHex.entries()].sort((a, b) => a[1].localeCompare(b[1]));

  const ov = document.getElementById("color-modal");
  ov.innerHTML = `<div class="modal-box">
    <div class="toolbar"><strong>Filtrar por color</strong>
      <span class="muted">${swatches.length} colores</span>
      <span class="spacer"></span>
      <button class="btn ghost small" id="cm-clear">Quitar filtro</button>
      <button class="btn ghost small" id="cm-close">Cerrar</button></div>
    <div class="color-grid">
      ${swatches.map(([hex, name]) => `<button type="button" class="color-cell ${matFilter.color.has(hex) ? "sel" : ""}" data-hex="${escHtml(hex)}" title="${escHtml(name)}">
        <span class="swatch big" style="background:${escHtml(hex)}"></span>
        <span class="color-name">${escHtml(name)}</span></button>`).join("")}
      ${hasNoColor ? `<button type="button" class="color-cell ${matFilter.color.has(NO_COLOR) ? "sel" : ""}" data-hex="${NO_COLOR}" title="Sin color">
        <span class="swatch big none"></span><span class="color-name">Sin color</span></button>` : ""}
    </div>
    <p class="muted" style="font-size:.78rem">Toca los colores que quieras incluir.</p>
  </div>`;
  ov.hidden = false;

  ov.querySelector(".modal-box").onclick = (e) => e.stopPropagation();
  ov.onclick = () => (ov.hidden = true);
  ov.querySelector("#cm-close").onclick = () => (ov.hidden = true);
  ov.querySelector("#cm-clear").onclick = () => {
    matFilter.color.clear(); updateColorBtn(); renderMaterialsTable();
    ov.querySelectorAll(".color-cell.sel").forEach((c) => c.classList.remove("sel"));
  };
  ov.querySelectorAll(".color-cell").forEach((cell) =>
    cell.addEventListener("click", () => {
      const hex = cell.dataset.hex;
      matFilter.color.has(hex) ? matFilter.color.delete(hex) : matFilter.color.add(hex);
      cell.classList.toggle("sel");
      updateColorBtn();
      renderMaterialsTable();
    }));
}

// --- Estimación + Presupuesto ------------------------------------------------
// --- Estimación de producto (proyecto multi-gcode, promedio de flota) --------
// El coste de imprimir se estima promediando el coste/hora de TODA la flota,
// sin fijar máquina; el material (por archivo) marca el filamento y la potencia.
let estFiles = [];      // {filename, weight_g, time_s, filament_type, thumb, material_id, quantity}
let estResult = null;   // última respuesta de /api/estimate/project
let estSeq = 0;         // secuencia: descarta respuestas que lleguen tarde
const vEst = (id) => document.getElementById(id).value;

async function loadEstimacion() {
  await ensureRefs();   // materiales, para los desplegables
  renderEstFiles();
  recalcEstimate();
}

// Preselección: material del tipo del gcode, priorizando uno que tenga precio.
function pickMaterialForType(ftype) {
  if (!ftype) return null;
  const t = ftype.toUpperCase();
  const same = materials.filter((m) => (m.material_type || "").toUpperCase() === t);
  return (same.find((m) => m.price_per_kg > 0) || same[0] || {}).id ?? null;
}

function estMatOptions(sel) {
  return `<option value="">— material —</option>` + materials.map((m) =>
    `<option value="${m.id}" ${m.id === sel ? "selected" : ""}>${escHtml(m.name)}${m.price_per_kg > 0 ? "" : " (sin precio)"}</option>`).join("");
}

function renderEstFiles() {
  const host = document.getElementById("est-files");
  if (!estFiles.length) {
    host.innerHTML = `<p class="muted">Sube uno o varios gcodes para empezar (o añade un extra).</p>`;
    return;
  }
  host.innerHTML = estFiles.map((f, i) => {
    if (f.is_extra) {
      // Extra sin gcode (tornillería, pegamento…): 🔧 en lugar de miniatura,
      // concepto e importe editables. Coste fijo, no depende de la flota.
      return `<div class="card est-row" data-i="${i}">
        <div class="est-thumb tool">🔧</div>
        <div class="est-info">
          <input data-est-label placeholder="Concepto (argolla, tornillería, imán…)" value="${escHtml(f.filename || "")}">
          <div class="muted">extra POR UNIDAD producida (escala con la cantidad del producto)</div>
        </div>
        <label class="field est-mat" style="margin:0"><span>Precio c/u</span>
          <input type="number" step="0.01" min="0" data-est-amount value="${f.extra_amount ?? ""}"></label>
        <label class="field est-qty" style="margin:0"><span>Por unidad</span>
          <input type="number" min="1" data-est-qty title="Cuántas lleva CADA unidad del producto (p.ej. 1 argolla por llavero)" value="${f.quantity}"></label>
        <div class="est-cost num" data-est-cost></div>
        <button class="btn danger small" data-est-del title="Quitar">✕</button>
      </div>`;
    }
    const thumb = f.thumb ? `<img class="est-thumb" src="${f.thumb}" alt="">` : `<div class="est-thumb"></div>`;
    const w = f.weight_g != null ? f.weight_g.toFixed(0) + " g" : "— g";
    const t = f.time_s ? fmtDur(f.time_s) : "— tiempo";
    return `<div class="card est-row" data-i="${i}">
      ${thumb}
      <div class="est-info">
        <div class="est-name" title="${escHtml(f.filename)}">${escHtml(f.filename)}</div>
        <div class="muted">${w} · ${t}${f.filament_type ? " · " + escHtml(f.filament_type) : ""}</div>
      </div>
      <label class="field est-mat" style="margin:0"><span>Material</span>
        <select data-est-mat>${estMatOptions(f.material_id)}</select></label>
      <label class="field est-qty" style="margin:0"><span>Cantidad</span>
        <input type="number" min="1" data-est-qty value="${f.quantity}"></label>
      <div class="est-cost num" data-est-cost></div>
      <button class="btn danger small" data-est-del title="Quitar">✕</button>
    </div>`;
  }).join("");

  host.querySelectorAll(".est-row").forEach((row) => {
    const i = +row.dataset.i;
    row.querySelector("[data-est-mat]")?.addEventListener("change", (e) => {
      if (e.target.tagName !== "SELECT") return;
      estFiles[i].material_id = e.target.value ? +e.target.value : null; recalcEstimate();
    });
    row.querySelector("[data-est-label]")?.addEventListener("input", (e) => {
      estFiles[i].filename = e.target.value;
    });
    row.querySelector("[data-est-amount]")?.addEventListener("input", (e) => {
      estFiles[i].extra_amount = parseFloat(e.target.value) || 0; recalcEstimate();
    });
    row.querySelector("[data-est-qty]").addEventListener("input", (e) => {
      estFiles[i].quantity = Math.max(1, parseInt(e.target.value) || 1); recalcEstimate();
    });
    row.querySelector("[data-est-del]").addEventListener("click", () => {
      estFiles.splice(i, 1); renderEstFiles(); recalcEstimate();
    });
  });
}

// Base de coste elegida → (ponderación, base) para el backend y su etiqueta.
const EST_BASIS = {
  max: { weighting: "usage", basis: "max", label: "máximo de flota" },
  usage: { weighting: "usage", basis: "avg", label: "promedio ponderado" },
  simple: { weighting: "simple", basis: "avg", label: "promedio simple" },
  min: { weighting: "usage", basis: "min", label: "mínimo de flota" },
};

async function recalcEstimate() {
  const costHost = document.getElementById("est-cost");
  if (!estFiles.length) { costHost.innerHTML = ""; estResult = null; recalcSale(); return; }
  const mode = EST_BASIS[vEst("est-basis")] || EST_BASIS.max;
  // gcodes y extras viajan por separado; se guarda el índice original de cada
  // uno para pintar su coste en la fila correcta al volver la respuesta.
  const fileIdx = [], extraIdx = [];
  estFiles.forEach((f, i) => (f.is_extra ? extraIdx : fileIdx).push(i));
  const body = {
    weighting: mode.weighting, basis: mode.basis,
    files: fileIdx.map((i) => { const f = estFiles[i]; return {
      filename: f.filename, weight_g: f.weight_g || 0, time_s: f.time_s || 0,
      // Se manda también el tipo del gcode: si no hay material de biblioteca,
      // la potencia usa igualmente el factor térmico del material.
      quantity: f.quantity, material_id: f.material_id, material_type: f.filament_type,
    }; }),
    extras: extraIdx.map((i) => { const f = estFiles[i]; return {
      label: f.filename || "extra", amount: f.extra_amount || 0, quantity: f.quantity,
    }; }),
  };
  const seq = ++estSeq;
  let d;
  try { d = await api.send("/api/estimate/project", "POST", body); }
  catch (e) { if (seq === estSeq) costHost.innerHTML = `<p class="muted">No se pudo estimar.</p>`; return; }
  if (seq !== estSeq) return;   // llegó una respuesta más nueva: descarta esta
  estResult = d; currency = d.currency;

  const paint = (rowI, html) => {
    const cell = document.querySelector(`.est-row[data-i="${rowI}"] [data-est-cost]`);
    if (cell) cell.innerHTML = html;
  };
  d.files.forEach((fc, k) => paint(fileIdx[k],
    `${money(fc.unit_cost)}/u<div class="muted">×${fc.quantity} = ${money(fc.line_cost)}</div>`
    + (fc.no_price ? `<span class="pill review">sin precio</span>` : "")));
  (d.extras || []).forEach((ec, k) => paint(extraIdx[k],
    `${money(ec.amount)}/u<div class="muted">×${ec.quantity} = ${money(ec.line_cost)}</div>`));

  const spread = d.cost_high > d.cost_low + 0.005;
  costHost.innerHTML = `<div class="card">
    <div class="est-total-row">
      <div><div class="label">Coste del producto (${mode.label})</div>
        <div class="value">${money(d.cost_total)}</div></div>
      ${spread ? `<div class="muted">según máquina: ${money(d.cost_low)} – ${money(d.cost_high)}</div>` : ""}
    </div>
    ${estFleetNote(d)}
  </div>`;
  recalcSale();
}

function estFleetNote(d) {
  const parts = Object.entries(d.fleet).filter(([, v]) => v).map(([k, v]) =>
    `<b>${escHtml(k || "—")}</b> ${money(v.avg_per_h)}/h ${v.weighted ? "pond." : "simple"} (${v.n_machines} máq) · ${money(v.min_per_h)} ${escHtml(v.min_machine)} → ${money(v.max_per_h)} ${escHtml(v.max_machine)}`);
  return parts.length ? `<div class="muted est-fleet">Coste/hora de flota — ${parts.join(" · ")}</div>` : "";
}

function recalcSale() {
  const cost = estResult ? estResult.cost_total : 0;
  const extras = estResult ? (estResult.extras_total || 0) : 0;
  const qty = Math.max(1, parseInt(vEst("est-qty")) || 1);
  const rate = parseFloat(vEst("est-rate")) || 0;
  const hours = parseFloat(vEst("est-hours")) || 0;
  const postMin = parseFloat(vEst("est-postmin")) || 0;
  const failure = parseFloat(vEst("est-failure")) || 0;
  const margin = parseFloat(vEst("est-margin")) || 0;

  // El recargo por fallo aplica SOLO a la parte impresa: una argolla o un imán
  // no se pierden cuando una impresión falla.
  const printing = (cost - extras) * (1 + failure / 100);
  const labor = hours * rate;
  const post = (postMin / 60) * rate;            // post-procesado a la misma tarifa
  // El margen aplica SOLO al trabajo de impresión. Los extras se repercuten a
  // coste. Impresión, margen y extras son POR UNIDAD y escalan con la cantidad;
  // la mano de obra y el post-procesado son del TRABAJO COMPLETO (las horas que
  // pones son del lote entero) y entran una sola vez.
  const marginAmt = printing * (margin / 100);          // por unidad
  const perUnit = printing + marginAmt + extras;         // por unidad
  const total = perUnit * qty + labor + post;            // lote completo
  const profitTotal = marginAmt * qty;

  document.getElementById("est-sale").innerHTML = `<table style="margin-top:.8rem">
    <tr><td colspan="2" class="muted" style="font-size:.78rem">Por unidad</td></tr>
    <tr><td>Coste de impresión${failure ? ` (+${failure}% fallo)` : ""}</td><td class="num">${money(printing)}</td></tr>
    <tr><td>+ margen (${margin}% sobre la impresión)</td><td class="num">${money(marginAmt)}</td></tr>
    ${extras ? `<tr><td>+ extras por unidad (a coste, sin margen)</td><td class="num">${money(extras)}</td></tr>` : ""}
    <tr><td>Subtotal por unidad × ${qty}</td><td class="num">${money(perUnit * qty)}</td></tr>
    <tr><td colspan="2" class="muted" style="font-size:.78rem">Del trabajo completo (una sola vez)</td></tr>
    <tr><td>+ mano de obra (${hours} h × ${money(rate)}/h)</td><td class="num">${money(labor)}</td></tr>
    <tr><td>+ post-procesado (${postMin} min)</td><td class="num">${money(post)}</td></tr>
    <tr><td><strong>TOTAL (${qty} ud.)</strong></td><td class="num"><strong>${money(total)}</strong></td></tr>
    <tr><td class="muted">Precio unitario efectivo</td><td class="num muted">${money(total / qty)}</td></tr>
    <tr><td class="muted">Beneficio total (margen de impresión)</td><td class="num muted">${money(profitTotal)}</td></tr>
  </table>`;
}

document.getElementById("est-add").addEventListener("click", () =>
  document.getElementById("est-input").click());
document.getElementById("est-input").addEventListener("change", async (e) => {
  for (const file of [...e.target.files]) {
    try {
      const d = await parseGcode(file);
      estFiles.push({
        filename: d.filename, weight_g: d.filament_g, time_s: d.estimated_time_s,
        filament_type: d.filament_type, thumb: d.thumbnail_uri,
        material_id: pickMaterialForType(d.filament_type), quantity: 1,
      });
    } catch (err) { toast("No se pudo leer " + file.name + ": " + err.message); }
  }
  e.target.value = "";
  renderEstFiles(); recalcEstimate();
});
document.getElementById("est-basis").addEventListener("change", recalcEstimate);
// PDF de cotización PROPIO de la Estimación: misma plantilla de marca que el de
// Cotización, pero con los datos y el modelo de esta pestaña (margen solo sobre
// la impresión, extras a coste, mano de obra del lote una sola vez). No toca la
// pestaña Cotización ni sus ítems.
function printEstimacion() {
  if (!estFiles.length || !estResult) return toast("No hay nada que cotizar");
  const qty = Math.max(1, parseInt(vEst("est-qty")) || 1);
  const rate = parseFloat(vEst("est-rate")) || 0;
  const hours = parseFloat(vEst("est-hours")) || 0;
  const postMin = parseFloat(vEst("est-postmin")) || 0;
  const failure = parseFloat(vEst("est-failure")) || 0;
  const margin = parseFloat(vEst("est-margin")) || 0;

  // Reconstruye el mismo cálculo de recalcSale, pero por línea: a cada gcode su
  // parte de margen; los extras a coste; el trabajo del lote aparte.
  const extrasTotal = estResult.extras_total || 0;
  const labor = hours * rate;
  const post = (postMin / 60) * rate;
  const failMul = 1 + failure / 100;
  const marMul = 1 + margin / 100;

  const conThumb = estFiles.some((f) => f.thumb);
  let unidadesTot = 0;
  const filaIdx = { f: 0, e: 0 };
  const filas = estFiles.map((f, i) => {
    let nombre, tipo, unit, unitsPerProd;
    if (f.is_extra) {
      const ec = (estResult.extras || [])[filaIdx.e++] || {};
      nombre = `🔧 ${f.filename || "extra"}`;
      tipo = "extra";
      unit = ec.amount || f.extra_amount || 0;      // a coste, sin margen
      unitsPerProd = f.quantity || 1;
    } else {
      const fc = (estResult.files || [])[filaIdx.f++] || {};
      nombre = f.filename || "pieza";
      tipo = fc.material || f.filament_type || "—";
      // unitario del PDF = coste de impresión con fallo y margen aplicados
      unit = (fc.unit_cost || 0) * failMul * marMul;
      unitsPerProd = f.quantity || 1;
    }
    const cant = unitsPerProd * qty;
    unidadesTot += cant;
    const thumb = f.thumb ? `<img src="${escHtml(f.thumb)}" alt="">` : "";
    return `<tr>
      ${conThumb ? `<td class="thumb">${f.is_extra ? "🔧" : thumb}</td>` : ""}
      <td>${i + 1}. ${escHtml(nombre)}</td>
      <td>${escHtml(String(tipo))}</td>
      <td class="num">${money(unit)}</td>
      <td class="num">${cant}</td>
      <td class="num">${money(unit * cant)}</td>
    </tr>`;
  }).join("");

  const printedBase = (estResult.cost_total - extrasTotal) * failMul;
  const marginAmt = printedBase * (margin / 100);
  const perUnit = printedBase + marginAmt + extrasTotal;
  const total = perUnit * qty + labor + post;
  const s = appSettings || {};
  const qnum = "E-" + Math.random().toString(16).slice(2, 8).toUpperCase();
  const cliente = "";

  const trabajoRows =
    (labor > 0 ? `<tr><td>Mano de obra (${hours} h)</td><td class="num">${money(labor)}</td></tr>` : "") +
    (post > 0 ? `<tr><td>Post-procesado (${postMin} min)</td><td class="num">${money(post)}</td></tr>` : "");

  printDoc(`Cotización ${qnum}`, `
    ${brandHead(s, "Impresión 3D", "Cotización")}
    <div class="meta">
      <span><strong>Cotización #:</strong> ${qnum}</span>
      ${cliente ? `<span><strong>Cliente:</strong> ${escHtml(cliente)}</span>` : ""}
      <span><strong>Fecha:</strong> ${new Date().toLocaleDateString()}</span>
      <span><strong>Piezas:</strong> ${unidadesTot}</span>
    </div>
    <table>
      <tr>${conThumb ? "<th></th>" : ""}<th>Descripción</th><th>Material</th>
          <th class="num">Costo unitario</th><th class="num">Cantidad</th><th class="num">Total</th></tr>
      ${filas}
    </table>
    <table class="totals">
      <tr><td>Sub-Total piezas (${qty} producto${qty > 1 ? "s" : ""})</td><td class="num">${money(perUnit * qty)}</td></tr>
      ${trabajoRows}
      <tr class="grand"><td>Total</td><td class="num">${money(total)}</td></tr>
    </table>
    ${brandTerms(s)}
    <div class="foot">${escHtml(s.company_name || "M3D Nexus")} — Transforma tus ideas en realidad con impresión 3D</div>
  `);
}
document.getElementById("est-to-quote").addEventListener("click", printEstimacion);

document.getElementById("est-add-extra").addEventListener("click", () => {
  estFiles.push({ is_extra: true, filename: "", extra_amount: 0, quantity: 1 });
  renderEstFiles(); recalcEstimate();
  // El concepto vacío está listo para teclear.
  document.querySelector(`.est-row[data-i="${estFiles.length - 1}"] [data-est-label]`)?.focus();
});
["est-qty", "est-rate", "est-hours", "est-postmin", "est-failure", "est-margin"].forEach((id) =>
  document.getElementById(id).addEventListener("input", recalcSale));

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
// Piezas desplegadas (ids), para que el refresco cada 8 s no las cierre.
const openPieces = new Set();

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

// Margen generado: suma del margen de los pedidos pagados o con anticipo (sin
// cancelados). Se calcula sobre TODOS los pedidos, no sobre los filtrados.
function renderOrdersSummary() {
  let margin = 0, received = 0, n = 0, cur = currency;
  for (const o of orders) {
    if (o.manual_status === "cancelled") continue;
    if (!["paid", "deposit"].includes(o.payment_status)) continue;
    n++;
    cur = o.currency || cur;
    if (o.margin) margin += o.margin.profit;
    received += o.payment_status === "paid" ? (o.agreed_price || 0) : (o.deposit_amount || 0);
  }
  const el = document.getElementById("ped-summary");
  el.innerHTML = n
    ? `<span class="ped-margin" title="${n} pedidos pagados o con anticipo · recibido ${money2(received, cur)}">Margen generado: <strong>${money2(margin, cur)}</strong></span>`
    : "";
}

function renderOrders() {
  renderOrdersSummary();
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
    const items = o.items.map((it, idx) => {
      const printing = it.status === "printing";
      const prog = printing && it.progress != null
        ? `<div class="progress mini"><div class="bar" style="width:${Math.round(it.progress * 100)}%"></div><span>${Math.round(it.progress * 100)}%</span></div>` : "";
      const eta = printing && it.eta_s != null
        ? `<span class="muted oi-eta" title="tiempo restante estimado">⏳ ${fmtEta(it.eta_s)}</span>` : "";
      const copies = it.quantity > 1 ? ` <span class="muted">${it.printed}/${it.quantity}</span>` : "";
      // Chips por copia interactivos: clic cicla el estado; el botón Guardar
      // aparece al tocar alguno y persiste solo al pulsarlo.
      const chips = Array.from({ length: it.quantity }, (_, ci) => {
        const s = (it.copy_status && it.copy_status[ci]) || "pending";
        return `<button type="button" class="copy-chip board-chip cs-${s}" data-s="${s}" title="Copia ${ci + 1}: ${COPY_LABEL[s]} (clic para cambiar)">${ci + 1}</button>`;
      }).join("");
      const copyChips = `<span class="board-copies" data-oid="${o.id}" data-idx="${idx}">${chips}
        <button type="button" class="btn small board-save" hidden>Guardar</button></span>`;
      // El nombre del gcode que se imprime; la etiqueta (si hay) va encima.
      const gname = (it.gcode_filename || "").split("/").pop() || "sin gcode";
      const name = it.label
        ? `<span class="oi-name">${escHtml(it.label)}</span><span class="muted oi-gcode" title="${escHtml(it.gcode_filename || "")}">${escHtml(gname)}</span>`
        : `<span class="oi-name" title="${escHtml(it.gcode_filename || "")}">${escHtml(gname)}</span>`;
      // Nombre de impresora como enlace a su interfaz Klipper.
      const pr = printers.find((p) => p.id === it.printer_id);
      const ku = pr && klipperUrl(pr);
      const printer = it.printer_name
        ? (ku ? `<a class="muted" href="${ku}" target="_blank" rel="noopener" title="Abrir ${escHtml(pr.name)}">${escHtml(it.printer_name)} ↗</a>`
              : `<span class="muted">${escHtml(it.printer_name)}</span>`)
        : `<span class="muted">—</span>`;
      // Coste de imprimir la pieza: coste del gcode × cantidad.
      // Ámbar si el material no tiene precio (el coste va incompleto).
      const itemCost = it.unit_cost != null
        ? (it.no_price
          ? `<span class="oi-cost cost-val warn" data-fix-mat="${it.est_material_id || ""}" data-fix-type="${escHtml(it.est_material || "")}" title="Falta el precio/kg${it.est_material ? " de " + escHtml(it.est_material) : ""} — clic para ponerlo">${money2(it.unit_cost * it.quantity, cur)}</span>`
          : `<span class="oi-cost cost-val ok" title="${money2(it.unit_cost, cur)} c/u × ${it.quantity}">${money2(it.unit_cost * it.quantity, cur)}</span>`)
        : "";
      // Miniatura del gcode (última impresión guardada); crece al desplegar.
      const thumb = it.thumbnail_url
        ? `<img class="oi-thumb" src="${it.thumbnail_url}" loading="lazy" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'oi-thumb ph'}))">`
        : `<div class="oi-thumb ph"></div>`;
      // Panel de detalle (oculto hasta desplegar la pieza): tabla etiqueta/valor
      // con renglones definidos, más legible que una rejilla suelta.
      const drow = (k, v) => v == null || v === "" ? "" : `<dt>${k}</dt><dd>${v}</dd>`;
      const expLines = (it.extra_expenses || []).map((e) =>
        drow(escHtml(e.label || "gasto"), money2((e.amount || 0) * (e.quantity || 1), cur))).join("");
      const bigThumb = it.thumbnail_url
        ? `<img class="oi-detail-thumb" src="${it.thumbnail_url}" loading="lazy" alt="">`
        : `<div class="oi-detail-thumb ph"></div>`;
      // El nombre del gcode es el enlace a su última impresión (color de acento).
      const gcodeCell = it.gcode_filename
        ? `<span class="gcode-nav" data-goto-gcode="${escHtml(it.gcode_filename)}" title="Ver la última impresión de este gcode">${escHtml(gname)} →</span>`
        : escHtml(gname);
      const detail = `<div class="oi-detail">
        <button class="btn danger small oi-del" data-del-item="${it.id}"
          data-name="${escHtml(it.label || gname)}" title="Quitar esta pieza del pedido">✕ Quitar</button>
        ${bigThumb}
        <div class="oi-detail-body">
          <dl class="oi-dl">
            ${drow("Gcode", gcodeCell)}
            ${drow("Impresora", it.printer_name ? escHtml(it.printer_name) : null)}
            ${drow("Material", it.est_material ? escHtml(it.est_material) : null)}
            ${drow("Tiempo est.", it.est_time_s ? fmtDur(it.est_time_s) : null)}
            ${drow("Filamento", it.filament_g ? it.filament_g.toFixed(0) + " g" : null)}
            ${drow("Coste unitario", it.unit_cost != null ? money2(it.unit_cost, cur) : null)}
            ${drow("Cantidad", it.quantity)}
            ${drow("Coste total", it.unit_cost != null ? `<strong>${money2(it.unit_cost * it.quantity, cur)}</strong>` : null)}
            ${drow("Impresas", `${it.printed}/${it.quantity}`)}
            ${expLines}
          </dl>
        </div>
      </div>`;
      return `<div class="order-item-wrap" data-item-id="${it.id}">
        <div class="order-item" data-expand-item>
          ${thumb}
          <span class="oi-names">${name}</span>
          ${printer}
          ${statusPill(ITEM_STATUS, it.status)}${copies}
          ${copyChips}
          ${eta}
          ${prog}
          <span class="spacer"></span>
          ${itemCost}
          <span class="oi-caret" aria-hidden="true">▸</span>
        </div>
        ${detail}
      </div>`;
    }).join("");

    const margin = o.margin
      ? `<span class="muted" title="coste estimado ${money2(o.margin.cost, cur)} (incluye gastos extra)">margen ${money2(o.margin.profit, cur)} (${o.margin.margin_pct}%)</span>`
      : (o.agreed_price != null ? `<span class="muted">precio ${money2(o.agreed_price, cur)}</span>` : "");
    // Anticipo: recibido de cuánto, con lo que falta.
    const deposit = o.payment_status === "deposit" && o.deposit_amount != null
      ? `<span class="muted" title="pendiente ${money2((o.agreed_price || 0) - o.deposit_amount, cur)}">· anticipo ${money2(o.deposit_amount, cur)}${o.agreed_price ? " / " + money2(o.agreed_price, cur) : ""}</span>`
      : "";
    // Total de gastos extra del pedido (pedido + piezas), precio × cantidad.
    const sumExp = (lst) => (lst || []).reduce((a, e) => a + (e.amount || 0) * (e.quantity || 1), 0);
    const expTotal = sumExp(o.extra_expenses)
      + o.items.reduce((a, it) => a + sumExp(it.extra_expenses), 0);
    const expenses = expTotal > 0
      ? `<span class="muted" title="empaque, velitas, etc.">· gastos ${money2(expTotal, cur)}</span>` : "";
    // Post-procesado del pedido (mano de obra).
    const ppMin = o.postproc_minutes || 0;
    const postproc = o.postproc_cost > 0
      ? `<span class="muted" title="${Math.floor(ppMin/60)}h ${ppMin%60}m × ${money2(o.postproc_rate, cur)}/h">· post-proc ${money2(o.postproc_cost, cur)}</span>` : "";

    return `<div class="card order-card" id="ordercard-${o.id}">
      <div class="order-head">
        ${o.photo_url ? `<img class="entity-thumb" src="${o.photo_url}" onclick="window.open('${o.photo_url}','_blank')" alt="" title="Foto del producto">` : ""}
        <strong>#${o.id} · ${escHtml(o.client)}</strong>
        ${statusPill(ORDER_STATUS, o.status)}
        <span class="pill" style="background:var(--panel-2)">${PAY_STATUS[o.payment_status] || o.payment_status}</span>
        ${dueLabel(o.due_date)}
        <span class="spacer"></span>
        ${margin}${deposit}${expenses}${postproc}
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
    b.addEventListener("click", () => {
      const o = orders.find((x) => x.id == b.dataset.editOrder);
      // El formulario se abre DENTRO de la propia tarjeta, en su sitio.
      orderForm(o, document.getElementById(`ordercard-${o.id}`));
    }));
  host.querySelectorAll("[data-del-order]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("¿Borrar pedido?")) return;
      await api.send(`/api/orders/${b.dataset.delOrder}`, "DELETE"); loadPedidos();
    }));
  host.querySelectorAll("[data-goto-gcode]").forEach((el) =>
    el.addEventListener("click", () => goToJobsForGcode(el.dataset.gotoGcode)));
  host.querySelectorAll("[data-fix-mat], [data-fix-type]").forEach((b) =>
    b.addEventListener("click", (e) => {
      e.stopPropagation();   // no desplegar la pieza al pulsar el coste
      goToMaterialFix(b.dataset.fixMat, b.dataset.fixType);
    }));
  host.querySelectorAll("[data-del-item]").forEach((b) =>
    b.addEventListener("click", async (e) => {
      e.stopPropagation();   // no plegar la pieza al pulsar
      if (!confirm(`¿Quitar “${b.dataset.name}” de este pedido?`)) return;
      await api.send(`/api/order-items/${b.dataset.delItem}`, "DELETE");
      toast("Pieza quitada"); loadPedidos();
    }));
  // Reabre las piezas que estaban desplegadas antes del refresco.
  host.querySelectorAll(".order-item-wrap").forEach((w) => {
    if (openPieces.has(+w.dataset.itemId)) w.classList.add("open");
  });
  // Clic en la pieza: despliega/oculta su detalle. Se ignoran los controles
  // interactivos (chips de copia, guardar, enlaces). El estado se recuerda para
  // que el refresco automático del tablero no lo cierre.
  host.querySelectorAll(".order-item[data-expand-item]").forEach((row) =>
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, a, select, input, .board-copies")) return;
      const wrap = row.closest(".order-item-wrap");
      const id = +wrap.dataset.itemId;
      if (wrap.classList.toggle("open")) openPieces.add(id); else openPieces.delete(id);
    }));

  // Chips por copia interactivos en el tablero.
  host.querySelectorAll(".board-copies").forEach((box) => {
    const save = box.querySelector(".board-save");
    box.querySelectorAll(".board-chip").forEach((chip) =>
      chip.addEventListener("click", () => {
        const next = COPY_CYCLE[(COPY_CYCLE.indexOf(chip.dataset.s) + 1) % 3];
        chip.dataset.s = next;
        chip.className = `copy-chip board-chip cs-${next}`;
        chip.title = `Copia: ${COPY_LABEL[next]} (clic para cambiar)`;
        box.classList.add("dirty");   // pausa el refresco automático
        save.hidden = false;
      }));
    save.addEventListener("click", async () => {
      const chips = [...box.querySelectorAll(".board-chip")].map((c) => c.dataset.s);
      const o = orders.find((x) => x.id == box.dataset.oid);
      const body = orderToBody(o);
      body.items[+box.dataset.idx].copy_status = chips;
      try {
        await api.send(`/api/orders/${o.id}`, "PUT", body);
        toast("Estado guardado"); loadPedidos();
      } catch (e) { toast("Error: " + e.message); }
    });
  });
}

// Serializa un pedido del tablero al cuerpo que espera la API (para reenviarlo
// al guardar un cambio puntual, p.ej. el estado de una pieza).
function orderToBody(o) {
  return {
    client: o.client, description: o.description,
    payment_status: o.payment_status, agreed_price: o.agreed_price,
    currency: o.currency, due_date: o.due_date,
    manual_status: o.manual_status, notes: o.notes, folder: o.folder,
    deposit_amount: o.deposit_amount, postproc_rate: o.postproc_rate,
    postproc_minutes: o.postproc_minutes || 0,
    extra_expenses: o.extra_expenses || [],
    items: o.items.map((it) => ({
      label: it.label, printer_id: it.printer_id, gcode_filename: it.gcode_filename,
      quantity: it.quantity, manual_status: it.manual_status,
      copy_status: it.copy_status || [], extra_expenses: it.extra_expenses || [],
    })),
  };
}

function money2(v, cur) { return `${(v ?? 0).toFixed(2)} ${cur || currency}`; }

// --- Formulario de pedido con editor de piezas -------------------------------
// Chips de estado por copia. Cada copia cicla pendiente→imprimiendo→completado.
const COPY_CYCLE = ["pending", "printing", "done"];
const COPY_LABEL = { pending: "pendiente", printing: "imprimiendo", done: "completado" };

function renderCopyChips(host, count, statuses) {
  const st = [...(statuses || [])];
  while (st.length < count) st.push("pending");
  st.length = count;
  const done = st.filter((s) => s === "done").length;
  host.innerHTML = `<span class="copy-count muted">${done}/${count} completadas</span>` +
    st.map((s, i) => `<button type="button" class="copy-chip cs-${s}" data-i="${i}" data-s="${s}"
      title="Copia ${i + 1}: ${COPY_LABEL[s]} (clic para cambiar)">${i + 1}</button>`).join("");
  host.querySelectorAll(".copy-chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      const next = COPY_CYCLE[(COPY_CYCLE.indexOf(chip.dataset.s) + 1) % 3];
      // Se reconstruye para actualizar el contador manteniendo el resto.
      const cur = readCopyChips(host);
      cur[+chip.dataset.i] = next;
      renderCopyChips(host, count, cur);
    }));
}
function readCopyChips(host) {
  return [...host.querySelectorAll(".copy-chip")].map((c) => c.dataset.s);
}

// Editor de una lista de gastos {label, amount}: filas con concepto + importe,
// y un botón "+ Añadir gasto" que siempre queda al final.
function expenseEditor(host, expenses) {
  host.innerHTML = "";
  const add = el(`<button type="button" class="btn ghost small exp-add">+ Añadir gasto</button>`);
  const addRow = (e = {}) => {
    const row = el(`<div class="expense-row">
      <input class="exp-label" placeholder="Concepto (velitas, empaque…)" value="${escHtml(e.label || "")}">
      <input class="exp-qty" type="number" min="1" step="1" title="Cantidad" value="${e.quantity ?? 1}">
      <span class="exp-x muted">×</span>
      <input class="exp-amount" type="number" step="0.01" min="0" placeholder="c/u" title="Precio por unidad" value="${e.amount ?? ""}">
      <button type="button" class="btn ghost small exp-del">✕</button>
    </div>`);
    row.querySelector(".exp-del").onclick = () => row.remove();
    host.insertBefore(row, add);   // siempre por encima del botón
  };
  // El botón se añade ANTES de crear las filas: addRow lo usa como referencia
  // en insertBefore, así que debe existir ya en host (si no, NotFoundError).
  add.onclick = () => addRow();
  host.appendChild(add);
  (expenses || []).forEach(addRow);
}

// Lee las filas de gastos de un contenedor a [{label, amount}], sin las vacías.
function readExpenses(host) {
  return [...host.querySelectorAll(".expense-row")]
    .map((r) => ({
      label: r.querySelector(".exp-label").value.trim(),
      amount: parseFloat(r.querySelector(".exp-amount").value) || 0,
      quantity: Math.max(1, parseInt(r.querySelector(".exp-qty").value) || 1),
    }))
    .filter((e) => e.label || e.amount);
}

function orderForm(o = {}, host = document.getElementById("order-form-host")) {
  const inline = host !== document.getElementById("order-form-host");  // dentro de la tarjeta
  const pay = o.payment_status || "pending";
  const oms = o.manual_status || "";
  const due = o.due_date ? new Date(o.due_date).toISOString().slice(0, 10) : "";

  host.innerHTML = `<div class="${inline ? "order-edit" : "card order-edit"}" style="${inline ? "" : "margin-top:1rem"}">
    <!-- Cabecera fija: guardar/cancelar y las fotos siempre a mano, sin bajar
         hasta el final en un pedido con muchas piezas. -->
    <div class="form-head">
      <h2>${o.id ? "Editar" : "Nuevo"} pedido ${o.id ? "#" + o.id : ""}</h2>
      <span class="spacer"></span>
      ${o.id ? `<button class="btn ghost small" id="of-add-photo">+ Foto</button>` : ""}
      <button class="btn" id="of-save">Guardar</button>
      <button class="btn ghost" id="of-cancel">Cancelar</button>
    </div>
    <div class="form-grid">
      <label class="field"><span>Cliente</span><input id="of-client" value="${escHtml(o.client || "")}"></label>
      <label class="field"><span>Descripción</span><input id="of-desc" value="${escHtml(o.description || "")}"></label>
      <label class="field"><span>Estado de pago</span><select id="of-pay">
        ${Object.entries(PAY_STATUS).map(([k, v]) => `<option value="${k}" ${k === pay ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="field"><span>Precio acordado</span><input type="number" step="0.01" id="of-price" value="${o.agreed_price ?? ""}"></label>
      <label class="field" id="of-deposit-wrap" ${pay === "deposit" ? "" : "hidden"}><span>Monto recibido (anticipo)</span>
        <input type="number" step="0.01" id="of-deposit" value="${o.deposit_amount ?? ""}"></label>
      <label class="field"><span>Fecha de entrega</span><input type="date" id="of-due" value="${due}"></label>
      <label class="field"><span>Carpeta local (nº)</span><input id="of-folder" placeholder="${o.id || "p.ej. 112"}" value="${escHtml(o.folder || "")}"></label>
      <label class="field"><span>Post-procesado: costo/hora</span>
        <input type="number" step="0.01" min="0" id="of-postrate" placeholder="0.00" value="${o.postproc_rate ?? ""}"></label>
      <label class="field"><span>Post-procesado: tiempo total</span>
        <span class="pp-time">
          <input type="number" min="0" id="of-post-h" value="${Math.floor((o.postproc_minutes || 0) / 60)}"> <span class="muted">h</span>
          <input type="number" min="0" max="59" id="of-post-m" value="${(o.postproc_minutes || 0) % 60}"> <span class="muted">m</span>
        </span></label>
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
    <div class="order-expenses">
      <div class="muted" style="font-size:.82rem;margin:.6rem 0 .3rem"><strong>Gastos extra del pedido</strong>
        (empaque, envío… se restan del margen)</div>
      <div id="of-expenses"></div>
    </div>
    <div class="entity-photos">
      <div class="muted" style="font-size:.82rem;margin:.6rem 0 .3rem"><strong>Fotos del producto terminado</strong></div>
      <div id="of-photos">${o.id ? "" : '<span class="muted">Guarda el pedido primero para añadir fotos.</span>'}</div>
    </div>
  </div>`;

  const itemsHost = host.querySelector("#of-items");
  (o.items && o.items.length ? o.items : [{}]).forEach((it) => addOrderItem(itemsHost, it));
  expenseEditor(host.querySelector("#of-expenses"), o.extra_expenses);
  if (o.id) {
    entityPhotoEditor(host.querySelector("#of-photos"), "order", o.id);
    // El "+ Foto" de la cabecera dispara el de la galería (y baja hasta ella).
    host.querySelector("#of-add-photo").addEventListener("click", () => {
      const box = host.querySelector("#of-photos");
      box.scrollIntoView({ block: "center", behavior: "smooth" });
      box.querySelector("#ep-add")?.click();
    });
  }

  // El monto de anticipo solo se muestra si el pago es "anticipo".
  host.querySelector("#of-pay").addEventListener("change", (e) => {
    host.querySelector("#of-deposit-wrap").hidden = e.target.value !== "deposit";
  });

  host.querySelector("#of-add-item").onclick = () => {
    const row = addOrderItem(itemsHost, {}, true);   // nueva pieza arriba
    row.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  };
  // Cancelar: si se edita dentro de la tarjeta, se recarga el tablero para
  // restaurarla; si es un pedido nuevo (host de arriba), solo se vacía.
  host.querySelector("#of-cancel").onclick = () =>
    inline ? loadPedidos() : (host.innerHTML = "");
  host.querySelector("#of-save").onclick = () => saveOrder(o.id, host, inline);
  if (!inline) host.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
}

function addOrderItem(host, it, prepend = false) {
  const row = el(`<div class="card oi-editor">
    <div class="oi-edit-top">
      <div class="oi-edit-thumb ph" title="Miniatura del gcode"></div>
      <div class="form-grid" style="flex:1">
        <label class="field"><span>Etiqueta (opcional)</span><input class="oi-label" value="${escHtml(it.label || "")}"></label>
        <label class="field"><span>Impresora</span><select class="oi-printer">
          <option value="">— elegir —</option>
          ${printers.map((p) => `<option value="${p.id}" ${p.id === it.printer_id ? "selected" : ""}>${escHtml(p.name)}</option>`).join("")}
        </select></label>
        <label class="field"><span>Gcode en la impresora</span><select class="oi-gcode"><option value="">— elige impresora —</option></select></label>
        <label class="field"><span>Cantidad</span><input type="number" min="1" class="oi-qty" value="${it.quantity || 1}"></label>
      </div>
    </div>
    <div class="oi-copies-wrap">
      <span class="muted" style="font-size:.78rem">Estado por copia:</span>
      <div class="oi-copies"></div>
    </div>
    <details class="oi-expenses"><summary class="muted">Gastos extra de esta pieza</summary>
      <div class="oi-exp-host"></div></details>
    <div class="row-actions"><span class="oi-status muted"></span><span class="spacer"></span>
      <button type="button" class="btn ghost small oi-remove">Quitar pieza</button></div>
  </div>`);
  // Las piezas nuevas (añadidas a mano) van arriba, para no scrollear; las que
  // ya existen al abrir el pedido se añaden en orden.
  if (prepend) host.prepend(row); else host.appendChild(row);
  expenseEditor(row.querySelector(".oi-exp-host"), it.extra_expenses);
  // Si la pieza ya trae gastos, se abre el bloque para que se vean.
  if (it.extra_expenses && it.extra_expenses.length) row.querySelector(".oi-expenses").open = true;

  // Chips por copia; se rerenderizan al cambiar la cantidad conservando lo marcado.
  const copiesHost = row.querySelector(".oi-copies");
  const qtyInput = row.querySelector(".oi-qty");
  renderCopyChips(copiesHost, Math.max(1, parseInt(qtyInput.value) || 1), it.copy_status);
  qtyInput.addEventListener("input", () =>
    renderCopyChips(copiesHost, Math.max(1, parseInt(qtyInput.value) || 1), readCopyChips(copiesHost)));

  const printerSel = row.querySelector(".oi-printer");
  const gcodeSel = row.querySelector(".oi-gcode");
  row._current = it.gcode_filename || "";
  printerSel.addEventListener("change", () => loadOrderGcodes(row));
  gcodeSel.addEventListener("change", () => updateItemThumb(row));
  updateItemThumb(row);   // miniatura inicial (si ya trae gcode)
  if (it.printer_id) loadOrderGcodes(row);
  row.querySelector(".oi-remove").onclick = () => {
    if (host.children.length > 1) row.remove();
    else toast("Un pedido necesita al menos una pieza");
  };
  return row;
}

// Miniatura del gcode seleccionado en una fila del editor de pieza.
function updateItemThumb(row) {
  const box = row.querySelector(".oi-edit-thumb");
  if (!box) return;
  const gcode = row.querySelector(".oi-gcode").value;
  if (!gcode) { box.className = "oi-edit-thumb ph"; box.innerHTML = ""; return; }
  box.className = "oi-edit-thumb";
  box.innerHTML = `<img src="/api/gcode-thumbnail?filename=${encodeURIComponent(gcode)}" alt=""
    onerror="this.closest('.oi-edit-thumb').className='oi-edit-thumb ph';this.remove()">`;
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
  updateItemThumb(row);   // refleja el gcode seleccionado
}

async function saveOrder(id, host, inline = false) {
  const items = [...host.querySelectorAll(".oi-editor")].map((row) => ({
    label: row.querySelector(".oi-label").value || null,
    printer_id: row.querySelector(".oi-printer").value ? +row.querySelector(".oi-printer").value : null,
    gcode_filename: row.querySelector(".oi-gcode").value || null,
    quantity: Math.max(1, parseInt(row.querySelector(".oi-qty").value) || 1),
    copy_status: readCopyChips(row.querySelector(".oi-copies")),
    extra_expenses: readExpenses(row.querySelector(".oi-exp-host")),
  }));
  const client = host.querySelector("#of-client").value.trim();
  if (!client) { toast("El pedido necesita un cliente"); return; }
  const due = host.querySelector("#of-due").value;
  const price = host.querySelector("#of-price").value;
  const pay = host.querySelector("#of-pay").value;
  const deposit = host.querySelector("#of-deposit").value;
  const body = {
    client,
    description: host.querySelector("#of-desc").value || null,
    payment_status: pay,
    agreed_price: price ? parseFloat(price) : null,
    deposit_amount: pay === "deposit" && deposit ? parseFloat(deposit) : null,
    due_date: due ? new Date(due).toISOString() : null,
    manual_status: host.querySelector("#of-manual").value || null,
    folder: host.querySelector("#of-folder").value.trim() || null,
    postproc_rate: host.querySelector("#of-postrate").value ? parseFloat(host.querySelector("#of-postrate").value) : null,
    postproc_minutes: Math.max(0, (parseInt(host.querySelector("#of-post-h").value) || 0) * 60
      + (parseInt(host.querySelector("#of-post-m").value) || 0)),
    extra_expenses: readExpenses(host.querySelector("#of-expenses")),
    items,
  };
  try {
    await api.send(id ? `/api/orders/${id}` : "/api/orders", id ? "PUT" : "POST", body);
    // Inline: loadPedidos reconstruye el tablero (y la tarjeta). Si no, se vacía
    // el host de arriba del pedido nuevo.
    if (!inline) host.innerHTML = "";
    toast("Pedido guardado"); loadPedidos();
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
// Refresco del tablero mientras se mira; se salta si hay un formulario de
// edición abierto (arriba o dentro de una tarjeta), para no borrarlo al vuelo.
setInterval(() => {
  if (currentTab !== "pedidos" || projMode !== "orders") return;
  // No refrescar si hay un formulario abierto o cambios de chips sin guardar.
  if (document.querySelector("#pedidos .order-edit, #pedidos .board-copies.dirty")) return;
  loadPedidos();
}, 8000);

// --- Proyectos (desarrollo de producto: coste real por peso de báscula) ------
let projMode = "orders";
let projects = [];
let projPrices = {};   // {TIPO: precio medio/kg}

function setPedMode(mode) {
  projMode = mode;
  document.querySelectorAll("#ped-mode button").forEach((b) => b.classList.toggle("active", b.dataset.pmode === mode));
  document.getElementById("orders-mode").hidden = mode !== "orders";
  document.getElementById("projects-mode").hidden = mode !== "projects";
  if (mode === "projects") loadProjects(); else loadPedidos();
}
document.querySelectorAll("#ped-mode button").forEach((b) =>
  b.addEventListener("click", () => setPedMode(b.dataset.pmode)));

async function loadProjects() {
  await ensureRefs();   // materiales, para la lista de tipos
  const [list, mp] = await Promise.all([
    api.get("/api/projects"), api.get("/api/projects/material-prices"),
  ]);
  projects = list; projPrices = mp.prices || {}; currency = mp.currency || currency;
  document.getElementById("proj-prices").innerHTML = "Precio medio por tipo — " +
    (Object.keys(projPrices).length
      ? Object.entries(projPrices).map(([t, p]) => `${escHtml(t)} ${money(p)}/kg`).join(" · ")
      : "sin precios en la biblioteca");
  renderProjectsBoard();
}

function renderProjectsBoard() {
  const host = document.getElementById("projects-board");
  if (!projects.length) {
    host.innerHTML = `<p class="muted">Sin proyectos. Crea uno con “+ Nuevo proyecto”: añade sus partes con el peso de báscula y su tiempo de impresión.</p>`;
    return;
  }
  host.innerHTML = projects.map((pr) => {
    const rec = pr.reconciliation;
    const recTxt = rec.measured_g != null
      ? `estimado ${rec.sum_parts_g} g · báscula ${rec.measured_g} g · error <b>${rec.error_pct >= 0 ? "+" : ""}${rec.error_pct}%</b> · calibración ×${rec.factor}`
      : `estimado ${rec.sum_parts_g} g · añade el peso de báscula del producto para calibrar`;
    const photo = pr.photo_url
      ? `<img class="entity-thumb" src="${pr.photo_url}" onclick="window.open('${pr.photo_url}','_blank')" alt="" title="Foto del producto">` : "";
    const parts = pr.parts.map((p) => projPartRow(p)).join("")
      // Gastos extra como filas propias, con 🔧 en lugar de miniatura.
      + (pr.extra_expenses || []).map((e) => `<div class="order-item-wrap">
          <div class="order-item">
            <div class="oi-thumb tool">🔧</div>
            <span class="oi-names"><span class="oi-name">${escHtml(e.label || "extra")}</span></span>
            <span class="muted">extra${(e.quantity || 1) > 1 ? " · ×" + e.quantity : ""}</span>
            <span class="spacer"></span>
            <span class="oi-cost cost-val ok">${money2((e.amount || 0) * (e.quantity || 1), currency)}</span>
          </div>
        </div>`).join("");
    return `<div class="card order-card">
      <div class="order-head">${photo}<strong>${escHtml(pr.name)}</strong>
        <span class="spacer"></span>
        <span class="pj-total">${money2(pr.cost_total, currency)}</span>
        <button class="btn ghost small" data-edit-proj="${pr.id}">Editar</button>
        <button class="btn danger small" data-del-proj="${pr.id}">✕</button>
        <button class="btn ghost small" data-est-proj="${pr.id}" title="Cargar sus gcodes en la pestaña Estimación (promedio de flota)">→ Estimación</button></div>
      ${pr.notes ? `<p class="muted" style="margin:.2rem 0">${escHtml(pr.notes)}</p>` : ""}
      <div class="order-items">${parts || '<span class="muted">Sin partes</span>'}</div>
      <div class="muted pj-foot">material ${money2(pr.material_cost, currency)} · máquina ${money2(pr.machine_cost, currency)}${pr.extras_cost ? ` · extras ${money2(pr.extras_cost, currency)}` : ""} · ${recTxt}</div>
    </div>`;
  }).join("");
  host.querySelectorAll("[data-edit-proj]").forEach((b) =>
    b.addEventListener("click", () => projectForm(projects.find((p) => p.id == b.dataset.editProj))));
  host.querySelectorAll("[data-del-proj]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("¿Borrar proyecto?")) return;
      await api.send(`/api/projects/${b.dataset.delProj}`, "DELETE"); loadProjects();
    }));
  host.querySelectorAll("[data-est-proj]").forEach((b) =>
    b.addEventListener("click", () => loadProjectIntoEstimacion(projects.find((p) => p.id == b.dataset.estProj))));
  // Parte desplegable (mismo mecanismo que las piezas de pedido).
  host.querySelectorAll(".order-item[data-expand-part]").forEach((row) =>
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, a, .gcode-nav")) return;
      row.closest(".order-item-wrap").classList.toggle("open");
    }));
  host.querySelectorAll("[data-fix-type]").forEach((b) =>
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      goToMaterialFix(b.dataset.fixMat, b.dataset.fixType);
    }));
  host.querySelectorAll("[data-del-part]").forEach((b) =>
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`¿Quitar “${b.dataset.name}” de este proyecto?`)) return;
      await api.send(`/api/project-parts/${b.dataset.delPart}`, "DELETE");
      toast("Parte quitada"); loadProjects();
    }));
  host.querySelectorAll("[data-goto-gcode]").forEach((el) =>
    el.addEventListener("click", () => goToJobsForGcode(el.dataset.gotoGcode)));
}

// Una parte de proyecto con el mismo look que una pieza de pedido (desplegable).
function projPartRow(p) {
  const gname = (p.gcode_filename || "").split("/").pop() || p.material_type;
  const turl = p.gcode_filename ? `/api/gcode-thumbnail?filename=${encodeURIComponent(p.gcode_filename)}` : null;
  const smallThumb = turl
    ? `<img class="oi-thumb" src="${turl}" loading="lazy" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'oi-thumb ph'}))">`
    : `<div class="oi-thumb ph"></div>`;
  const bigThumb = turl
    ? `<img class="oi-detail-thumb" src="${turl}" loading="lazy" alt="">`
    : `<div class="oi-detail-thumb ph"></div>`;
  const drow = (k, v) => v == null || v === "" ? "" : `<dt>${k}</dt><dd>${v}</dd>`;
  const gcodeCell = p.gcode_filename
    ? `<span class="gcode-nav" data-goto-gcode="${escHtml(p.gcode_filename)}" title="Ver la última impresión">${escHtml(gname)} →</span>`
    : escHtml(gname);
  const detail = `<div class="oi-detail">
    <button class="btn danger small oi-del" data-del-part="${p.id}"
      data-name="${escHtml(p.name || gname)}" title="Quitar esta parte del proyecto">✕ Quitar</button>
    ${bigThumb}<div class="oi-detail-body"><dl class="oi-dl">
    ${drow("Gcode", gcodeCell)}
    ${drow("Impresora", p.printer_name ? escHtml(p.printer_name) : null)}
    ${drow("Material", `${escHtml(p.material_type)}${p.no_price ? ' <span class="pill review">sin precio</span>' : ""}`)}
    ${drow("Tiempo impresión", p.print_time_s ? fmtDur(p.print_time_s) : null)}
    ${drow("Peso (báscula)", `${p.weight_g} g`)}
    ${drow("Precio /kg", p.price_per_kg ? money2(p.price_per_kg, currency) : null)}
    ${drow("Coste material", money2(p.material, currency))}
    ${drow("Coste máquina", money2(p.machine, currency))}
    ${drow("Cantidad", p.quantity)}
    ${drow("Coste total", `<strong>${money2(p.line_cost, currency)}</strong>`)}
  </dl></div></div>`;
  return `<div class="order-item-wrap">
    <div class="order-item" data-expand-part>
      ${smallThumb}
      <span class="oi-names"><span class="oi-name">${escHtml(gname)}</span></span>
      <span class="muted">${escHtml(p.material_type)}${p.weight_g ? " · " + p.weight_g + " g" : ""}${p.printer_name ? " · " + escHtml(p.printer_name) : ""}${p.quantity > 1 ? " · ×" + p.quantity : ""}</span>
      <span class="spacer"></span>
      <span class="oi-cost cost-val ${p.no_price ? "warn" : "ok"}"${p.no_price ? ` data-fix-type="${escHtml(p.material_type || "")}" title="Ningún ${escHtml(p.material_type || "material")} tiene precio/kg — clic para ponerlo"` : ""}>${money2(p.line_cost, currency)}</span>
      <span class="oi-caret" aria-hidden="true">▸</span>
    </div>
    ${detail}
  </div>`;
}

// Carga los gcodes de un proyecto en la pestaña Estimación (promedio de flota),
// con el peso de báscula, el tiempo y el material de cada parte.
async function loadProjectIntoEstimacion(pr) {
  if (!pr) return;
  await ensureRefs();
  estFiles = pr.parts.map((p) => ({
    filename: (p.gcode_filename || "").split("/").pop() || (p.material_type || "parte"),
    weight_g: p.weight_g || 0,
    time_s: p.print_time_s || 0,
    filament_type: p.material_type,
    thumb: p.gcode_filename ? `/api/gcode-thumbnail?filename=${encodeURIComponent(p.gcode_filename)}` : null,
    material_id: pickMaterialForType(p.material_type),
    quantity: p.quantity || 1,
  }))
  // Los gastos extra del proyecto viajan como partes extra (🔧, coste fijo).
  .concat((pr.extra_expenses || []).map((e) => ({
    is_extra: true, filename: e.label || "extra",
    extra_amount: e.amount || 0, quantity: e.quantity || 1,
  })));
  showTab("quote");
  renderEstFiles();
  recalcEstimate();
  toast(`${estFiles.length} gcodes de “${pr.name}” cargados en Estimación`);
}

function projTypeOptions(sel) {
  const types = [...new Set(materials.map((m) => m.material_type).filter(Boolean))].sort();
  if (sel && !types.includes(sel)) types.push(sel);
  return types.map((t) => {
    const pp = projPrices[t.toUpperCase()];
    return `<option value="${escHtml(t)}" ${t === sel ? "selected" : ""}>${escHtml(t)}${pp ? " (" + money(pp) + "/kg)" : ""}</option>`;
  }).join("");
}

function addProjectPart(host, pt, prepend) {
  const row = el(`<div class="pf-part">
    <div class="pp-thumb ph" title="Miniatura del gcode"></div>
    <select class="pp-printer"><option value="">— impresora —</option>
      ${printers.map((p) => `<option value="${p.id}" ${p.id === pt.printer_id ? "selected" : ""}>${escHtml(p.name)}</option>`).join("")}</select>
    <select class="pp-gcode"><option value="">— elige impresora —</option></select>
    <select class="pp-type" title="tipo de material">${projTypeOptions(pt.material_type || "PLA")}</select>
    <input type="number" step="0.1" min="0" class="pp-weight" placeholder="peso g (báscula)" value="${pt.weight_g ?? ""}">
    <input type="number" step="1" min="0" class="pp-time" placeholder="min" title="tiempo de impresión (min)" value="${pt.print_time_s ? Math.round(pt.print_time_s / 60) : ""}">
    <input type="number" min="1" class="pp-qty" title="cantidad" value="${pt.quantity || 1}">
    <button type="button" class="btn ghost small pp-del" title="Quitar">✕</button>
  </div>`);
  prepend ? host.prepend(row) : host.appendChild(row);
  row._gcur = pt.gcode_filename || "";
  row.querySelector(".pp-printer").addEventListener("change", () => loadPartGcodes(row));
  row.querySelector(".pp-gcode").addEventListener("change", () => autofillPart(row));
  if (pt.printer_id) loadPartGcodes(row);
  row.querySelector(".pp-del").onclick = () => { row.remove(); projPreview(host); };
}

// Puebla el gcode de la parte con los archivos de la impresora (o del historial).
async function loadPartGcodes(row) {
  const pid = row.querySelector(".pp-printer").value;
  const sel = row.querySelector(".pp-gcode");
  if (!pid) { sel.innerHTML = `<option value="">— elige impresora —</option>`; return; }
  sel.innerHTML = `<option value="">cargando…</option>`;
  const list = async (url) => { try { const r = await api.get(url); return Array.isArray(r) ? r : null; } catch (e) { return null; } };
  let files = await list(`/api/printers/${pid}/files`);
  if (!files || !files.length) files = (await list(`/api/printers/${pid}/history-files`)) || [];
  const cur = row._gcur;
  sel.innerHTML = `<option value="">— elige gcode —</option>` +
    files.map((f) => `<option value="${escHtml(f.path)}" ${f.path === cur ? "selected" : ""}>${escHtml(f.path)}</option>`).join("");
  if (cur && !files.some((f) => f.path === cur))
    sel.innerHTML += `<option value="${escHtml(cur)}" selected>${escHtml(cur)}</option>`;
  updatePartThumb(row);
}

// Miniatura del gcode seleccionado en una fila de parte.
function updatePartThumb(row) {
  const box = row.querySelector(".pp-thumb");
  if (!box) return;
  const gcode = row.querySelector(".pp-gcode").value;
  if (!gcode) { box.className = "pp-thumb ph"; box.innerHTML = ""; return; }
  box.className = "pp-thumb";
  box.innerHTML = `<img src="/api/gcode-thumbnail?filename=${encodeURIComponent(gcode)}" alt=""
    onerror="this.closest('.pp-thumb').className='pp-thumb ph';this.remove()">`;
}

// Al elegir un gcode: autocompleta tipo de material y tiempo REAL del último
// print de ese gcode (el peso NO, se mide con báscula).
async function autofillPart(row) {
  const pid = row.querySelector(".pp-printer").value;
  const gcode = row.querySelector(".pp-gcode").value;
  row._gcur = gcode;
  if (!gcode) return;
  let info;
  try { info = await api.get(`/api/gcode-info?printer_id=${pid}&filename=${encodeURIComponent(gcode)}`); }
  catch (e) { return; }
  if (info && info.found) {
    const typeSel = row.querySelector(".pp-type");
    if (info.material_type) {
      if (![...typeSel.options].some((o) => o.value === info.material_type))
        typeSel.add(new Option(info.material_type, info.material_type));
      typeSel.value = info.material_type;
    }
    if (info.print_time_s) row.querySelector(".pp-time").value = Math.round(info.print_time_s / 60);
  }
  updatePartThumb(row);
  projPreview(row.parentElement);
}

// Previsualización de MATERIAL en vivo (instantánea); la máquina se calcula al
// guardar (necesita el coste/hora de flota del servidor).
function projPreview(partsHost) {
  const box = partsHost.closest(".card").querySelector(".pf-costs");
  let mat = 0;
  partsHost.querySelectorAll(".pf-part").forEach((r) => {
    const t = (r.querySelector(".pp-type").value || "").toUpperCase();
    const w = parseFloat(r.querySelector(".pp-weight").value) || 0;
    const q = Math.max(1, parseInt(r.querySelector(".pp-qty").value) || 1);
    mat += w / 1000 * (projPrices[t] || 0) * q;
  });
  box.innerHTML = `Material (aprox): <b>${money2(mat, currency)}</b> · el coste de máquina (energía + amortización + mantenimiento) se calcula al guardar.`;
}

function projectForm(pr = {}) {
  const host = document.getElementById("project-form-host");
  const form = el(`<div class="card" style="margin-top:1rem">
    <div class="form-head">
      <h2>${pr.id ? "Editar" : "Nuevo"} proyecto</h2>
      <span class="spacer"></span>
      ${pr.id ? `<button type="button" class="btn ghost small pf-add-photo">+ Foto</button>` : ""}
      <button class="btn pf-save">Guardar</button>
      <button class="btn ghost pf-cancel">Cancelar</button>
    </div>
    <div class="form-grid">
      <label class="field"><span>Nombre</span><input class="pf-name" value="${escHtml(pr.name || "")}"></label>
      <label class="field"><span>Peso del producto completo (g, báscula)</span><input type="number" step="0.1" min="0" class="pf-total" value="${pr.total_weight_g ?? ""}"></label>
    </div>
    <label class="field"><span>Notas</span><input class="pf-notes" value="${escHtml(pr.notes || "")}"></label>
    <div class="pf-partshead"><span class="muted">Partes — impresora · gcode · tipo · peso (g, báscula) · tiempo (min) · cantidad</span></div>
    <div class="pf-parts"></div>
    <button type="button" class="btn ghost small pf-addpart">+ Añadir parte</button>
    <div class="order-expenses">
      <div class="muted" style="font-size:.82rem;margin:.6rem 0 .3rem"><strong>Gastos extra del producto</strong>
        (tornillería, pegamento, piezas compradas…)</div>
      <div class="pf-expenses"></div>
    </div>
    <div class="pf-costs muted" style="margin-top:.6rem"></div>
    <div class="entity-photos">
      <div class="muted" style="font-size:.82rem;margin:.6rem 0 .3rem"><strong>Fotos del producto terminado</strong></div>
      <div class="pf-photos">${pr.id ? "" : '<span class="muted">Guarda el proyecto primero para añadir fotos.</span>'}</div>
    </div>
  </div>`);
  host.innerHTML = ""; host.appendChild(form);
  if (pr.id) {
    entityPhotoEditor(form.querySelector(".pf-photos"), "project", pr.id);
    form.querySelector(".pf-add-photo").addEventListener("click", () => {
      const box = form.querySelector(".pf-photos");
      box.scrollIntoView({ block: "center", behavior: "smooth" });
      box.querySelector("#ep-add")?.click();
    });
  }
  const partsHost = form.querySelector(".pf-parts");
  (pr.parts && pr.parts.length ? pr.parts : [{}]).forEach((pt) => addProjectPart(partsHost, pt));
  expenseEditor(form.querySelector(".pf-expenses"), pr.extra_expenses);
  partsHost.addEventListener("input", () => projPreview(partsHost));
  projPreview(partsHost);
  form.querySelector(".pf-addpart").onclick = () => { addProjectPart(partsHost, {}); projPreview(partsHost); };
  form.querySelector(".pf-cancel").onclick = () => (host.innerHTML = "");
  form.querySelector(".pf-save").onclick = async () => {
    const parts = [...partsHost.querySelectorAll(".pf-part")].map((r) => ({
      printer_id: r.querySelector(".pp-printer").value ? +r.querySelector(".pp-printer").value : null,
      gcode_filename: r.querySelector(".pp-gcode").value || null,
      material_type: r.querySelector(".pp-type").value || "PLA",
      weight_g: parseFloat(r.querySelector(".pp-weight").value) || 0,
      print_time_s: (parseFloat(r.querySelector(".pp-time").value) || 0) * 60,
      quantity: Math.max(1, parseInt(r.querySelector(".pp-qty").value) || 1),
    }));
    const body = {
      name: form.querySelector(".pf-name").value || "Proyecto",
      notes: form.querySelector(".pf-notes").value || null,
      total_weight_g: parseFloat(form.querySelector(".pf-total").value) || null,
      extra_expenses: readExpenses(form.querySelector(".pf-expenses")),
      parts,
    };
    try {
      await api.send(pr.id ? `/api/projects/${pr.id}` : "/api/projects", pr.id ? "PUT" : "POST", body);
      host.innerHTML = ""; toast("Proyecto guardado"); loadProjects();
    } catch (e) { toast("Error: " + e.message); }
  };
}
document.getElementById("add-project").addEventListener("click", () => projectForm());

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
  // En multi-material el laminador escribe "filament used [g] = a, b" (un valor
  // por filamento) y "total filament used [g] = T". Hay que SUMAR / usar el
  // total, no quedarse con el primer valor (que puede ser 0 si ese extrusor no
  // se usó). Se captura la línea entera (sin salto) y se suman las comas.
  const sumCsv = (s) => s == null ? null : s.split(",").reduce((a, x) => a + (parseFloat(x) || 0), 0);
  const grams = sumCsv(grab(/;\s*total\s+filament[_ ]used\s*\[g\]\s*=\s*([0-9.,\t ]+)/i))
             ?? sumCsv(grab(/;\s*filament[_ ]used\s*\[g\]\s*=\s*([0-9.,\t ]+)/i))
             ?? sumCsv(grab(/;\s*filament[_ ]weight[_ ]total\s*[:=]\s*([0-9.,\t ]+)/i));
  const mm = sumCsv(grab(/;\s*total\s+filament[_ ]used\s*\[mm\]\s*=\s*([0-9.,\t ]+)/i))
          ?? sumCsv(grab(/;\s*filament[_ ]used\s*\[mm\]\s*=\s*([0-9.,\t ]+)/i));
  const curaM = grab(/;Filament used:\s*([\d.]+)m/i);   // Cura: en metros
  const type = grab(/;\s*filament[_ ]type\s*[:=]\s*([A-Za-z0-9+\-. ]+)/i);
  const timeRaw = grab(/;\s*estimated printing time[^=]*=\s*(.+)/i)
               ?? grab(/;TIME:\s*(\d+)/i);

  let weightG = grams ? grams : null;
  if (weightG == null) {
    // Sin peso del laminador: se deduce de la longitud, como en el registro.
    const lenMm = mm ? mm : (curaM ? parseFloat(curaM) * 1000 : 0);
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
document.getElementById("print-quote").addEventListener("click", () =>
  cotizMode === "venta" ? printVenta() : printCotiz());

// --- Venta directa: paquetes predeterminados con precio manual ---------------
// Reutiliza el motor del PDF (printDoc + plantilla de marca) pero con ítems
// manuales: foto, descripción y precio de venta, más envío e IVA.
let cotizMode = "quote";
document.querySelectorAll("#cotiz-mode button").forEach((b) =>
  b.addEventListener("click", () => {
    cotizMode = b.dataset.cmode;
    document.querySelectorAll("#cotiz-mode button").forEach((x) =>
      x.classList.toggle("active", x === b));
    document.getElementById("quote-mode").hidden = cotizMode !== "quote";
    document.getElementById("venta-mode").hidden = cotizMode !== "venta";
    if (cotizMode === "venta") {
      if (!document.querySelector("#venta-items .venta-item")) addVentaItem();
      recalcVenta();
    } else recalcQuote();
  }));

const ventaItems = () => [...document.querySelectorAll("#venta-items .venta-item")];

function addVentaItem() {
  const item = el(`<div class="card venta-item">
    <div class="item-body">
      <div class="v-thumbwrap">
        <div class="q-thumb placeholder"></div>
        <button type="button" class="btn ghost small v-photo-btn">+ Foto</button>
        <input type="file" class="v-photo" accept="image/*" hidden>
      </div>
      <div class="item-fields">
        <div class="form-grid">
          <label class="field"><span>Ítem / paquete</span><input class="v-desc" placeholder="Paquete 10 recuerditos personalizados"></label>
          <label class="field"><span>Precio de venta (c/u)</span><input type="number" step="0.01" min="0" class="v-price" value="0"></label>
          <label class="field"><span>Cantidad</span><input type="number" min="1" class="v-qty" value="1"></label>
        </div>
        <div class="muted v-line"></div>
      </div>
      <button type="button" class="btn danger small v-remove">Quitar</button>
    </div>
  </div>`);
  document.getElementById("venta-items").appendChild(item);
  const photoInput = item.querySelector(".v-photo");
  item.querySelector(".v-photo-btn").addEventListener("click", () => photoInput.click());
  photoInput.addEventListener("change", async () => {
    const f = photoInput.files[0];
    photoInput.value = "";
    if (!f) return;
    try {
      item._thumb = await fileToDataUri(f, 640, 0.85);
      item.querySelector(".v-thumbwrap .q-thumb").outerHTML =
        `<img class="q-thumb" src="${item._thumb}" alt="">`;
    } catch (e) { toast("No se pudo leer la imagen"); }
  });
  item.querySelectorAll(".v-desc, .v-price, .v-qty").forEach((i) =>
    i.addEventListener("input", recalcVenta));
  item.querySelector(".v-remove").addEventListener("click", () => { item.remove(); recalcVenta(); });
}
document.getElementById("v-add-item").addEventListener("click", addVentaItem);
["v-envio", "v-iva"].forEach((id) =>
  document.getElementById(id).addEventListener("input", recalcVenta));

function ventaTotals() {
  const lines = ventaItems().map((item) => {
    const price = parseFloat(item.querySelector(".v-price").value) || 0;
    const qty = Math.max(1, parseInt(item.querySelector(".v-qty").value) || 1);
    return { item, desc: item.querySelector(".v-desc").value, price, qty, total: price * qty };
  }).filter((l) => l.desc || l.price);
  const subtotal = lines.reduce((a, l) => a + l.total, 0);
  const envio = parseFloat(document.getElementById("v-envio").value) || 0;
  const ivaPct = parseFloat(document.getElementById("v-iva").value) || 0;
  const iva = (subtotal + envio) * (ivaPct / 100);
  return { lines, subtotal, envio, ivaPct, iva, total: subtotal + envio + iva };
}

function recalcVenta() {
  if (cotizMode !== "venta") return;
  const v = ventaTotals();
  ventaItems().forEach((item) => {
    const price = parseFloat(item.querySelector(".v-price").value) || 0;
    const qty = Math.max(1, parseInt(item.querySelector(".v-qty").value) || 1);
    item.querySelector(".v-line").textContent =
      price ? `${money(price)} × ${qty} = ${money(price * qty)}` : "";
  });
  document.getElementById("venta-summary").innerHTML = `<table>
    <tr><td>Sub-Total (${v.lines.length} ítems)</td><td class="num">${money(v.subtotal)}</td></tr>
    <tr><td>Servicio de envío</td><td class="num">${money(v.envio)}</td></tr>
    <tr><td>IVA (${v.ivaPct}%)</td><td class="num">${money(v.iva)}</td></tr>
    <tr><td><strong>Total</strong></td><td class="num"><strong>${money(v.total)}</strong></td></tr>
  </table>`;
  document.querySelector("#cotiz-bar .q-total").innerHTML =
    `<span class="bar-total">${money(v.total)}</span> <span class="bar-detail muted">venta directa</span>`;
}

function printVenta() {
  const v = ventaTotals();
  if (!v.lines.length) return toast("Añade al menos un ítem con precio");
  const s = appSettings || {};
  const cliente = document.getElementById("v-cliente").value;
  const qnum = "V-" + Math.random().toString(16).slice(2, 8).toUpperCase();
  const conThumb = v.lines.some((l) => l.item._thumb);
  const filas = v.lines.map((l, i) => `<tr>
      ${conThumb ? `<td class="thumb">${l.item._thumb ? `<img src="${escHtml(l.item._thumb)}" alt="">` : ""}</td>` : ""}
      <td>${i + 1}. ${escHtml(l.desc || "Producto")}</td>
      <td class="num">${money(l.price)}</td>
      <td class="num">${l.qty}</td>
      <td class="num">${money(l.total)}</td>
    </tr>`).join("");

  printDoc(`Cotización ${qnum}`, `
    ${brandHead(s, "Impresión 3D", "Cotización")}
    <div class="meta">
      <span><strong>Cotización #:</strong> ${qnum}</span>
      ${cliente ? `<span><strong>Cliente:</strong> ${escHtml(cliente)}</span>` : ""}
      <span><strong>Fecha:</strong> ${new Date().toLocaleDateString()}</span>
      <span><strong>Piezas:</strong> ${v.lines.reduce((a, l) => a + l.qty, 0)}</span>
    </div>
    <table>
      <tr>${conThumb ? "<th></th>" : ""}<th>Descripción</th>
          <th class="num">Precio unitario</th><th class="num">Cantidad</th><th class="num">Total</th></tr>
      ${filas}
    </table>
    <table class="totals">
      <tr><td>Sub-Total</td><td class="num">${money(v.subtotal)}</td></tr>
      <tr><td>Servicio de envío</td><td class="num">${money(v.envio)}</td></tr>
      <tr><td>IVA (${v.ivaPct}%)</td><td class="num">${money(v.iva)}</td></tr>
      <tr class="grand"><td>Total</td><td class="num">${money(v.total)}</td></tr>
    </table>
    ${brandTerms(s)}
    <div class="foot">${escHtml(s.company_name || "M3D Nexus")} — Transforma tus ideas en realidad con impresión 3D</div>
  `);
}

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
    applyBrand();            // el logo del panel se actualiza al guardarlo
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

// Marca del panel: el logo de empresa si está configurado en Ajustes; si no,
// el texto. Se aplica al arrancar y al guardar los ajustes.
function applyBrand() {
  const host = document.getElementById("side-brand");
  if (!host) return;
  const logo = appSettings && appSettings.company_logo;
  const name = (appSettings && appSettings.company_name) || "M3D Nexus";
  host.innerHTML = logo
    ? `<img src="${escHtml(logo)}" alt="${escHtml(name)}" title="${escHtml(name)}">`
    : `<h1>M3D <span>Nexus</span></h1>`;
}

// Carga inicial: abre la sección que pide la URL (permite entrar directo a
// /pedidos o recargar sin volver al dashboard).
ensureRefs().then(() => {
  applyBrand();
  const tab = tabFromPath();
  history.replaceState({ tab }, "", `/${TAB_ROUTES[tab]}`);
  showTab(tab);
  loadTab(tab);
  renderLive();
});
