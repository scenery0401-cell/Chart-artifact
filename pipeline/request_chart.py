#!/usr/bin/env python3
"""Create one INVERT chart and classify a requested ticker (GitHub IssueOps runner).

Never run this from an untrusted browser. A validated owner-created issue invokes it
on the GitHub Actions runner; unknown sectors stay explicitly unclassified.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import deep_scan_job as DS

SYMBOL = re.compile(r"[A-Z0-9^=.\-]{1,15}\Z")
SOURCE_URL = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/{ex}/{ex}_full_tickers.json"


def classify(ticker, outdir, watch):
    for sector in watch.get("sectors", []):
        for row in sector.get("items", []):
            if row.get("t") == ticker:
                return sector["name"], row.get("name") or ticker, "existing"

    # Daily 1,000-stock universe provides a verified sector for many scanned tickers.
    f = outdir / "today.json"
    if f.exists():
        try:
            for row in json.loads(f.read_text(encoding="utf-8")).get("stocks", []):
                if row.get("t") == ticker and row.get("grp") not in (None, "", "기타"):
                    return row["grp"], row.get("name") or ticker, "daily_universe"
        except (OSError, ValueError):
            pass

    # Look up the exchange's public ticker catalog; do not infer sector from the
    # ticker spelling or company name. The catalog may omit some tickers.
    for ex in ("nyse", "nasdaq", "amex"):
        try:
            url = SOURCE_URL.format(ex=ex)
            req = urllib.request.Request(url, headers={"User-Agent": "INVERT-Chart-Bot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as response:
                rows = json.load(response)
            for row in rows:
                if str(row.get("symbol", "")).upper().replace(".", "-") != ticker:
                    continue
                sec = (row.get("sector") or "").strip()
                name = (row.get("name") or ticker).strip()
                if sec:
                    return DS.SECTOR_KO.get(sec, sec), name, "exchange_catalog"
                return "미분류", name, "sector_missing"
        except (OSError, ValueError, TypeError) as e:
            print(f"Sector catalog {ex} unavailable: {e}", file=sys.stderr)
    return "미분류", ticker, "sector_unverified"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--out", default="docs/data")
    args = ap.parse_args()
    ticker = args.ticker.strip().upper()
    if not SYMBOL.fullmatch(ticker):
        raise SystemExit("Invalid ticker")
    outdir = ROOT / args.out
    outdir.mkdir(parents=True, exist_ok=True)
    wfile = outdir / "watchlist.json"
    watch = json.loads(wfile.read_text(encoding="utf-8"))
    watch = watch.get("data", watch)
    sector, name, origin = classify(ticker, outdir, watch)
    print(json.dumps({"ticker": ticker, "sector": sector, "classification_source": origin}, ensure_ascii=False), flush=True)

    # Compute first: invalid or unavailable symbols must not enter the watchlist.
    with tempfile.TemporaryDirectory() as td:
        cmd = [sys.executable, "-I", str(ROOT / "pipeline/chart_job.py"),
               "--engine", str(ROOT / "pipeline/pullback-engine.mjs"),
               "--out", td, "--cutoffs", str(ROOT / "pipeline/mom_cutoffs.json"), ticker]
        subprocess.run(cmd, check=True, timeout=350)
        report = json.loads((pathlib.Path(td) / "summary.json").read_text(encoding="utf-8"))
        if not report or "error" in report[0]:
            raise SystemExit("Chart unavailable: " + str(report[0].get("error", "no result"))[:180])
        subprocess.run([sys.executable, "-I", str(ROOT / "pipeline/chart_index.py"),
                        "--out", str(outdir), td], check=True, timeout=60)

    is_fixed = any(f.get("t") == ticker for f in watch.get("fixed", []))
    is_existing = any(item.get("t") == ticker for group in watch.get("sectors", []) for item in group.get("items", []))
    if not (is_existing or is_fixed):
        group = next((g for g in watch.get("sectors", []) if g.get("name") == sector), None)
        if group is None:
            group = {"name": sector, "items": []}
            watch.setdefault("sectors", []).append(group)
        group["items"].append({"t": ticker, "key": ticker.replace("^", "~").replace("=", "@"),
                               "name": report[0].get("name") or name})
        group["items"].sort(key=lambda x: x["t"])
    watch["count"] = sum(len(g["items"]) for g in watch.get("sectors", []))
    watch["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wfile.write_text(json.dumps(watch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"CHART_READY {ticker} {sector} {report[0].get('asof')} classification={origin}", flush=True)


if __name__ == "__main__":
    main()
