# M3D Nexus

Gestión de un taller de impresión 3D con **coste real medido**: registro
automático de cada impresión, **pedidos** de clientes, **proyectos** de
desarrollo de producto y **estimación** de precio antes de imprimir.

> El proyecto nació como `printcost` y ese nombre sobrevive en los
> **identificadores técnicos** del despliegue actual: el contenedor
> (`docker exec printcost …`), la base de datos (`printcost.db`) y la carpeta
> `/opt/printcost`. La herramienta se llama **M3D Nexus**.

Cada impresora corre **Klipper + Moonraker** y tiene un **Sonoff POW3** midiendo
consumo, centralizado en **Home Assistant**. M3D Nexus correlaciona ambas
fuentes: toma el historial de jobs de Moonraker y, para la ventana temporal de
cada impresión, consulta a HA cuánta energía gastó ese enchufe. Con eso calcula
el coste real (electricidad + filamento + amortización + mantenimiento).

```
 Moonraker (por impresora)          Home Assistant
   historial de jobs        +       energía POW3 (kWh acumulado)
   start/end, filamento              por entidad/sensor
            │                               │
            └───────────────┬───────────────┘
                            ▼
                  M3D Nexus (FastAPI + SQLite)
        "¿cuánta energía en [start,end]?" → coste → registro
                            ▼
     Web UI: dashboard · impresiones · pedidos/proyectos ·
             estimación · cotización · materiales · calibración
```

## Cómo calcula el coste

Para cada job terminado (completado **o** fallado):

| Componente     | Fórmula |
|----------------|---------|
| Electricidad   | `kWh de la impresión × precio/kWh` |
| Filamento      | `gramos × precio/kg del material` |
| Amortización   | `horas × ((precio máquina − reventa) / vida útil en horas)` |
| Mantenimiento  | `horas × coste/hora de la impresora` |

- **kWh de la impresión** = `energía_acumulada(fin) − energía_acumulada(inicio)`
  del sensor POW3 en HA. Mide el consumo real de toda la impresora (calentar
  cama/hotend incluido).
- **Amortización con reventa**: solo se deprecia lo que de verdad pierdes
  (`precio − valor de reventa estimado`), repartido entre la vida útil
  (`años × días activos/año × horas/día`). Una máquina que conserva valor no
  carga su precio entero a cada pieza.
- **Gramos** = el peso que el slicer mete en el gcode si está disponible; si no,
  se estima desde la longitud de filamento y la densidad del material.
- Las impresiones falladas cuentan, pero **solo por lo que gastaron**: la
  energía es la medida en su ventana real, y el filamento se escala por la
  fracción que llegó a extruirse (`filament_used` / `filament_total` del
  slicer, acotada a [0, 1]). Un fallo a los cinco minutos no cobra la pieza
  entera. Los jobs completados usan el peso del slicer tal cual.

### Cuando no hay energía medida

Dos mecanismos evitan que una impresión sin sensor salga a coste de luz cero:

- **Energía prestada.** Una impresora sin sensor propio puede referenciar a otra
  (`power_ref_printer_id`, p.ej. una segunda Creality Hi apuntando a la primera):
  su energía se estima como `potencia media de la referencia × duración` y el
  job se marca `energy_estimated` (la UI lo muestra con `≈`). Nunca pisa una
  medida real, y estos valores **se excluyen** del cálculo de potencias medias
  para que el promedio no se derive de sí mismo.
- **Factor térmico por material.** Si un material no tiene energía medida, su
  consumo se estima escalando la base de **esa misma máquina** según sus
  temperaturas (cama y boquilla sobre el ambiente): PLA 1.0, PETG ≈ 1.27,
  ABS/ASA ≈ 1.54, TPU ≈ 0.81. Así PETG no sale igual que PLA sin haberlo medido.
  Una medida real siempre gana al factor; y se prefiere la base propia de la
  máquina antes que la medida de otra (una Voron cerrada no representa a una
  Ender abierta).

## Puesta en marcha (Docker)

```bash
cp config.example.yaml config.yaml     # edita impresoras, materiales, precio luz
cp .env.example .env                    # pon tu HA_TOKEN
docker compose up -d --build
```

Abre `http://<host>:8088`.

