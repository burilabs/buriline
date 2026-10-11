"""Language-model layer.

The model never sees raw data and never computes numbers. It receives the JSON summary
produced by the analytics modules and is asked to explain, summarise or answer questions
strictly from those numbers. Two providers exist:

* ``OllamaProvider`` talks to a local Ollama instance (no internet needed once the model is
  on the machine).
* ``RuleBasedProvider`` produces a deterministic German explanation without any model. It is
  the fallback when Ollama is not reachable and the reference for what a good answer
  must contain.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings

SYSTEM_PROMPT_DE = """Du bist BuriLine, ein Assistent für Produktions- und Lagerkennzahlen in einem Industriebetrieb.
Du bekommst eine JSON-Zusammenfassung mit bereits berechneten Kennzahlen. Regeln:
1. Verwende ausschließlich Zahlen, die in der Zusammenfassung stehen. Rechne nichts selbst aus und erfinde keine Werte.
2. Antworte auf Deutsch, sachlich, kurz, für einen Schichtleiter oder Meister.
3. Nenne bei jeder Aussage die Maschine, Schicht oder den Artikel, auf den sie sich bezieht.
4. Wenn die Zusammenfassung eine Frage nicht beantworten kann, sage das klar.
5. Prozentwerte sind in der Zusammenfassung als Dezimalzahl angegeben (0.87 = 87 %)."""


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    ok: bool
    note: str = ""


class OllamaProvider:
    name = "ollama"

    def __init__(self, url: str | None = None, model: str | None = None, timeout: float | None = None):
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.model
        self.timeout = timeout or settings.llm_timeout_s

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=3.0)
            return [m["name"] for m in r.json().get("models", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    def chat(self, system: str, user: str) -> LLMResult:
        payload = {"model": self.model, "stream": False, "options": {"temperature": 0.2},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
            text = r.json()["message"]["content"].strip()
            return LLMResult(text=text, model=self.model, provider=self.name, ok=True)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return LLMResult(text="", model=self.model, provider=self.name, ok=False, note=str(exc))


class RuleBasedProvider:
    """Deterministic German text from the summary. No model involved."""
    name = "regelbasiert"
    model = "keins"

    def available(self) -> bool:
        return True

    def explain_line(self, s: dict[str, Any]) -> str:
        parts = [f"Zeitraum {s['period']['from'][:10]} bis {s['period']['to'][:10]} ({s['period']['days']} Tage)."]
        for m in s["machines"]:
            oee = f", OEE {m['oee']*100:.0f} %" if m.get("oee") is not None else ""
            parts.append(
                f"{m['machine']}: Verfügbarkeit {m['availability']*100:.0f} %{oee}, "
                f"{m['stops']} Stillstände ({m['microstops']} davon Mikrostopps unter {s['microstop_threshold_s']} s), "
                f"{m['stop_s']/60:.0f} min Stillstand, {m['setup_s']/60:.0f} min Rüsten, "
                f"{m['flow_loss_s']/60:.0f} min blockiert oder ohne Produkt.")
        if s["pareto"]:
            top = s["pareto"][0]
            parts.append(f"Größter Stillstandsgrund: {top['alarm_text']} ({top['alarm_code']}) an {top['machine']} "
                         f"mit {top['duration_min']:.0f} min in {top['count']} Fällen, das sind {top['share']*100:.0f} % der Stillstandszeit.")
        b = s.get("bottleneck")
        if b:
            parts.append(f"Engpass: {b['machine']} mit {b['own_downtime_min']:.0f} min eigener Stillstands- und Rüstzeit. "
                         f"Niedrigste Verfügbarkeit hat {b['lowest_availability']}.")
        if s["microstops"]:
            mcount = sum(x["count"] for x in s["microstops"])
            parts.append(f"Mikrostopps: {mcount} kurze Unterbrechungen, häufigster Grund {s['microstops'][0]['alarm_text']} an {s['microstops'][0]['machine']}.")
        if s["shifts"]:
            worst = min(s["shifts"], key=lambda x: x["availability"])
            best = max(s["shifts"], key=lambda x: x["availability"])
            parts.append(f"Schwächste Schicht: {worst['shift']} am {worst['date']} mit {worst['availability']*100:.0f} % Verfügbarkeit, "
                         f"beste: {best['shift']} am {best['date']} mit {best['availability']*100:.0f} %.")
        t = s["totals"]
        parts.append(f"Gesamt: {t['good_count']} Gutstück, {t['reject_count']} Ausschuss, {t['stop_min']:.0f} min Stillstand in {t['stops']} Ereignissen.")
        return "\n".join(parts)

    def explain_board(self, s: dict[str, Any]) -> str:
        t = s["totals"]
        parts = [f"Stundentafel {s['period']['from']} bis {s['period']['to']}: Soll {t['target']}, Ist {t['actual']}, "
                 f"Zielerreichung {t['attainment']*100:.0f} %. {t['hours_below_target']} von {t['hours']} Stunden unter Soll."]
        if s["reasons"]:
            r = s["reasons"][0]
            parts.append(f"Häufigster Abweichungsgrund: {r['reason']} in {r['hours']} Stunden, {int(r['lost'])} Stück verloren.")
        if s["by_shift"]:
            worst = min(s["by_shift"], key=lambda x: x["attainment"])
            parts.append(f"Schwächste Schicht: {worst['shift']} am {worst['date']} mit {worst['attainment']*100:.0f} % Zielerreichung.")
        if s["worst_hours"]:
            w = s["worst_hours"][0]
            parts.append(f"Schlechteste Stunde: {w['date']} {w['hour']}:00 Uhr ({w['shift']}), Soll {w['target']}, Ist {w['actual']}"
                         + (f", Grund: {w['reason']}." if w['reason'] else ", kein Grund eingetragen."))
        return "\n".join(parts)

    def explain_inventory(self, s: dict[str, Any]) -> str:
        t = s["totals"]
        parts = [f"{t['skus']} Artikel, Bestandswert {t['stock_value']:.0f} EUR. {t['negative']} Artikel mit negativem Bestand, "
                 f"{t['below_min']} unter Mindestbestand, {t['runout_14d']} reichen weniger als 14 Tage, "
                 f"{t['slow_movers']} Ladenhüter mit {t['slow_value']:.0f} EUR gebundenem Kapital."]
        if s["negative"]:
            n = s["negative"][0]
            parts.append(f"Negativer Bestand zum Beispiel bei Artikel {n['sku']} ({n['stock']}): meist eine Entnahme ohne Buchung des Wareneingangs oder eine Doppelbuchung.")
        if s["reorder_suggestions"]:
            r = s["reorder_suggestions"][0]
            parts.append(f"Dringendste Nachbestellung: Artikel {r['sku']}, Reichweite {r['reach_days']} Tage, Vorschlag {r['suggested_qty']} Stück für 30 Tage.")
        return "\n".join(parts)

    def answer(self, question: str, summaries: dict[str, Any]) -> str:
        """Very small keyword router so the fallback still gives a useful answer."""
        q = question.lower()
        texts = []
        if "line" in summaries and any(k in q for k in ("stillstand", "oee", "verfügbar", "maschine", "engpass", "mikro", "schicht", "störung")):
            texts.append(self.explain_line(summaries["line"]))
        if "board" in summaries and any(k in q for k in ("soll", "ist", "stückzahl", "stundentafel", "ziel")):
            texts.append(self.explain_board(summaries["board"]))
        if "inventory" in summaries and any(k in q for k in ("lager", "bestand", "artikel", "nachbestell", "inventur", "negativ")):
            texts.append(self.explain_inventory(summaries["inventory"]))
        if not texts:
            for key, fn in (("line", self.explain_line), ("board", self.explain_board), ("inventory", self.explain_inventory)):
                if key in summaries:
                    texts.append(fn(summaries[key]))
        return "Hinweis: Kein Sprachmodell erreichbar, dies ist die regelbasierte Zusammenfassung.\n\n" + "\n\n".join(texts)


def build_user_prompt(question: str, summaries: dict[str, Any]) -> str:
    compact = {k: _compact(v) for k, v in summaries.items()}
    return f"Zusammenfassung (JSON):\n{json.dumps(compact, ensure_ascii=False)}\n\nFrage: {question}"


def _compact(summary: dict[str, Any]) -> dict[str, Any]:
    """Drop the row-level data so the prompt stays small for a 3B model."""
    out = dict(summary)
    out.pop("rows", None)
    for key in ("pareto", "microstops", "shifts", "trend", "worst_hours", "below_min", "slow_movers", "runout", "negative", "reorder_suggestions", "by_shift", "weekday_shift"):
        if key in out and isinstance(out[key], list):
            out[key] = out[key][:8]
    return out


class Assistant:
    """Chooses the provider and keeps the audit trail informed."""

    def __init__(self, ollama: OllamaProvider | None = None):
        self.ollama = ollama or OllamaProvider()
        self.rules = RuleBasedProvider()

    def status(self) -> dict[str, Any]:
        up = self.ollama.available()
        return {"ollama_url": self.ollama.url, "ollama_available": up, "model": self.ollama.model,
                "models_installed": self.ollama.models() if up else [], "fallback": self.rules.name}

    def ask(self, question: str, summaries: dict[str, Any]) -> LLMResult:
        if self.ollama.available():
            res = self.ollama.chat(SYSTEM_PROMPT_DE, build_user_prompt(question, summaries))
            if res.ok and res.text:
                return res
            note = res.note
        else:
            note = "Ollama nicht erreichbar"
        return LLMResult(text=self.rules.answer(question, summaries), model=self.rules.model,
                         provider=self.rules.name, ok=True, note=note)

    def shift_report(self, summaries: dict[str, Any]) -> LLMResult:
        q = ("Schreibe einen Schichtbericht für die Frühbesprechung: 1) Wichtigste Kennzahlen, "
             "2) Die drei größten Verluste mit Zahlen, 3) Empfohlene Maßnahmen für die nächste Schicht. Maximal 180 Wörter.")
        return self.ask(q, summaries)
