# printcost

Registro de impresiones 3D con **cálculo automático del coste por impresión**.

Cada impresora corre **Klipper + Moonraker** y tiene un **Sonoff POW3** midiendo
consumo, centralizado en **Home Assistant**. `printcost` correlaciona ambas
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
                   printcost (FastAPI + SQLite)
        "¿cuánta energía en [start,end]?" → coste → registro
                            ▼
                   Web UI: dashboard, registro, ajustes
```

## Cómo calcula el coste

Para cada job terminado (completado **o** fallado):

| Componente     | Fórmula |
|----------------|---------|
| Electricidad   | `kWh de la impresión × precio/kWh` |
| Filamento      | `gramos × precio/kg del material` |
| Amortización   | `horas × (precio máquina / vida útil en horas)` |
| Mantenimiento  | `horas × coste/hora` |

- **kWh de la impresión** = `energía_acumulada(fin) − energía_acumulada(inicio)`
  del sensor POW3 en HA. Mide el consumo real de toda la impresora (calentar
  cama/hotend incluido).
- **Gramos** = el peso que el slicer mete en el gcode si está disponible; si no,
  se estima desde la longitud de filamento y la densidad del material.
- Las impresiones falladas cuentan, pero **solo por lo que gastaron**: la
  energía es la medida en su ventana real, y el filamento se escala por la
  fracción que llegó a extruirse (`filament_used` / `filament_total` del
  slicer, acotada a [0, 1]). Un fallo a los cinco minutos no cobra la pieza
  entera. Los jobs completados usan el peso del slicer tal cual.

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
pytest            # tests del motor de costes
```

La UI tiene sus propios tests. No hace falta node instalado:

```bash
# Parser de gcode (sin dependencias)
docker run --rm -v "$PWD":/w:ro -w /w node:20-slim node tests/gcode_parser.test.mjs

# Aritmética de la cotización y generación del PDF (carga la UI real en jsdom)
docker run --rm -v "$PWD":/w:ro -w /tmp node:20-slim sh -c \
  "npm i --silent --no-save jsdom && cp /w/tests/quote_items.test.mjs . && \
   PRINTCOST_ROOT=/w node quote_items.test.mjs"
```

## Estructura

```
app/
  cost.py                 motor de costes (puro, testeado)
  config.py  db.py  models.py
  integrations/
    moonraker.py          lee el historial de jobs
    homeassistant.py      lee la energía por ventana temporal
  services/
    ingest.py             correlaciona job + energía → coste (upsert)
    sync.py               sondeo periódico de todas las impresoras
  api/routes.py           API REST
  web/static/             UI (HTML + JS vanilla)
tests/test_cost.py
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
  desde la web (recalcula al instante).

## Convención de nombres de filamento

`printcost` extrae **marca, tipo y color** del campo `filament_name` del gcode.
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
  amortización + mantenimiento), incluidas las falladas.
- **Coste en vivo**: tarjeta "Imprimiendo ahora" con potencia, kWh y coste
  acumulándose en tiempo real (`/api/live`, refresco cada 5 s).
- **Estimar antes de imprimir**: elige un gcode de la impresora y calcula el
  coste previsto. La potencia media se deriva de tu propio historial.
- **Presupuesto / precio de venta**: sobre el coste estimado, añade recargo por
  fallo, mano de obra, post-procesado y margen → precio final, con PDF/CSV.
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
  coste de *todo* el histórico desde `raw_metadata`. Úsalo cuando cambie la forma
  de calcular una magnitud (`/api/jobs/{id}/recompute` solo rehace el coste a
  partir del peso ya guardado). Acepta `--dry-run`.

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

- [ ] **El filtro «Solo a revisar» ya no discrimina.** Marca 460 de 468 jobs,
  casi todos por energía que nunca llegará. Ahora que se sabe cuál es
  irrecuperable, `needs_review` podría reservarse para lo accionable (falta
  material o precio) y mostrar la energía perdida como una columna aparte.

- [ ] **SQLite sin WAL.** Dos hilos de fondo escriben además de las rutas de
  FastAPI. Añadir `PRAGMA journal_mode=WAL` al arranque para evitar
  *database is locked*.

### Interfaz

- [ ] **Fechas desplazadas.** Las columnas `DateTime` se declaran sin
  `timezone=True`, así que el UTC se guarda como *naive*, se serializa sin `Z` y
  el navegador lo interpreta como hora **local**: todas las fechas de la UI
  aparecen corridas por el offset local.

- [ ] **XSS almacenado.** `web/static/app.js` interpola nombres de archivo, de
  material y los campos de empresa en `innerHTML` sin escapar (~30 sitios). Un
  gcode llamado `<img onerror=…>` ejecuta script. Riesgo bajo con un solo
  usuario, pero se arregla con un helper `esc()`.

### Despliegue

- [ ] **Sin autenticación.** Toda la API está abierta a quien alcance el puerto,
  incluido `DELETE /api/printers/{id}`, que borra en cascada sus impresiones.
  Asumible en LAN; **no exponer a internet** sin poner un proxy con auth delante.
- [ ] **Path traversal en el proxy de miniaturas.** `MoonrakerClient.thumbnail`
  usa `quote()`, que no escapa `/` ni `..`: `/api/thumbnail/1?path=../../…` llega
  tal cual a Moonraker. Depende de que Moonraker lo rechace.
- [ ] **El proyecto no está bajo control de versiones** (hay `.gitignore` pero no
  repo). `git init`.
- [ ] **El contenedor corre como root** y no tiene `HEALTHCHECK`.

## Roadmap

- [ ] Push por WebSocket de Moonraker (ahora el tiempo real es por *polling*).
- [ ] Tarifa por tramos / sensor de precio de HA (PVPC, mercado).
- [ ] Integración con Spoolman para coste de filamento por bobina.
- [ ] Miniaturas del gcode en el registro y en el presupuesto.
- [ ] Potencia media configurable por impresora (hoy se deriva del historial).