### Token de Home Assistant
En HA: tu perfil → *Tokens de acceso de larga duración* → **Crear token**.
Ponlo en `.env` como `HA_TOKEN=...` (mejor que en el YAML).

El contenedor lee la variable **al arrancar**, así que después de cambiar `.env`
hay que recrearlo — no basta con reiniciar:

```bash
docker compose up -d --force-recreate printcost
docker exec printcost python -m app.debug_ha    # comprueba que el token vale
```

Si el token caduca o se revoca, la app **no avisa**: sigue registrando
impresiones con `energy_kwh` vacío. Comprueba `Ping: OK ✅` tras cada cambio.

### Encontrar la entidad de energía
En HA, *Herramientas para desarrolladores → Estados*, busca el sensor del POW3
con la energía **acumulada** en kWh (`state_class: total_increasing`):
- **Tasmota:** suele ser `sensor.<nombre>_energy_total`.
- **Firmware original (integración Sonoff / SonoffLAN):** verifica el nombre
  exacto; el contador acumulado a veces se llama `..._energy` o similar.

Esa entidad va en `ha_energy_entity` de cada impresora.

## Desarrollo local (sin Docker)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp config.example.yaml config.yaml
export CONFIG_PATH=config.yaml DB_PATH=./printcost.db HA_TOKEN=...
uvicorn app.main:app --reload
pytest            # motor de costes, servicios y API (281 tests)
```

La UI tiene sus propios tests. No hace falta node instalado:

```bash
# Parser de gcode (sin dependencias; ojo: se ejecuta desde el repo montado)
docker run --rm -v "$PWD":/w:ro -w /tmp node:20-slim sh -c \
  "npm i --silent --no-save jsdom && NODE_PATH=/tmp/node_modules \
   node /w/tests/gcode_parser.test.mjs"

# Suites que cargan la UI real en jsdom (cotización, chips del tablero,
# edición de pedidos). Cambia el nombre del fichero según la suite.
docker run --rm -v "$PWD":/w:ro -w /tmp node:20-slim sh -c \
  "npm i --silent --no-save jsdom && cp /w/tests/order_edit.test.mjs . && \
   PRINTCOST_ROOT=/w node order_edit.test.mjs"
```

Tras tocar `web/static/`, sube el `?v=` de `index.html` (cache-buster) antes de
desplegar, o el navegador servirá la versión anterior.

## Estructura

```
app/
  cost.py                 motor de costes (puro, testeado)
  config.py  db.py  models.py  schemas.py
  integrations/
    moonraker.py          historial de jobs, archivos y miniaturas
    homeassistant.py      lee la energía por ventana temporal
  services/
    ingest.py             correlaciona job + energía → coste (upsert)
    sync.py               sondeo periódico + guardado de miniaturas
    live.py               seguimiento de la impresión en curso
    estimate.py           estimación previa, potencia media, coste/hora de flota
    projects.py           coste real de producto por peso de báscula
    orders.py             estado de pedidos y piezas, margen
    filament.py           parseo de nombres de filamento (multi-material)
    calibration.py  orca.py  orca_import.py  loaded.py
  api/routes.py           API REST
  web/static/             UI (HTML + JS vanilla)
  recompute.py            recálculo masivo del histórico          (CLI)
  backfill_thumbnails.py  descarga miniaturas de gcodes ya registrados (CLI)
  calibrate.py  debug_ha.py                                       (CLI)
