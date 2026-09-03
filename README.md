# hotel-mcp-server

An MCP server for internal hotel operations: availability and reservation
lookups, room assignment, overbooking risk, housekeeping scheduling, and
booking with printable receipts. Nine tools.

Built for **CC3067 Redes, Project 1**.
The Model Context Protocol is implemented **by hand on top of JSON-RPC 2.0** —
this package has no MCP SDK dependency, and in fact **no runtime dependencies at
all**. `faker` is needed only to regenerate the sample database.

- Protocol revision: **MCP 2025-06-18**
- Transport: **stdio** (newline-delimited JSON on stdin/stdout)
- Language: Python 3.11+

---

## What it does

A hotel front desk faces questions a language model cannot answer on its own
without inventing the answer. Nine tools cover them, in three groups.

**Everyday questions** — what a receptionist asks all day:

| Tool | Question it answers |
|---|---|
| `consultar_disponibilidad` | How many rooms are free on a date, or across a range? |
| `buscar_reservaciones` | Where is this guest's booking? Who is arriving unassigned? |
| `resumen_del_dia` | How does one day look: arrivals, departures, occupancy, VIPs? |

**Decisions** — the ones that need real computation:

| Tool | Question it answers |
|---|---|
| `asignar_habitacion` | Which room should this arriving guest get? |
| `calcular_riesgo_overbooking` | How many rooms can we safely oversell for this date? |
| `programar_limpieza` | In what order should housekeeping clean today, and what won't be ready? |

**Writes** — the only three tools that change anything:

| Tool | What it does |
|---|---|
| `crear_reservacion` | Register a new booking, unassigned |
| `confirmar_asignacion` | Commit a room to a reservation, re-validating first |
| `generar_comprobante` | Write a printable proof of reservation as an `.xlsx` |

The usual path for a new booking:

```
crear_reservacion ─> asignar_habitacion ─> confirmar_asignacion ─> generar_comprobante
```

For an existing one, start at `buscar_reservaciones` to turn a name into an id.

The part worth looking at is the **cleaning-window validation** in
`asignar_habitacion`. Finding a room whose dates are free is a trivial query.
The constraint that actually bites a front desk is that a *free* room may still
be unusable: the previous guest leaves at 12:00, the room needs 60 minutes of
cleaning, and the incoming guest arrives at 12:30. A plain availability query
hands you that room anyway. This server rejects it and tells you it was 30
minutes short.

---

## Installation

