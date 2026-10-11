"""Command line entry point: ``buriline serve``, ``buriline demo``, ``buriline verify-audit``."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .config import settings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="buriline", description="BuriLine – Betriebsdaten-Analyse, offline.")
    p.add_argument("--version", action="version", version=f"buriline {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="Web-Oberfläche starten")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8800)
    d = sub.add_parser("demo", help="Synthetische Demo-Daten erzeugen")
    d.add_argument("--out", default=str(settings.data_dir))
    d.add_argument("--days", type=int, default=7)
    sub.add_parser("verify-audit", help="Audit-Trail auf Unversehrtheit prüfen")
    a = sub.add_parser("analyse", help="Kennzahlen einer Exportdatei als JSON ausgeben")
    a.add_argument("file")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("buriline.web.app:app", host=args.host, port=args.port)
        return 0
    if args.cmd == "demo":
        from . import demo
        paths = demo.write_demo(Path(args.out), days=args.days)
        (Path(args.out) / "ideal_rates.json").write_text(json.dumps(demo.machine_rates()))
        for k, v in paths.items():
            print(f"{k}: {v}")
        return 0
    if args.cmd == "verify-audit":
        from .audit import AuditTrail
        ok, count, msg = AuditTrail(settings.audit_file).verify()
        print(f"{msg} ({count} Einträge)")
        return 0 if ok else 1
    if args.cmd == "analyse":
        from . import analytics, ingest
        df = ingest.load_line_events(Path(args.file))
        print(json.dumps(analytics.line_summary(df), ensure_ascii=False, indent=2, default=str))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