tests/                    pytest (motor, servicios y API) + *.test.mjs (UI)
```

## Decisiones y límites actuales

- **Tarifa plana** (un precio/kWh). Para tarifas por tramos habría que trocear
  la energía por franja horaria usando la API de estadísticas de HA
  (`recorder/statistics_during_period`).
- **Sincronización por sondeo** (cada `poll_interval_seconds`). Suficiente y
  robusto para uso doméstico; la ingesta es idempotente. Mejora futura:
  WebSocket de Moonraker (`notify_history_changed`) para tiempo real.
- **La energía se pide una sola vez por impresión.** Un job deja de consultarse
  cuando se resuelve, en un sentido o en otro: o tiene kWh, o queda marcado
  `energy_unavailable` porque HA ya no puede darlos (fuera de
  `ha_history_retention_days`, o su ventana no tiene datos del sensor). Si HA no
  responde no se marca nada: se reintenta en la ronda siguiente. Sin esa
  distinción el sondeo no converge y machaca a HA indefinidamente.
- **Cambiar el precio de un material** recalcula al vuelo las impresiones que lo
  usan. El precio de la luz, en cambio, queda congelado en cada registro
  (`tariff_per_kwh`) por reproducibilidad; para reescribirlo, `app.recompute`.
- **Material por tipo:** se asigna el material activo cuyo tipo case con el del
  slicer; si no hay match, el job queda *a revisar* y puedes asignarlo a mano
  desde la web (recalcula al instante). Lo asignado a mano queda marcado
  `material_manual` y el sondeo **no lo re-resuelve** (clave en gcodes
  multi-material, donde el auto-detector se quedaría con el primer filamento).
- **Dos formas de costear, a propósito:**
  - *Impresiones* → coste **medido** de un job concreto en su máquina concreta.
  - *Proyectos* → coste **real de producto** con peso de báscula y coste/hora de
    flota; sirve para calibrar cuánto se desvía lo estimado de lo medido.
  - *Estimación* → coste **previsto** de gcodes aún sin imprimir, sin fijar
    máquina (promedio/máximo/mínimo de flota) para poder cotizar.
- **El estado de las piezas de un pedido es manual.** No se deduce del historial
  del gcode: el mismo archivo puede usarse en varios pedidos, así que un
  histórico previo marcaría como impreso algo que aún no lo está. Los chips por
  copia son la fuente de verdad.
- **Las miniaturas se guardan una sola vez por job.** Si la impresora está
  apagada no se marca el intento (se reintenta al encenderla); si respondió pero
  el gcode ya no está, se marca definitivo y no se vuelve a pedir.

## Convención de nombres de filamento

M3D Nexus extrae **marca, tipo y color** del campo `filament_name` del gcode.
Para que el parseo sea infalible, nombra tus presets de filamento en el laminador
con este formato (el laminador añade ` @ impresora`, eso se ignora):

```
Marca | Tipo | Color
```

Ejemplos:

| Preset en el laminador            | Marca              | Tipo | Color      |
|-----------------------------------|--------------------|------|------------|
| `Elegoo \| PLA+ \| White`         | Elegoo             | PLA  | White      |
| `Polymaker PolyTerra \| PLA \| Army Green` | Polymaker PolyTerra | PLA | Army Green |
| `eSun \| PETG \|`                 | eSun               | PETG | —          |

Reglas:

- Separa los campos con **`|`** (la barra vertical). Marca y color pueden tener
  varias palabras sin problema.
- El **tipo** se toma del campo `filament_type` del laminador (canónico:
  PLA/PETG/ABS/ASA…); el del nombre es solo respaldo. Puedes escribir `PLA+`.
- El **color** es opcional (deja el último campo vacío si no aplica).
- **No uses** `@`, `;` ni `"` en el nombre: el laminador y Moonraker los usan
  como separadores internos.
- Compatibilidad: los nombres sin `|` (estilo `Elegoo PLA+ White`) se siguen
  parseando con heurística, pero la convención con `|` es la que no falla nunca.

Cuando imprimes con un filamento que aún no existe en la app, se crea solo
(precio 0, marcado *a revisar*) para que solo le pongas el precio/kg.

## Funciones

- **Registro automático** de cada impresión con su coste (luz + filamento +
  amortización + mantenimiento), incluidas las falladas. El listado va con
  **miniatura del gcode** (estilo Mainsail), guardada en la BD al sincronizar
  para que se vea **aunque la impresora esté apagada** o se borre el archivo.
- **Coste en vivo**: tarjeta "Imprimiendo ahora" con potencia, kWh y coste
  acumulándose en tiempo real (`/api/live`, refresco cada 5 s).
- **Pedidos de clientes**: tablero con estado por pieza (chips por copia,
  marcados a mano), precio acordado y **margen a la vista**, anticipos, gastos
  extra con cantidad, post-procesado (tarifa/hora × minutos), carpeta local del
  pedido y cola por impresora. Cada pieza se despliega con su detalle (gcode,
  material, tiempo, filamento, coste unitario y total) y su miniatura.