```bash
git clone https://github.com/JosFer720/hotel-mcp-server.git
cd hotel-mcp-server

# With uv (recommended)
uv venv --python 3.13
uv pip install -e .

# Or with pip
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

A pre-seeded database is committed at `data/hotel.db`, so the server works
immediately after install. To regenerate it:

```bash
uv pip install -e ".[seed]"
python -m hotel_mcp.seed
```

> **macOS note.** If `import hotel_mcp` fails right after an editable install,
> check `ls -lO .venv/lib/python*/site-packages/*.pth` for the `hidden` flag.
> Python 3.13+ silently skips hidden `.pth` files. Fix with
> `chflags nohidden .venv/lib/python*/site-packages/*.pth`, or set
> `PYTHONPATH=src` instead.

## Running

The server speaks MCP over stdio and is meant to be launched by an MCP host,
not run interactively:

```bash
python -m hotel_mcp
```

### Adding it to a host

Any MCP host works. The command is `python -m hotel_mcp` with the repository's
interpreter.

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "hotel": {
      "command": "/absolute/path/to/hotel-mcp-server/.venv/bin/python",
      "args": ["-m", "hotel_mcp"]
    }
  }
}
```

**A host using a `servers.json`-style config:**

```json
{
  "hotel": {
    "transport": "stdio",
    "command": "/absolute/path/to/hotel-mcp-server/.venv/bin/python",
    "args": ["-m", "hotel_mcp"],
    "env": { "PYTHONPATH": "/absolute/path/to/hotel-mcp-server/src" }
  }
}
```

### Configuration

| Variable | Purpose | Default |
|---|---|---|
| `HOTEL_DB` | Path to the SQLite database | `data/hotel.db` in the repo |
| `HOTEL_EXPORT_DIR` | Where `generar_comprobante` writes receipts | `./comprobantes` |
| `HOTEL_NAME` | Hotel name printed on receipts | `Hotel Demo UVG` |

Three tools write. If you point a host at the database shipped here, it will be
modified in place — copy it first and set `HOTEL_DB` to the copy if you want the
sample data left alone.

---

## Tool specification

All dates are `YYYY-MM-DD`; all times are 24-hour `HH:MM`. Every tool returns
both a human-readable text block and a `structuredContent` object with the same
payload.

### `consultar_disponibilidad`

How many rooms are free on a date, or across a range of nights.

**Input**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `fecha` | string | yes | First night, `YYYY-MM-DD` |
| `fecha_fin` | string | no | Last night. Omit for a single night |
| `tipo` | string | no | `individual` \| `doble` \| `suite` |
| `vista` | string | no | `mar` \| `ciudad` \| `jardin` \| `interior` |

A room counts as occupied on a night when a live reservation satisfies
`checkin <= night < checkout`, so the checkout day itself is free. Over a range,
a room is only reported free if it is free **every** night in it.

**Output**

```json
{
  "desde": "2026-09-05",
  "hasta": "2026-09-06",
  "noches": 2,
  "libres": [{"numero": "111", "tipo": "suite", "capacidad": 2,
              "vista": "mar", "piso": 1, "tarifa_base": 350.0}],
  "por_tipo": {"doble": 6, "individual": 3, "suite": 3},
  "por_vista": {"mar": 4, "ciudad": 3, "interior": 3, "jardin": 2},
  "resumen": {"habitaciones_consideradas": 60, "minimo_libres_en_el_rango": 12,
              "noches_consultadas": 2}
}
```

---

### `buscar_reservaciones`

Find reservations by guest name, by a night the stay covers, by room, or by
state. This is how you turn a surname into the `reservacion_id` the other tools
need.

**Input** — every field optional:

| Field | Type | Meaning |
|---|---|---|
| `huesped` | string | Partial name, case-insensitive |
| `fecha` | string | Any stay covering that night, not just arrivals |
| `habitacion` | string | Room number, e.g. `402` |
| `estado` | string | `confirmada` \| `checkin` \| `checkout` \| `no_show` \| `cancelada` |
| `solo_sin_asignar` | boolean | Only arrivals that still need a room |
| `limite` | integer | Max rows, default 25, capped at 200 |

With no `estado`, only live reservations (`confirmada`, `checkin`) are returned.

**Output**

```json
{
  "filtros": {"huesped": "morales", "fecha": null},
  "encontradas": 2,
  "reservaciones": [
    {"reservacion_id": 15150, "huesped": "Ana Morales", "vip": false,
     "habitacion": "111", "habitacion_asignada": true,
     "checkin": "2026-09-05", "checkout": "2026-09-07",
     "hora_checkin_estimada": "15:00", "personas": 2, "canal": "directo",
     "tipo_tarifa": "reembolsable", "estado": "confirmada",
     "preferencias": {"vista": "mar"}}
  ]
}
```

---

### `resumen_del_dia`

Everything about one day in a single call — the first thing anyone asks each
morning.

**Input**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `fecha` | string | yes | Day to summarise, `YYYY-MM-DD` |

**Output**

```json
{
  "fecha": "2026-08-29",
  "ocupacion": {"habitaciones_totales": 60, "ocupadas": 48,
                "libres": 12, "ocupacion_pct": 80.0},
  "llegadas": {"total": 28, "vip": 4, "sin_habitacion_asignada": 3, "detalle": [...]},
  "salidas": {"total": 25, "detalle": [...]},
  "pendientes": {"asignar_habitacion": [
    {"reservacion_id": 15144, "huesped": "Elsa Perez", "vip": true,
     "hora_checkin_estimada": "14:00"}]}
}
```

---

### `asignar_habitacion`

Choose the best available room for a reservation.

**Input**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `reservacion_id` | integer | yes | Reservation to assign a room to. |
| `preferencias_override` | object | no | Overrides the guest profile's stored preferences. |
| `preferencias_override.vista` | string | no | One of `mar`, `ciudad`, `jardin`, `interior`. |
| `preferencias_override.piso_min` | integer | no | Lowest acceptable floor. |
| `preferencias_override.accesible` | boolean | no | Guest needs a wheelchair-accessible room. |
| `hora_checkin_estimada` | string | no | Override the arrival time (`HH:MM`). Applied in memory only — the reservation is not modified. |

**Algorithm**

1. Load the reservation, the guest, and the guest's stored preferences.
2. Filter rooms by party size, and by accessibility when required.
3. Drop rooms with an overlapping stay. Two ranges overlap iff
   `a.checkin < b.checkout AND b.checkin < a.checkout`, so a same-day turnover
   is deliberately **not** an overlap.
4. **Validate the cleaning window.** If another stay checks out on the arrival
   date, require
   `previous_checkout + room.minutos_limpieza <= hora_checkin_estimada`.
   Otherwise the room is rejected and the shortfall is reported. When the
   previous checkout time is unknown, the hotel's 12:00 checkout hour is
   assumed; when the arrival time is unknown the check passes, since a room
   cannot be proven unusable against an unknown arrival.
5. Score the survivors: preference matches, VIP priority, minus a penalty for
   wasting capacity (giving a four-person suite to a single guest costs the
   hotel a room it could have sold to a family).
6. Return the best match, two alternatives, and the score arithmetic.

**Output**

```json
{
  "reservacion": {
    "id": 15144, "huesped": "...", "vip": false, "personas": 2,
    "checkin": "2026-08-25", "checkout": "2026-08-27",
    "hora_checkin_estimada": "11:00",
    "preferencias_aplicadas": { "vista": "mar" }
  },
  "asignacion": {
    "habitacion": "402", "habitacion_id": 40, "puntaje": 87,
    "desglose": { "base": 50, "vista_coincide": 25, "prioridad_vip": 12 },
    "tipo": "doble", "vista": "mar", "piso": 4, "tarifa_base": 168.35
  },
  "alternativas": [ { "...": "same shape, lower score" } ],
  "descartadas_por_limpieza": [
    {
      "habitacion": "310",
      "faltan_minutos": 35,
      "detalle": "previous checkout 11:00 + 35min cleaning = ready 11:35, guest arrives 11:00"
    }
  ],
  "resumen_descartes": {
    "por_limpieza": 2, "por_ocupacion_o_accesibilidad": 39,
    "candidatas": 9, "evaluadas": 50
  }
}
```

**Scoring**

| Factor | Points |
|---|---|
| Base | +50 |
| Requested view matches / does not | +25 / −10 |
| Floor at or above the requested minimum / below | +10 / −15 |
| Accessible room when required | +15 |
| VIP guest | +8, plus 5 for a sea view, plus the floor number (capped at 5) |
| Wasted capacity | −6 per unused bed, capped at −18 |

### `calcular_riesgo_overbooking`

Recommend how many rooms to oversell for a date.

**Input**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `fecha` | string | yes | Date to evaluate. |
| `umbral_riesgo` | number | no | Maximum acceptable probability of walking a guest. Default `0.05`. |

**Algorithm**

1. Compute the historical no-show rate for each `(canal, tipo_tarifa)` pair from
   the property's own settled reservations. Cancellations are excluded — a
   cancelled booking is released in advance and never occupied the night.
2. Smooth each rate with additive (Laplace) smoothing, `α=1, β=8`. Without it a
   segment with three bookings and no no-shows would report exactly `0.0` and
   the caller would oversell against it.
3. Treat each confirmed arrival as an independent Bernoulli trial with its
   segment's probability. The night's total no-shows is therefore a sum of
   *non-identical* Bernoullis (a Poisson-binomial), which has no convenient
   closed form and is poorly served by a normal approximation at these counts.
4. Obtain the distribution by Monte Carlo (10,000 simulations, seeded so results
   are reproducible).
5. Selling `N` extra rooms forces a walk exactly when fewer than `N` bookings
   fail to show, so `P(walk) = P(no_shows < N)`. Return the largest `N` whose
   `P(walk)` stays at or below the threshold.

**Output**

```json
{
  "fecha": "2026-08-30",
  "capacidad_total": 60,
  "habitaciones_ocupadas_por_estancias": 32,
  "reservaciones_confirmadas": 26,
  "habitaciones_libres": 2,
  "no_shows_esperados": 2.82,
  "sobreventa_recomendada": 1,
  "prob_walk": 0.0502,
  "umbral_riesgo": 0.15,
  "metodo": "Monte Carlo, 10000 simulaciones ...",
  "curva_riesgo": [
    { "sobreventa": 0, "prob_walk": 0.0 },
    { "sobreventa": 1, "prob_walk": 0.0502 },
    { "sobreventa": 2, "prob_walk": 0.203 }
  ],
  "desglose_por_canal": [
    { "canal": "booking", "tarifa": "reembolsable", "reservas": 12,
      "tasa_no_show": 0.1664, "historico": 1872, "no_shows_esperados": 2.0 }
  ]
}
```

`curva_riesgo` is the whole risk curve, so a revenue manager can pick a
different tolerance without another call.

### `programar_limpieza`

Build the day's housekeeping schedule.

**Input**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `fecha` | string | yes | Day to schedule. |
| `turno` | string | no | `matutino` or `vespertino`. Omit for both. |

Shifts are `matutino` 08:00–16:00 and `vespertino` 14:00–22:00.

**Algorithm**

1. List every room checking out that day.
2. Mark rooms with a same-day arrival as deadline-bound; the deadline is that
   guest's estimated arrival. A VIP arrival raises priority further.
3. Load the staff actually on shift (`turnos_disponibles.disponible = 1`).
4. Sort earliest-deadline-first, VIP breaking ties, longer jobs before shorter
   ones at equal priority so the long job is less likely to be the one that
   overruns.
5. Give each task to whichever eligible worker frees up soonest, with total
   assigned minutes breaking ties — that is what keeps the load even.
6. Report every room whose estimated finish is after its deadline.

**Output**

```json
{
  "fecha": "2026-08-25", "turno": "ambos",
  "asignaciones": [
    { "personal": "María López", "turno": "matutino", "carga_minutos": 200,
      "orden": [
        { "habitacion": "501", "inicio_estimado": "08:00", "fin_estimado": "08:25",
          "minutos": 25, "deadline": "11:00", "vip": true,
          "motivo": "same-day arrival at 11:00 (VIP)" }
      ] }
  ],
  "en_riesgo": [
    { "habitacion": "512", "deadline": "13:00", "fin_estimado": "13:45",
      "retraso_minutos": 45, "vip": false, "asignada_a": "..." }
  ],
  "sin_asignar": [],
  "resumen": {
    "habitaciones_por_limpiar": 25, "con_llegada_el_mismo_dia": 18,
    "vip": 3, "personal_disponible": 10, "minutos_totales": 965, "en_riesgo": 0
  }
}
```

---

### `crear_reservacion`

**Writes, in two steps.** Register a new booking. The room is left unassigned
on purpose: choosing one is `asignar_habitacion`'s job, and a booking taken over
the phone should be recordable before it is roomed.

Called **without** `confirmado`, it writes nothing — the connection is opened
read-only, so SQLite itself would refuse — and returns the booking it *would*
create, for the user to check. Only a second call with `confirmado: true`
inserts it.

The gate is in the code rather than in the model's instructions because an
instruction is advice. Told to read the details back and wait, the model
complied on one run and created the booking unprompted on the next; for a tool
that writes, "usually asks first" is not the guarantee you want.

**Input**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `huesped` | string | yes | Full name. An existing profile with the same name is reused |
| `checkin` | string | yes | `YYYY-MM-DD` |
| `checkout` | string | yes | `YYYY-MM-DD`, must be after `checkin` |
| `personas` | integer | no | Party size, default 1 |
| `canal` | string | no | `directo` \| `booking` \| `expedia` \| `agencia` \| `corporativo`, default `directo` |
| `tipo_tarifa` | string | no | `reembolsable` \| `no_reembolsable` \| `prepagada`, default `reembolsable` |
| `hora_checkin_estimada` | string | no | `HH:MM`. Without it the cleaning-window check cannot reject a room |
| `vip` | boolean | no | Mark the guest VIP |
| `preferencias` | object | no | `{"vista": "mar", "piso_min": 3, "accesible": true}` |
| `confirmado` | boolean | no | Omit on the first call. `true` on the second, after the user approves |

Guest matching is a case-insensitive match on the **full** name — deliberately
stricter than `buscar_reservaciones`, where a loose match only shows an extra
row, but here would file a booking under the wrong person.

Rejected with a message, before anything is written: a `checkout` not after
`checkin`, a party larger than any room, an unknown channel or rate type, a
malformed time, or an empty name.

**Output — first call, nothing written**

```json
{
  "requiere_confirmacion": true,
  "creado": false,
  "reserva_propuesta": {
    "huesped": "Ana Morales", "huesped_nuevo": true,
    "checkin": "2026-09-14", "checkout": "2026-09-16", "noches": 2,
    "hora_checkin_estimada": "15:00", "personas": 2,
    "canal": "directo", "tipo_tarifa": "reembolsable",
    "estado": "confirmada", "habitacion": null
  },
  "instruccion": "Nothing has been written. Show these details to the user ..."
}
```

Validation runs on this call too, so a bad date or an unknown channel is
reported *before* anyone is asked to approve it.

**Output — second call, with `confirmado: true`**

```json
{
  "reservacion_id": 15150, "creado": true,
  "huesped": "Ana Morales", "huesped_id": 401, "huesped_nuevo": true,
  "checkin": "2026-09-05", "checkout": "2026-09-07", "noches": 2,
  "hora_checkin_estimada": "15:00", "personas": 2,
  "canal": "directo", "tipo_tarifa": "reembolsable",
  "estado": "confirmada", "habitacion": null,
  "siguiente_paso": "Call asignar_habitacion with this reservacion_id ..."
}
```

A repeat booking for the same guest and dates is **not** rejected — one family
can legitimately need two rooms — but an `aviso` field flags it, on the preview
as well, so a double entry can be caught before it is written.

> **Note for host authors.** A server cannot see where a turn begins or ends: it
> receives a stream of `tools/call` requests. So this gate alone does not stop a
> model from calling the preview and the confirmed write back to back in one
> reply, leaving the user with nothing to approve — which is exactly what the
> model did when told to hurry. Only the host knows a user message arrived in
> between. If you connect this server, refuse a `confirmado: true` call unless
> the tool returned `requiere_confirmacion` on an **earlier** turn; the
> reference host in `redes-mcp-chatbot` does this in `agent/loop.py`.

---

### `confirmar_asignacion`

**Writes.** Commit a room to a reservation. `asignar_habitacion` only
recommends and rolls its transaction back; nothing is booked until this
succeeds.

**Input**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `reservacion_id` | integer | yes | Reservation to update |
| `habitacion` | string | yes | Room number, normally the recommended one |

Every constraint is re-checked against **current** data first — capacity,
overlapping stays and the cleaning window. By the time a user confirms, the
recommendation may be minutes old and another clerk may have taken the room, so
a stale recommendation is rejected with the reason rather than applied:

```
room 305 is taken: reservation 15153 (Carlos Rios) runs 2026-09-05 to
2026-09-07. Run asignar_habitacion again to get a current recommendation.
```

**Output**

```json
{
  "reservacion_id": 15150, "huesped": "Ana Morales", "vip": false,
  "habitacion": "111", "habitacion_anterior": null,
  "tipo": "suite", "vista": "mar", "piso": 1, "tarifa_base": 350.0,
  "checkin": "2026-09-05", "checkout": "2026-09-07",
  "hora_checkin_estimada": "15:00", "confirmada": true
}
```

---

### `generar_comprobante`

**Writes a file.** Produce a printable proof of reservation as an `.xlsx`, for
when a guest asks for written confirmation that their booking exists.

**Input**

| Field | Type | Required | Meaning |
|---|---|---|---|
| `reservacion_id` | integer | yes | Reservation to issue the receipt for |
| `destino` | string | no | Full output path. Omit for an automatic name under `HOTEL_EXPORT_DIR` |

The spreadsheet is written with the standard library only — an `.xlsx` is a ZIP
of XML parts, so `src/hotel_mcp/xlsx.py` builds one directly rather than pulling
in openpyxl and costing this package its zero-dependency install. It opens
natively in Excel, Numbers and Google Sheets.

Works before a room is assigned; the room reads `sin asignar` and the total is
`null`.

**Output**

```json
{
  "archivo": "/path/to/comprobante-R-15150-Ana-Morales.xlsx",
  "folio": "R-15150", "reservacion_id": 15150,
  "huesped": "Ana Morales", "vip": false,
  "habitacion": "111", "habitacion_asignada": true,
  "checkin": "2026-09-05", "checkout": "2026-09-07", "noches": 2,
  "personas": 2, "tarifa_por_noche": 350.0, "total_estimado": 700.0,
  "canal": "directo", "tipo_tarifa": "reembolsable",
  "estado": "confirmada", "emitido": "2026-08-29 13:40"
}
```

## Usage examples

Natural-language prompts, and the tool call each produces:

> **"Llega el Sr. Pérez mañana a las 2pm y pidió vista al mar. ¿Qué habitación le doy?"**
> → `asignar_habitacion { reservacion_id: 15144, preferencias_override: { vista: "mar" } }`

> **"¿Y si llega a las 11 en vez de las 2?"**
> → `asignar_habitacion { reservacion_id: 15144, hora_checkin_estimada: "11:00" }`
> Rooms being vacated at 11:00 now appear under `descartadas_por_limpieza`, and
> the recommendation can change.

> **"¿Cuánto puedo sobrevender el fin de semana?"**
> → `calcular_riesgo_overbooking { fecha: "2026-08-30", umbral_riesgo: 0.05 }`

> **"Dame el orden de limpieza de hoy para el turno matutino."**
> → `programar_limpieza { fecha: "2026-08-25", turno: "matutino" }`

> **"¿Cuántas habitaciones libres hay hoy?"**
> → `consultar_disponibilidad { fecha: "2026-08-29" }`

> **"¿Cómo viene el día?"**
> → `resumen_del_dia { fecha: "2026-08-29" }`

> **"Llegó una señora que dice apellidarse Morales, ¿existe su reservación?"**
> → `buscar_reservaciones { huesped: "Morales" }`

> **"Hacé una reservación para Ana Morales, del 5 al 7, 2 personas, llega a las 3."**
> → `crear_reservacion { huesped: "Ana Morales", checkin: "2026-09-05",`
> `checkout: "2026-09-07", personas: 2, hora_checkin_estimada: "15:00" }`
> — returns the proposal; the assistant reads it back and waits.
>
> **"Sí, confirmá."**
> → the same call plus `confirmado: true`, which writes it
> then `asignar_habitacion { reservacion_id: 15150 }`
> then `confirmar_asignacion { reservacion_id: 15150, habitacion: "111" }`

> **"Pide un comprobante impreso."**
> → `generar_comprobante { reservacion_id: 15150 }`

### Talking to it directly

The server is plain JSON-RPC over stdio, so you can drive it from a shell:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cli","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python -m hotel_mcp
```

---

## Database

SQLite. The shipped sample contains 60 rooms, 400 guests, 12 housekeepers and
about 15,000 reservations spanning two years — enough history for the no-show
rates to be statistically meaningful.

| Table | Purpose |
|---|---|
| `habitaciones` | Rooms: type, capacity, view, accessibility, rate, cleaning minutes |
| `huespedes` | Guests: VIP flag, preferences as JSON |
| `reservaciones` | Stays: dates, estimated arrival, actual checkout, channel, rate type, state |
| `personal_limpieza` | Housekeeping staff and their shift |
| `turnos_disponibles` | Per-day availability for each staff member |

Staff rosters (`turnos_disponibles`) are only seeded for a window around the
generation date. `programar_limpieza` outside that window returns an explicit
`nota` saying nobody is rostered, rather than an empty schedule that would read
as "no work today".

Full DDL: [`src/hotel_mcp/schema.sql`](src/hotel_mcp/schema.sql).

The generator gives each `(channel, rate type)` pair a different ground-truth
no-show probability — prepaid guests almost always arrive, refundable OTA
bookings often do not — so the overbooking tool has something real to recover.

---

## Protocol notes

Implemented by hand, without an MCP SDK:

| Method | Notes |
|---|---|
| `initialize` | Answers with `protocolVersion`, `capabilities.tools`, `serverInfo`, `instructions`. |
| `notifications/initialized` | Accepted; no response, per JSON-RPC. |
| `tools/list` | Returns all nine tools. Not paginated — there is no `nextCursor`. |
| `tools/call` | Validates arguments against the tool's schema, then dispatches. |
| `ping` | Answers `{}`. |
| `notifications/cancelled` | Cancels the in-flight task and, per spec, sends no response for it. |

Two implementation details worth repeating for anyone writing their own:

- **Protocol errors and tool errors are different things.** An unknown method
  (`-32601`) or arguments that fail validation (`-32602`) are JSON-RPC errors.
  A tool that runs and then fails — no such reservation, nobody on shift — is a
  *successful* response carrying `isError: true`, because the model needs to
  read the message in order to recover.
- **stdout is reserved for the protocol.** At startup the real stdout handle is
  stashed and `sys.stdout` is repointed at stderr. One stray `print()` would
  otherwise corrupt the JSON stream and produce a hang that looks exactly like
  a client bug.

Each request is handled in its own task, so the 10,000-iteration overbooking
simulation cannot block `ping` or a cancellation.

---

## Development

```bash
uv pip install -e ".[dev]"
PYTHONPATH=src python -m pytest -q
```

68 tests cover the cleaning-window rule, the Laplace smoothing and monotonicity
of the risk curve, the deadline/VIP/load-balancing behaviour of the scheduler,
the validation, confirmation gate and race handling of the write paths, and the structure of the
generated spreadsheet.

```
src/hotel_mcp/
├── mcpkit/            reusable MCP-over-stdio core, no hotel code in it
│   ├── jsonrpc.py     frame construction and error codes
│   ├── server.py      tool registry, schema validation, method dispatch
│   └── stdio_server.py  the read loop, stdout guard, cancellation
├── logic/             business logic, no protocol code in it
│   ├── assignment.py
│   ├── overbooking.py
│   └── housekeeping.py
├── tools.py           schemas, and the glue between the two halves
├── db.py, seed.py, schema.sql
```

`mcpkit/` deliberately imports nothing hotel-specific, so it can be lifted into
another MCP server unchanged.

## License

MIT.
