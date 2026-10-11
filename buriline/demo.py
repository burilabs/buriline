"""Synthetic demo data for a packaging line, an hourly Soll/Ist board and a small warehouse.

The generator is deterministic for a given seed so screenshots, tests and the demo video
stay reproducible. Nothing here is real customer data.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

MACHINES = [
    # name, ideal rate (units per minute), typical stop profile
    ("Blister", 180.0),
    ("Kartonierer", 150.0),
    ("Kartonpacker", 20.0),
]

ALARMS = {
    "Blister": [
        ("E1041", "Folienriss Formstation", 90, 420),
        ("E1102", "Produkt fehlt in Kavität", 10, 45),
        ("E1210", "Siegeltemperatur außerhalb Toleranz", 120, 900),
        ("E1305", "Stanzung Werkzeug blockiert", 60, 300),
    ],
    "Kartonierer": [
        ("E2010", "Faltschachtel nicht aufgerichtet", 8, 40),
        ("E2033", "Beipackzettel fehlt", 15, 60),
        ("E2101", "Leimauftrag Störung", 120, 600),
        ("E2200", "Lichtschranke Einlauf verschmutzt", 20, 90),
    ],
    "Kartonpacker": [
        ("E3005", "Karton klemmt im Magazin", 30, 180),
        ("E3042", "Etikett nicht lesbar", 10, 50),
        ("E3110", "Verschlussklappe Fehlstellung", 60, 400),
    ],
}

REASONS_BOARD = [
    "Materialmangel", "Umrüsten", "Maschinenstörung", "Personal fehlt",
    "Qualitätsprüfung", "Werkzeugwechsel", "Anlauf nach Pause",
]


def _shift_for(ts: datetime) -> str:
    h = ts.hour
    if 6 <= h < 14:
        return "Früh"
    if 14 <= h < 22:
        return "Spät"
    return "Nacht"


def generate_line_events(days: int = 7, seed: int = 42, start: datetime | None = None) -> pd.DataFrame:
    """One row per state interval and machine."""
    rng = random.Random(seed)
    start = start or datetime(2026, 9, 21, 6, 0)
    rows: list[dict] = []
    batch_no = 4711
    for machine, rate in MACHINES:
        alarms = ALARMS[machine]
        t = start
        end = start + timedelta(days=days)
        batch = f"B{batch_no}"
        batch_counter = 0
        while t < end:
            # Planned breaks: 30 min at 10:00, 18:00, 02:00
            if t.minute == 0 and t.hour in (10, 18, 2) and rng.random() < 0.95:
                rows.append(_row(t, machine, "PLANNED_STOP", 1800, None, "Pause", batch, 0, 0))
                t += timedelta(seconds=1800)
                continue
            # Batch change every ~20 production hours
            batch_counter += 1
            if batch_counter > rng.randint(60, 110):
                batch_counter = 0
                batch_no += 1
                batch = f"B{batch_no}"
                rows.append(_row(t, machine, "SETUP", rng.randint(900, 2700), None, "Umrüsten", batch, 0, 0))
                t += timedelta(seconds=rows[-1]["duration_s"])
                continue
            # Run interval
            run_s = rng.randint(180, 1500)
            # Performance loss: the line rarely runs at ideal rate
            eff = rng.uniform(0.82, 0.99)
            if machine == "Kartonpacker" and rng.random() < 0.3:
                eff *= 0.9
            total = int(rate * eff * run_s / 60)
            reject = int(total * rng.uniform(0.001, 0.012))
            rows.append(_row(t, machine, "RUN", run_s, None, None, batch, total - reject, reject))
            t += timedelta(seconds=run_s)
            # What follows the run: an alarm, a micro stop, blocked/starved, or nothing
            r = rng.random()
            if r < 0.45:
                code, text, lo, hi = rng.choice(alarms)
                dur = rng.randint(lo, hi)
                rows.append(_row(t, machine, "STOP", dur, code, text, batch, 0, 0))
                t += timedelta(seconds=dur)
            elif r < 0.75:
                code, text, lo, hi = rng.choice(alarms[:2])  # the quick ones
                dur = rng.randint(5, 55)
                rows.append(_row(t, machine, "STOP", dur, code, text, batch, 0, 0))
                t += timedelta(seconds=dur)
            elif r < 0.87 and machine != "Blister":
                dur = rng.randint(20, 240)
                rows.append(_row(t, machine, "STARVED", dur, None, "Kein Produkt von vorgelagerter Maschine", batch, 0, 0))
                t += timedelta(seconds=dur)
            elif r < 0.93 and machine != "Kartonpacker":
                dur = rng.randint(20, 240)
                rows.append(_row(t, machine, "BLOCKED", dur, None, "Nachgelagerte Maschine nimmt nicht ab", batch, 0, 0))
                t += timedelta(seconds=dur)
    df = pd.DataFrame(rows)
    df["shift"] = df["timestamp"].map(_shift_for)
    df = df.sort_values(["timestamp", "machine"]).reset_index(drop=True)
    return df


def _row(ts, machine, state, dur, code, text, batch, good, reject):
    return {
        "timestamp": ts, "machine": machine, "state": state, "duration_s": int(dur),
        "alarm_code": code or "", "alarm_text": text or "", "batch": batch,
        "good_count": int(good), "reject_count": int(reject),
    }


def machine_rates() -> dict[str, float]:
    return {m: r for m, r in MACHINES}


def generate_hourly_board(days: int = 7, seed: int = 7, start: datetime | None = None) -> pd.DataFrame:
    """Hour-by-hour Soll/Ist board as a supplier shop floor would fill it in."""
    rng = random.Random(seed)
    start = start or datetime(2026, 9, 21, 6, 0)
    rows = []
    orders = ["A-55021", "A-55022", "A-55030", "A-55041"]
    order_i = 0
    target = 420
    for d in range(days):
        day = start + timedelta(days=d)
        for h in range(24):
            ts = day.replace(hour=0) + timedelta(hours=h)
            if ts < start:
                continue
            if h in (10, 18, 2):
                continue  # break hour handled as reduced target
            if rng.random() < 0.02:
                order_i = (order_i + 1) % len(orders)
                target = rng.choice([380, 420, 450])
            noise = rng.gauss(0, 25)
            actual = int(target + noise)
            reason = ""
            if rng.random() < 0.22:
                reason = rng.choice(REASONS_BOARD)
                actual = int(target * rng.uniform(0.45, 0.88))
            if _shift_for(ts) == "Spät" and ts.weekday() == 1 and rng.random() < 0.6:
                reason = "Materialmangel"
                actual = int(target * rng.uniform(0.5, 0.7))
            rows.append({
                "date": ts.date().isoformat(), "hour": h, "shift": _shift_for(ts), "line": "Linie 3",
                "order": orders[order_i], "target": target, "actual": max(actual, 0),
                "reason": reason, "operator": rng.choice(["MA-12", "MA-07", "MA-31"]),
            })
    return pd.DataFrame(rows)


def generate_inventory(seed: int = 3, n: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stock list plus 90 days of movements for a parts warehouse."""
    rng = random.Random(seed)
    today = datetime(2026, 9, 28)
    stock_rows, mov_rows = [], []
    for i in range(n):
        sku = f"{4700 + i}"
        usage = rng.choice([0, 0.2, 0.5, 1, 2, 5, 12, 30])
        cost = round(rng.uniform(0.4, 180), 2)
        stock = int(usage * rng.uniform(5, 40)) if usage else rng.randint(0, 60)
        min_stock = int(usage * 10)
        if i % 17 == 0:
            stock = -rng.randint(1, 8)  # booking errors happen
        last_mov = today - timedelta(days=rng.randint(0, 400 if usage == 0 else 20))
        stock_rows.append({
            "sku": sku, "description": f"Teil {sku}", "stock": stock, "min_stock": min_stock,
            "unit_cost": cost, "avg_daily_usage": usage, "last_movement": last_mov.date().isoformat(),
            "location": f"R{rng.randint(1, 9)}-{rng.randint(1, 40):02d}",
        })
        for d in range(90):
            if usage and rng.random() < min(0.9, usage / 6 + 0.1):
                mov_rows.append({"date": (today - timedelta(days=d)).date().isoformat(), "sku": sku,
                                 "qty": -int(max(1, rng.gauss(usage, usage / 2 + 0.1))), "type": "Entnahme"})
            if usage and rng.random() < 0.05:
                mov_rows.append({"date": (today - timedelta(days=d)).date().isoformat(), "sku": sku,
                                 "qty": int(usage * rng.randint(10, 40)), "type": "Wareneingang"})
    return pd.DataFrame(stock_rows), pd.DataFrame(mov_rows)


def write_demo(out_dir: Path, days: int = 7) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "line_events": out_dir / "line_events.csv",
        "hourly_board": out_dir / "hourly_board.csv",
        "inventory_stock": out_dir / "inventory_stock.csv",
        "inventory_movements": out_dir / "inventory_movements.csv",
    }
    generate_line_events(days).to_csv(paths["line_events"], index=False)
    generate_hourly_board(days).to_csv(paths["hourly_board"], index=False)
    stock, mov = generate_inventory()
    stock.to_csv(paths["inventory_stock"], index=False)
    mov.to_csv(paths["inventory_movements"], index=False)
    return paths
