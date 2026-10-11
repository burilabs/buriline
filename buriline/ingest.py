"""Loading of CSV/XLSX exports with tolerant column mapping.

Plants export their data with all kinds of column names. We map the common German and
English variants onto a fixed schema and validate the result so later analysis can rely
on it.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd

LINE_SCHEMA = {
    "timestamp": ["timestamp", "zeit", "zeitstempel", "time", "datetime", "datum_zeit", "start"],
    "machine": ["machine", "maschine", "station", "anlage", "equipment"],
    "state": ["state", "zustand", "status", "betriebszustand"],
    "duration_s": ["duration_s", "dauer_s", "dauer", "duration", "sekunden"],
    "alarm_code": ["alarm_code", "fehlercode", "code", "alarm", "störcode", "stoercode"],
    "alarm_text": ["alarm_text", "fehlertext", "text", "meldung", "beschreibung", "alarmtext"],
    "batch": ["batch", "charge", "auftrag", "order", "lot"],
    "good_count": ["good_count", "gutstück", "gutstueck", "gut", "good", "io"],
    "reject_count": ["reject_count", "ausschuss", "reject", "nio", "schlecht"],
    "shift": ["shift", "schicht"],
}

BOARD_SCHEMA = {
    "date": ["date", "datum", "tag"],
    "hour": ["hour", "stunde", "std"],
    "shift": ["shift", "schicht"],
    "line": ["line", "linie", "maschine", "machine", "arbeitsplatz"],
    "order": ["order", "auftrag", "fa", "fertigungsauftrag"],
    "target": ["target", "soll", "plan", "vorgabe"],
    "actual": ["actual", "ist", "stück", "stueck", "menge"],
    "reason": ["reason", "grund", "abweichungsgrund", "bemerkung"],
    "operator": ["operator", "mitarbeiter", "ma", "bediener"],
}

STOCK_SCHEMA = {
    "sku": ["sku", "artikel", "artikelnummer", "material", "teilenummer", "item"],
    "description": ["description", "bezeichnung", "text", "name"],
    "stock": ["stock", "bestand", "menge", "lagerbestand", "qty"],
    "min_stock": ["min_stock", "mindestbestand", "meldebestand", "min"],
    "unit_cost": ["unit_cost", "preis", "ek", "einstandspreis", "cost"],
    "avg_daily_usage": ["avg_daily_usage", "verbrauch_tag", "tagesverbrauch", "usage"],
    "last_movement": ["last_movement", "letzte_bewegung", "last_move"],
    "location": ["location", "lagerplatz", "platz", "bin"],
}

MOVEMENT_SCHEMA = {
    "date": ["date", "datum", "buchungsdatum"],
    "sku": ["sku", "artikel", "artikelnummer", "material", "teilenummer"],
    "qty": ["qty", "menge", "quantity", "bewegung"],
    "type": ["type", "art", "bewegungsart", "typ"],
}

STATE_ALIASES = {
    "RUN": ["run", "läuft", "laeuft", "produktion", "production", "running", "betrieb", "auto"],
    "STOP": ["stop", "stillstand", "störung", "stoerung", "alarm", "fault", "down", "fehler"],
    "SETUP": ["setup", "rüsten", "ruesten", "umrüsten", "umruesten", "changeover"],
    "PLANNED_STOP": ["planned_stop", "pause", "geplant", "break", "wartung geplant", "no_production"],
    "BLOCKED": ["blocked", "blockiert", "stau", "rückstau", "rueckstau"],
    "STARVED": ["starved", "leer", "mangel", "kein material", "wartet auf produkt"],
    "IDLE": ["idle", "bereit", "warten", "standby"],
}


class IngestError(ValueError):
    pass


def _normalise(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def map_columns(df: pd.DataFrame, schema: dict[str, list[str]], required: tuple[str, ...]) -> pd.DataFrame:
    cols = {_normalise(c): c for c in df.columns}
    rename: dict[str, str] = {}
    for target, aliases in schema.items():
        for alias in aliases:
            if alias in cols and cols[alias] not in rename:
                rename[cols[alias]] = target
                break
    out = df.rename(columns=rename)
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise IngestError(
            f"Pflichtspalten fehlen: {', '.join(missing)}. Gefundene Spalten: {', '.join(map(str, df.columns))}")
    keep = [c for c in schema if c in out.columns]
    return out[keep].copy()


def normalise_state(value: object) -> str:
    v = _normalise(str(value))
    for canonical, aliases in STATE_ALIASES.items():
        if v == canonical.lower() or v in aliases:
            return canonical
    return "STOP" if v else "IDLE"


def read_table(source: Path | bytes, filename: str | None = None) -> pd.DataFrame:
    name = (filename or (str(source) if isinstance(source, Path) else "")).lower()
    data = BytesIO(source) if isinstance(source, bytes) else source
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(data)
    # CSV: let pandas sniff ; vs , and handle German decimal commas later
    return pd.read_csv(data, sep=None, engine="python")


def load_line_events(source: Path | bytes, filename: str | None = None) -> pd.DataFrame:
    raw = read_table(source, filename)
    df = map_columns(raw, LINE_SCHEMA, required=("timestamp", "machine", "state"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    if df["timestamp"].isna().any():
        raise IngestError("Zeitstempel konnten nicht gelesen werden (Format z. B. 2026-09-21 06:00:00).")
    df["machine"] = df["machine"].astype(str).str.strip()
    df["state"] = df["state"].map(normalise_state)
    df = df.sort_values(["machine", "timestamp"]).reset_index(drop=True)
    if "duration_s" not in df.columns or df["duration_s"].isna().all():
        # Derive durations from the next event of the same machine
        nxt = df.groupby("machine")["timestamp"].shift(-1)
        df["duration_s"] = (nxt - df["timestamp"]).dt.total_seconds()
        df["duration_s"] = df["duration_s"].fillna(0)
    df["duration_s"] = pd.to_numeric(df["duration_s"], errors="coerce").fillna(0).clip(lower=0)
    for col in ("good_count", "reject_count"):
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in ("alarm_code", "alarm_text", "batch"):
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)
    if "shift" not in df.columns or df["shift"].isna().all():
        df["shift"] = df["timestamp"].dt.hour.map(lambda h: "Früh" if 6 <= h < 14 else "Spät" if 14 <= h < 22 else "Nacht")
    return df


def load_hourly_board(source: Path | bytes, filename: str | None = None) -> pd.DataFrame:
    raw = read_table(source, filename)
    df = map_columns(raw, BOARD_SCHEMA, required=("date", "hour", "target", "actual"))
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
    df["hour"] = pd.to_numeric(df["hour"], errors="coerce").fillna(0).astype(int)
    for col in ("target", "actual"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in ("reason", "order", "operator", "line", "shift"):
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)
    if (df["shift"] == "").all():
        df["shift"] = df["hour"].map(lambda h: "Früh" if 6 <= h < 14 else "Spät" if 14 <= h < 22 else "Nacht")
    return df


def load_inventory(stock_source: Path | bytes, movements_source: Path | bytes | None = None,
                   stock_name: str | None = None, movements_name: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    stock = map_columns(read_table(stock_source, stock_name), STOCK_SCHEMA, required=("sku", "stock"))
    stock["sku"] = stock["sku"].astype(str).str.strip()
    for col in ("stock", "min_stock", "unit_cost", "avg_daily_usage"):
        if col not in stock.columns:
            stock[col] = 0
        stock[col] = pd.to_numeric(stock[col], errors="coerce").fillna(0)
    if "description" not in stock.columns:
        stock["description"] = ""
    if "last_movement" in stock.columns:
        stock["last_movement"] = pd.to_datetime(stock["last_movement"], errors="coerce")
    movements = None
    if movements_source is not None:
        movements = map_columns(read_table(movements_source, movements_name), MOVEMENT_SCHEMA, required=("date", "sku", "qty"))
        movements["date"] = pd.to_datetime(movements["date"], errors="coerce")
        movements["sku"] = movements["sku"].astype(str).str.strip()
        movements["qty"] = pd.to_numeric(movements["qty"], errors="coerce").fillna(0)
    return stock, movements