- **Proyectos (desarrollo de producto)**: mismo tablero, otro propósito. Las
  partes llevan el **peso real de báscula**, y el coste sale de
  `material (peso × precio medio del tipo) + máquina (horas × coste/hora de
  flota)`. Al indicar el **peso del producto ensamblado** se reconcilia contra la
  suma de partes y se obtiene el **error %** y un **factor de calibración**
  (báscula ÷ estimado) para corregir futuras estimaciones.
- **Enlace impresiones ↔ pedidos/proyectos**: desde el registro, el botón
  *Agregar a:* añade esa impresión como pieza de un pedido o parte de un
  proyecto; cada impresión muestra un **badge** de a qué pedido/proyecto
  pertenece. El nombre del gcode abre la **UI de la impresora** (Mainsail
  `/files` o Fluidd `/#/jobs`, según `ui_type`) copiando el nombre al
  portapapeles.
- **Estimación de producto (multi-gcode, sin fijar máquina)**: sube uno o varios
  gcodes —pueden ser materiales distintos— y calcula el coste promediando la
  **flota**. La **base de coste** es elegible: *máximo* (protege precios, por
  defecto), *promedio ponderado por uso real*, *promedio simple* o *mínimo*
  (competitivo); siempre se muestra el rango min–max. Encima va el **precio de
  venta**: cantidad, mano de obra (horas × tarifa), post-procesado, % de fallo y
  margen sobre todo.
- **Selector de filamento con fotos**: en el registro, un modal con la
  biblioteca en tarjetas (foto de bobina o muestra de color) y filtros por tipo,
  marca y color. Para gcodes **multi-material**, el botón 🧵 lista *todos* los
  filamentos del archivo y deja elegir con cuál se imprimió, **creándolo** si no
  existe. Lo elegido a mano se marca `material_manual` y el sondeo **no lo pisa**.
- **Fotos del producto terminado** en pedidos y proyectos (hasta 8, reescaladas
  en el navegador), con miniatura en la tarjeta.
- **Cotización itemizada**: un documento con cabecera (cliente, envío, IVA,
  margen, tarifas de luz y operario) y **un ítem por impresión**. Cada ítem lleva
  su impresora, material, masa, tiempo y cantidad, así que una misma cotización
  puede mezclar piezas de máquinas distintas. Envío e IVA se cobran una vez, no
  una por pieza.
- **Cotización desde un gcode**: elige un archivo de la impresora o sube varios
  de golpe (uno por ítem), y se rellenan solos la altura de capa, el tiempo, la
  masa, el tipo de material y su precio/kg. La miniatura del laminador se
  incrusta en el PDF, en una columna junto a cada línea.
  Los gcodes subidos **se leen en el navegador**, no se suben al servidor: solo
  la cabecera (miniaturas) y la cola (config del laminador), así uno de 200 MB
  tarda lo mismo que uno de 2 MB. Formatos: PrusaSlicer, OrcaSlicer y Cura.
- **PDFs con tu marca**: logo, colores y datos de empresa configurables desde
  *Ajustes*. El logo se guarda incrustado (data URI) porque la impresión se
  dispara al instante y una imagen externa no daría tiempo a cargar.
- **Gráficas y export**: coste por mes y por material, y exportación CSV del
  registro completo (`/api/jobs.csv`).
- **Calibración por impresora**: matriz de qué filamento está afinado en qué
  máquina y con qué boquilla. Importa la carpeta de perfiles de OrcaSlicer
  (`Ajustes → Calibración → Importar de OrcaSlicer`) y lee marca, color,
  temperaturas, **pressure advance** y flow ratio, distinguiendo tres niveles:
  calibrado a fondo (PA/flow), solo temperaturas, o sin tocar. También puede
  deducir la matriz del propio historial (`python -m app.calibrate`), que además
  destapa las impresiones hechas con el perfil de otra máquina.
