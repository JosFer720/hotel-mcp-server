-- Schema for the hotel operations database.
-- Times are stored as TEXT in ISO-8601 ('YYYY-MM-DD', 'HH:MM') so SQLite's
-- date functions and plain string comparison both behave correctly.

CREATE TABLE IF NOT EXISTS habitaciones (
    id               INTEGER PRIMARY KEY,
    numero           TEXT UNIQUE NOT NULL,
    piso             INTEGER NOT NULL,
    tipo             TEXT NOT NULL,              -- individual | doble | suite
    capacidad        INTEGER NOT NULL,
    vista            TEXT,                       -- mar | ciudad | jardin | interior
    accesible        INTEGER DEFAULT 0,          -- wheelchair accessible
    tarifa_base      REAL NOT NULL,
    minutos_limpieza INTEGER NOT NULL            -- suites take longer to clean
);

CREATE TABLE IF NOT EXISTS huespedes (
    id            INTEGER PRIMARY KEY,
    nombre        TEXT NOT NULL,
    vip           INTEGER DEFAULT 0,
    preferencias  TEXT                           -- JSON: {"vista":"mar","piso_min":3}
);

CREATE TABLE IF NOT EXISTS reservaciones (
    id                    INTEGER PRIMARY KEY,
    huesped_id            INTEGER REFERENCES huespedes(id),
    habitacion_id         INTEGER REFERENCES habitaciones(id),  -- NULL = unassigned
    checkin               TEXT NOT NULL,          -- YYYY-MM-DD
    checkout              TEXT NOT NULL,          -- YYYY-MM-DD
    hora_checkin_estimada TEXT,                   -- HH:MM
    hora_checkout_real    TEXT,                   -- HH:MM, when known
    personas              INTEGER NOT NULL DEFAULT 1,
    canal                 TEXT NOT NULL,          -- directo|booking|expedia|agencia|corporativo
    tipo_tarifa           TEXT NOT NULL,          -- reembolsable|no_reembolsable|prepagada
    estado                TEXT NOT NULL           -- confirmada|checkin|checkout|no_show|cancelada
);

CREATE TABLE IF NOT EXISTS personal_limpieza (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    turno  TEXT NOT NULL                          -- matutino | vespertino
);

CREATE TABLE IF NOT EXISTS turnos_disponibles (
    id          INTEGER PRIMARY KEY,
    personal_id INTEGER REFERENCES personal_limpieza(id),
    fecha       TEXT NOT NULL,
    disponible  INTEGER DEFAULT 1
);

-- The overbooking tool scans two years of history grouped by channel and rate
-- type; the housekeeping and assignment tools scan by date. These indexes keep
-- both interactive.
CREATE INDEX IF NOT EXISTS idx_res_fechas    ON reservaciones(checkin, checkout);
CREATE INDEX IF NOT EXISTS idx_res_hab       ON reservaciones(habitacion_id, checkin, checkout);
CREATE INDEX IF NOT EXISTS idx_res_segmento  ON reservaciones(canal, tipo_tarifa, estado);
CREATE INDEX IF NOT EXISTS idx_turnos_fecha  ON turnos_disponibles(fecha, disponible);
