<div align="center">

<img src="https://raw.githubusercontent.com/burilabs/burios/main/logo.png" alt="BuriLine" width="110"/>

<h1>BuriLine</h1>

<p>Betriebsdaten-Analyse mit KI, die das Werk nie verlässt.<br>
<em>Offline production and warehouse analytics with a local language model.</em></p>

<img src="https://img.shields.io/badge/version-0.1.0-blue"/>
<img src="https://img.shields.io/badge/runs-100%25%20offline-E67E22"/>
<img src="https://img.shields.io/badge/python-3.11%2B-green"/>
<img src="https://img.shields.io/badge/LLM-Ollama%20%7C%20Qwen%202.5-orange"/>
<img src="https://img.shields.io/badge/license-MIT-yellow"/>

</div>

---

Maschinendaten gehören ins Werk, nicht in die Cloud. BuriLine läuft auf einem Mini-PC in der Produktion,
liest die Exporte, die jede Linie ohnehin liefert, und beantwortet die Fragen, die der Meister sonst abends
von Hand ausrechnet.

- **Kennzahlen werden deterministisch berechnet**, nachrechenbar für jeden Auditor. Das Sprachmodell rechnet nie.
- **Das Sprachmodell läuft lokal** (Ollama, z. B. Qwen 2.5 3B) und erklärt die Zahlen auf Deutsch, beantwortet Rückfragen und schreibt den Schichtbericht. Ohne Modell liefert BuriLine eine regelbasierte Erklärung.
- **Audit-Trail**: jede Frage, jede Antwort, jeder Datensatz-Hash, jedes Modell. Nur anhängend, SHA-256-verkettet, prüfbar.
- **Keine Abhängigkeit nach außen**: keine CDN-Skripte, keine Telemetrie, der Modell-Container hat im Compose-Setup keine Route ins Internet.

## Drei Anwendungen, ein Kern

| Feld | Datenquelle | Berechnet | Die KI macht daraus |
|---|---|---|---|
| **Stillstandsanalyse** | Maschinenzustände, Alarmlisten (CSV/XLSX) | OEE je Maschine, Verfügbarkeit, Leistung, Qualität, Pareto der Stillstandsgründe, Mikrostopps, MTBF/MTTR, Engpass, Schicht- und Tagesverlauf | Erklärt Stillstände, Schichtbericht |
| **Stundentafel Soll/Ist** | Eintragungen vom Terminal, Zähler | Zielerreichung je Stunde, Schicht, Auftrag; Abweichungsgründe; Wochentag × Schicht | "Warum lagen wir Dienstag in der Spätschicht unter Soll?" |
| **Lager** | Bestandsliste, Bewegungen aus WMS/ERP | Reichweite, Fehlmengen, negative Bestände, Ladenhüter, ABC, Nachbestellvorschläge | "Warum ist Artikel 4711 negativ?" |

## Schnellstart

```bash
git clone https://github.com/burilabs/buriline
cd buriline
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
buriline demo          # synthetische Verpackungslinie, Stundentafel und Lager nach ./data
buriline serve         # http://127.0.0.1:8800
```

Unter *Daten* eigene CSV- oder Excel-Exporte hochladen. Spaltennamen werden auf Deutsch und Englisch erkannt
(`Zeitstempel/timestamp`, `Maschine/machine`, `Zustand/state`, `Dauer`, `Fehlercode`, `Fehlertext`, `Gutstück`, `Ausschuss`, …).

### Mit lokalem Sprachmodell

```bash
# einmalig auf einem Rechner mit Internet, danach per USB ins Werk oder direkt auf dem Werks-PC
ollama pull qwen2.5:3b
# BuriLine findet Ollama unter http://127.0.0.1:11434; anderes Modell über BURILINE_MODEL
```

Ist Ollama nicht erreichbar, antwortet BuriLine regelbasiert und kennzeichnet das in jeder Antwort.

### Docker (Werks-Server)

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen2.5:3b   # einmalig; die Registry ist dafür nötig
```

Der Ollama-Container hängt nur am internen Netz und kann nicht nach draußen. Daten liegen im Volume `buriline-data`.

## Datenformate

**Maschinenzustände** (eine Zeile je Zustandsintervall):

```
timestamp;machine;state;duration_s;alarm_code;alarm_text;batch;good_count;reject_count
2026-09-21 06:00:00;Blister;RUN;600;;;B4711;1780;4
2026-09-21 06:10:00;Blister;STOP;95;E1041;Folienriss Formstation;B4711;0;0
```

Zustände: `RUN`, `STOP`, `SETUP`, `PLANNED_STOP`, `BLOCKED`, `STARVED`, `IDLE` (deutsche Varianten wie `läuft`, `Störung`, `Rüsten`, `Pause` werden erkannt). Fehlt `duration_s`, wird die Dauer aus dem nächsten Ereignis derselben Maschine berechnet. Die Idealrate je Maschine kann in `data/ideal_rates.json` hinterlegt werden, sonst wird sie als 95. Perzentil der beobachteten Laufraten geschätzt.

**Stundentafel**: `date;hour;shift;line;order;target;actual;reason;operator`

**Lager**: Bestandsliste `sku;description;stock;min_stock;unit_cost;avg_daily_usage;last_movement;location`, optional Bewegungen `date;sku;qty;type`.

## Audit-Trail

`data/audit.jsonl` ist eine Kette: jeder Eintrag enthält den SHA-256 des vorherigen. `buriline verify-audit` prüft die Kette, die Oberfläche zeigt den Status unter *Audit-Trail*. Einträge werden von der Anwendung nie geändert oder gelöscht.

## Entwicklung

```bash
pip install -e .[dev]
pytest
```

## Status

Version 0.1.0 ist ein funktionsfähiger Prototyp mit synthetischen Daten. Gesucht sind Pilotbetriebe mit echten Alarmlisten und Stundentafeln. Geplant: OPC UA / MQTT als Live-Quelle, Instandhaltung (Handbuch-Suche zum Fehlercode), Abweichungsberichte für GMP, Debian-Paket für den Betrieb auf [BuriOS](https://github.com/burilabs/burios).

---

<div align="center">Made by <a href="https://brlabs.dev">BuriLabs</a></div>
