"""FastAPI web application. Everything is served from this process, no external
resources (fonts, scripts, CDNs) are referenced, so it works on an air-gapped machine."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__, analytics, demo, ingest, inventory
from ..ai import Assistant
from ..audit import AuditTrail, sha256_of_bytes, sha256_of_file
from ..config import settings

HERE = Path(__file__).parent
app = FastAPI(title="BuriLine", version=__version__)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["pct"] = lambda v: "–" if v is None else f"{v * 100:.0f} %"
templates.env.filters["num"] = lambda v: "–" if v is None else f"{v:,.0f}".replace(",", ".")
templates.env.filters["num1"] = lambda v: "–" if v is None else f"{v:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
templates.env.filters["minutes"] = lambda s: "–" if s is None else f"{s / 60:,.0f} min".replace(",", ".")


class State:
    """Datasets currently loaded. Kept in memory and mirrored to the data directory so a
    restart picks them up again."""

    def __init__(self) -> None:
        settings.ensure_dirs()
        self.audit = AuditTrail(settings.audit_file)
        self.assistant = Assistant()
        self.line: pd.DataFrame | None = None
        self.board: pd.DataFrame | None = None
        self.stock: pd.DataFrame | None = None
        self.movements: pd.DataFrame | None = None
        self.hashes: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.ideal_rates: dict[str, float] = {}
        self._summaries: dict[str, Any] | None = None
        self._restore()

    # ------------------------------------------------------------ persistence
    def _restore(self) -> None:
        d = settings.data_dir
        for key, name, loader in (("line", "line_events", ingest.load_line_events),
                                  ("board", "hourly_board", ingest.load_hourly_board)):
            for ext in (".csv", ".xlsx"):
                p = d / f"{name}{ext}"
                if p.exists():
                    try:
                        setattr(self, key, loader(p))
                        self.hashes[key] = sha256_of_file(p)
                        self.names[key] = p.name
                    except ingest.IngestError:
                        pass
                    break
        sp, mp = d / "inventory_stock.csv", d / "inventory_movements.csv"
        if sp.exists():
            try:
                self.stock, self.movements = ingest.load_inventory(sp, mp if mp.exists() else None)
                self.hashes["inventory"] = sha256_of_file(sp)
                self.names["inventory"] = sp.name
            except ingest.IngestError:
                pass
        rates = d / "ideal_rates.json"
        if rates.exists():
            self.ideal_rates = json.loads(rates.read_text())

    def load_demo(self) -> None:
        paths = demo.write_demo(settings.data_dir)
        (settings.data_dir / "ideal_rates.json").write_text(json.dumps(demo.machine_rates()))
        self.ideal_rates = demo.machine_rates()
        self._restore()
        self._summaries = None
        self.audit.append("demo_geladen", details={k: v for k, v in self.hashes.items()})

    def store_upload(self, kind: str, data: bytes, filename: str) -> str:
        ext = ".xlsx" if filename.lower().endswith((".xlsx", ".xlsm", ".xls")) else ".csv"
        target = settings.data_dir / f"{kind}{ext}"
        if kind == "line_events":
            self.line = ingest.load_line_events(data, filename); key = "line"
        elif kind == "hourly_board":
            self.board = ingest.load_hourly_board(data, filename); key = "board"
        elif kind == "inventory_stock":
            self.stock, _ = ingest.load_inventory(data, None, filename); key = "inventory"
        elif kind == "inventory_movements":
            if self.stock is None:
                raise ingest.IngestError("Bitte zuerst die Bestandsliste hochladen.")
            _, self.movements = ingest.load_inventory(settings.data_dir / "inventory_stock.csv", data, None, filename); key = "inventory"
        else:
            raise ingest.IngestError(f"Unbekannter Datentyp {kind}")
        for old in settings.data_dir.glob(f"{kind}.*"):
            old.unlink()
        target.write_bytes(data)
        self.hashes[key] = sha256_of_bytes(data)
        self.names[key] = filename
        self._summaries = None
        self.audit.append("datei_geladen", dataset_sha256=self.hashes[key], details={"typ": kind, "datei": filename, "bytes": len(data)})
        return key

    # ------------------------------------------------------------ analytics
    def summaries(self) -> dict[str, Any]:
        if self._summaries is None:
            out: dict[str, Any] = {}
            if self.line is not None and not self.line.empty:
                out["line"] = analytics.line_summary(self.line, self.ideal_rates)
            if self.board is not None and not self.board.empty:
                out["board"] = analytics.board_summary(self.board)
            if self.stock is not None and not self.stock.empty:
                out["inventory"] = inventory.inventory_summary(self.stock, self.movements)
            self._summaries = out
        return self._summaries

    def dataset_hash(self) -> str:
        return ",".join(f"{k}:{v[:12]}" for k, v in sorted(self.hashes.items())) or "keine Daten"


state = State()


def ctx(request: Request, **extra: Any) -> dict[str, Any]:
    return {"version": __version__, "names": state.names, "hashes": state.hashes,
            "has": {"line": state.line is not None, "board": state.board is not None, "inventory": state.stock is not None},
            "now": datetime.now().strftime("%d.%m.%Y %H:%M"), **extra}


def render(request: Request, name: str, **extra: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx(request, **extra))


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    s = state.summaries()
    return render(request, "dashboard.html", s=s.get("line"), ai=state.assistant.status())


@app.get("/stundentafel", response_class=HTMLResponse)
def board_page(request: Request):
    s = state.summaries()
    return render(request, "board.html", s=s.get("board"))


@app.get("/lager", response_class=HTMLResponse)
def inventory_page(request: Request):
    s = state.summaries()
    return render(request, "inventory.html", s=s.get("inventory"))


@app.get("/fragen", response_class=HTMLResponse)
def ask_page(request: Request):
    return render(request, "ask.html", ai=state.assistant.status(), history=state.audit.records(20))


@app.post("/api/ask")
def api_ask(question: str = Form(...), user: str = Form("bediener")):
    summaries = state.summaries()
    if not summaries:
        return JSONResponse({"ok": False, "error": "Keine Daten geladen."}, status_code=400)
    res = state.assistant.ask(question.strip(), summaries)
    rec = state.audit.append("frage", user=user, dataset_sha256=state.dataset_hash(), model=f"{res.provider}:{res.model}",
                             prompt=question.strip(), answer=res.text, details={"hinweis": res.note})
    return {"ok": True, "answer": res.text, "model": res.model, "provider": res.provider, "note": res.note, "audit_hash": rec["hash"]}


@app.get("/bericht", response_class=HTMLResponse)
def report_page(request: Request, user: str = "bediener"):
    summaries = state.summaries()
    text = ""
    res = None
    if summaries:
        res = state.assistant.shift_report(summaries)
        text = res.text
        state.audit.append("schichtbericht", user=user, dataset_sha256=state.dataset_hash(),
                           model=f"{res.provider}:{res.model}", answer=text)
    return render(request, "report.html", s=summaries, text=text, res=res)


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    ok, count, msg = state.audit.verify()
    return render(request, "audit.html", records=state.audit.records(200), ok=ok, count=count, msg=msg,
                  audit_file=str(settings.audit_file))


@app.get("/daten", response_class=HTMLResponse)
def data_page(request: Request, error: str = "", info: str = ""):
    return render(request, "data.html", error=error, info=info, ai=state.assistant.status())


@app.post("/daten/upload")
async def upload(kind: str = Form(...), file: UploadFile = File(...)):
    data = await file.read()
    try:
        key = state.store_upload(kind, data, file.filename or "upload.csv")
    except ingest.IngestError as exc:
        return RedirectResponse(url=f"/daten?error={exc}", status_code=303)
    except Exception as exc:  # noqa: BLE001 - show the reason to the operator
        return RedirectResponse(url=f"/daten?error=Datei konnte nicht gelesen werden: {exc}", status_code=303)
    return RedirectResponse(url=f"/daten?info=Geladen: {file.filename} ({key})", status_code=303)


@app.post("/daten/demo")
def load_demo():
    state.load_demo()
    return RedirectResponse(url="/?info=demo", status_code=303)


@app.get("/api/summary")
def api_summary():
    return state.summaries()


@app.get("/api/status")
def api_status():
    return {"version": __version__, "datasets": state.names, "hashes": state.hashes, "ai": state.assistant.status(),
            "audit": dict(zip(("ok", "records", "message"), state.audit.verify()))}


@app.get("/gesundheit")
def health():
    return {"status": "ok"}