- **Diagnóstico**: `python -m app.debug_ha` para depurar la conexión con HA.
- **Mantenimiento**: `python -m app.recompute` re-deriva peso de filamento y
  coste de *todo* el histórico desde `raw_metadata`. Úsalo cuando cambies precios
  de material, parámetros de amortización/mantenimiento o la forma de calcular
  una magnitud (`/api/jobs/{id}/recompute` solo rehace el coste a partir del peso
  ya guardado). Acepta `--dry-run`.
  `python -m app.backfill_thumbnails` descarga las miniaturas de los gcodes ya
  registrados (solo de impresoras encendidas; las apagadas se reintentan luego).

## Problemas conocidos

Detectados en la revisión del **2026-07-22** contra la BD en producción
(468 jobs, 5 impresoras). Ordenados por impacto.

### Falsean los costes

- [x] ~~**Los trabajos fallidos cobraban el filamento entero.**~~ *(corregido el
  2026-07-22)* `_filament_weight` prefería `filament_weight_total`, el peso de la
  pieza *completa*, en vez de lo extruido antes de cancelarse: un
  `klippy_shutdown` de 723 mm (≈ 2,2 g) figuraba como 49,8 g. Ahora los jobs no
  completados se escalan por `filament_used / filament_total`. El histórico se
  reparó con `python -m app.recompute`: 68 registros, −3,39 kg de filamento
  fantasma, −881 $.

- [ ] **Sin margen en la ventana de energía.** `energy_window_padding_seconds: 0`
  deja fuera el pico de calentamiento de cama y hotend, que es la mayor parte
  del consumo. Probar 60–120 s.

### Fiabilidad

- [x] ~~**Reingesta infinita: ~500 peticiones/min a Home Assistant.**~~
  *(corregido el 2026-07-22)* `ingest_job` solo saltaba un job si tenía energía y
  no estaba *a revisar*; como casi ninguno cumplía ambas, cada ciclo de 60 s
  lanzaba una llamada a HA por cada job del historial, sin converger nunca. Ahora
  se distingue **"todavía no la tengo"** de **"ya no existe"** (columna
  `energy_unavailable`) y solo se pregunta por lo recuperable. Medido sobre el
  contenedor real: **de 228 a 7 peticiones por cada 3 min**, y las 7 que quedan
  son del seguimiento en vivo de la impresión en curso, no del historial.

- [x] ~~**El back-catálogo nunca podrá tener energía.**~~ *(mitigado el
  2026-07-22)* Sigue siendo cierto —el `recorder` de HA purga el historial
  detallado a los 10 días—, pero ya no se paga por ello: los jobs anteriores a
  `ha_history_retention_days` se marcan irrecuperables sin llegar a consultar.
  Recuperar esa energía de verdad exigiría `recorder/statistics_during_period`
  (estadísticas a largo plazo), la misma API que pide la tarifa por tramos.

- [ ] **Degradación silenciosa cuando HA no responde.** Con el token caducado la
  app siguió registrando impresiones sin energía durante dos meses (465 de 468
  jobs con `energy_kwh` a NULL) sin ningún aviso. Ya se avisa **en el log** una
  vez por ronda, y `/api/health` reporta el estado, pero la UI no lo muestra en
  ninguna parte. **Fix pendiente:** banner en el dashboard cuando
  `home_assistant` sea `false`.

- [ ] **El filtro «Solo a revisar» no discrimina bien.** Marca 477 de 610 jobs
  (2026-08-08), casi todos por energía que nunca llegará o materiales sin
  precio. `needs_review` podría reservarse para lo accionable (falta material o
  precio) y mostrar la energía perdida como una columna aparte.

- [ ] **Materiales sin precio.** 46 de 65 filamentos tienen `price_per_kg = 0`,
  así que sus impresiones no cobran filamento y quedan *a revisar*. Se arregla
  poniendo el precio (recalcula solo las suyas al vuelo).

- [x] ~~**SQLite sin WAL.**~~ *(corregido el 2026-08-08)* Dos hilos de fondo
  escriben además de las rutas de FastAPI, y el sondeo retiene el lock mientras
  baja miniaturas y energía por red. Ahora el engine activa
  `PRAGMA journal_mode=WAL` y `busy_timeout=30000`: los lectores no se bloquean
  y una escritura concurrente espera en vez de fallar con *database is locked*.

### Interfaz

- [ ] **Fechas desplazadas.** Las columnas `DateTime` se declaran sin
  `timezone=True`, así que el UTC se guarda como *naive*, se serializa sin `Z` y
  el navegador lo interpreta como hora **local**: todas las fechas de la UI
  aparecen corridas por el offset local.

- [~] **XSS almacenado.** Había ~30 interpolaciones de nombres de archivo,
  material y campos de empresa en `innerHTML` sin escapar. Se introdujo el
  helper `escHtml()` y hoy se usa en ~107 sitios (todas las vistas nuevas:
  pedidos, proyectos, estimación, selectores). **Queda pendiente** una pasada
  final por las vistas antiguas para confirmar que no falta ninguna.

### Despliegue

- [ ] **Sin autenticación.** Toda la API está abierta a quien alcance el puerto,
  incluido `DELETE /api/printers/{id}`, que borra en cascada sus impresiones.
  Asumible en LAN; **no exponer a internet** sin poner un proxy con auth delante.
- [ ] **Path traversal en el proxy de miniaturas.** `MoonrakerClient.thumbnail`
  usa `quote()`, que no escapa `/` ni `..`: `/api/thumbnail/1?path=../../…` llega
  tal cual a Moonraker. Depende de que Moonraker lo rechace.
- [x] ~~**El proyecto no está bajo control de versiones.**~~ Repo git con remoto
  en GitHub (`M3DNexus`), push con la clave de despliegue `~/.ssh/m3dnexus_deploy`.
- [ ] **El contenedor corre como root** y no tiene `HEALTHCHECK`.

## Roadmap

- [ ] Push por WebSocket de Moonraker (ahora el tiempo real es por *polling*).
- [ ] Tarifa por tramos / sensor de precio de HA (PVPC, mercado).
- [ ] Integración con Spoolman para coste de filamento por bobina.
- [ ] Potencia media configurable por impresora (hoy se deriva del historial, y
  si falta el material se escala por el factor térmico).
- [ ] Temperaturas del factor térmico editables desde *Ajustes* (hoy son una
  tabla fija en `services/estimate.py`).
- [ ] Aplicar el **factor de calibración** de un proyecto (báscula ÷ estimado)
  a las estimaciones futuras de esos gcodes.

## Visión a futuro — local-first + catálogo de productos

Rumbo acordado (2026-08-08): que M3D Nexus sea el **SW central** de gestión de
proyectos, servicio de impresión y desarrollo de producto vendible, corriendo
**nativo en Windows** con la **BD y los archivos en carpetas locales** (un
contenedor en Proxmox no puede tocar el disco local del PC, y ahí hay límite de
storage). Cada producto pasa a ser una **carpeta autocontenida = "master"
reproducible**: sus gcodes, el 3MF de OrcaSlicer, calibraciones, fotos en
original y la receta de coste. La BD queda como **índice**; lo pesado va a disco.

**Modelo mental unificado:** pedidos, productos y proyectos son **la misma
entidad** por debajo (partes + archivos + coste + fotos); solo cambia la
categoría/etiqueta con que se ven. Lo que aplica a proyectos aplica a pedidos y
viceversa.

### Próximo paso (arrancar por aquí)

- [ ] **Productizar rutas: BD + capa de archivos**, para seguir desarrollando
  rápido. BD en carpeta local elegible; carpeta base de proyectos/pedidos; crear
  la carpeta del proyecto y **guardar / listar / abrir en el Explorador** sus
  gcodes, 3MF y calibraciones, ligados en la BD. Token de HA a Ajustes (hoy es
  variable de entorno).

### Después

- [ ] Galería de fotos **en original, sin comprimir, en disco** + miniatura para
  la UI (hoy son blobs reescalados en la BD) → usarlas para redes con un clic.
- [ ] **Duplicar / replicar un proyecto con un clic**: clona la receta y copia
  sus gcodes/3MF a la carpeta del nuevo proyecto.
- [ ] Unificar pedidos / productos / proyectos como una sola entidad categorizada.
- [ ] Empaquetado autocontenido: **PyInstaller + PyWebview** (ventana de app),
  con **GitHub Actions** para el build de Windows (Mac/Linux opcional). Tauri
  (instaladores firmados + auto-update) solo si se distribuye.
- [ ] (Escalón mayor) Mandar el gcode a imprimir en la impresora vía Moonraker
  desde el catálogo.
