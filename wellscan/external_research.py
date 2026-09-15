"""Opt-in real public-history experiment using the SAME backtest/strategy engine.

Run: python -m wellscan.external_research --symbols AAPL MSFT NVDA --days 2
Optional research dependency: yfinance. No broker authentication or production DB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import pandas as pd

from .backtest import run
from .indicators import normalize_bars
from .models import Candidate, Market, TradingSession
from .objective_report import objective_tables
from .policy import TradingPolicy, estimated_costs
from .sessions import filter_session_bars


def research_bars(raw: pd.DataFrame, market: Market, end_date: str | None) -> pd.DataFrame:
    """Validate only the requested observation window, never future holdout rows."""
    if not isinstance(raw.index, pd.DatetimeIndex) or raw.index.tz is None:
        raise ValueError("source timezone missing")
    local = raw.tz_convert("Asia/Seoul" if market == Market.KR else "America/New_York")
    if end_date:
        local = local.loc[local.index.date <= pd.Timestamp(end_date).date()]
    session = TradingSession.KR_REGULAR if market == Market.KR else TradingSession.US_REGULAR
    selected = filter_session_bars(local, session)
    if market == Market.KR:
        selected = selected.between_time("09:00", "15:29")
    if selected.empty:
        raise ValueError("no regular session bars")
    return normalize_bars(selected)


def cached_version(root: Path | None) -> str:
    if root is None:
        raise ValueError("cache root required")
    record = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if record.get("dataset_role") == "HOLDOUT_CANDIDATE":
        raise ValueError("홀드아웃 후보는 튜닝 재사용 금지 · 고정 버전의 별도 1회 평가 절차 필요")
    return str(record["yfinance_version"])


def download_window(start: str | None, end: str | None) -> dict:
    if start is None and end is None:
        return {"period": "7d"}
    if not start or not end or pd.Timestamp(start) >= pd.Timestamp(end):
        raise ValueError("download start/end must both be supplied in increasing order")
    return {"start": start, "end": end}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "NVDA"])
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--output", default=".scanner_data/research")
    parser.add_argument("--reuse", type=Path, help="previous run directory; verify raw hashes, no download")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--reserve-only", action="store_true", help="reserve a fresh date window; no validation, strategy evaluation or reuse")
    parser.add_argument("--end-date", help="inclusive historical cutoff YYYY-MM-DD; later bars excluded")
    parser.add_argument("--download-start", help="provider requested start date, inclusive")
    parser.add_argument("--download-end", help="provider requested end date, exclusive")
    args = parser.parse_args()
    window = download_window(args.download_start, args.download_end)
    if args.reserve_only and (args.reuse or not args.download_start or not args.download_only):
        parser.error("reserve-only requires explicit download window and download-only; reuse is forbidden")
    if args.reuse and args.download_start:
        parser.error("download window cannot be combined with --reuse")
    yf = None
    if args.reuse is None:
        import yfinance as yf_module
        yf = yf_module
    root = Path(args.output) / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root.mkdir(parents=True, exist_ok=False)
    if yf is not None:
        yf.set_tz_cache_location(str(root / "yf-cache"))
    manifest = {"provider": "Yahoo via yfinance", "yfinance_version": yf.__version__ if yf is not None else cached_version(args.reuse),
                "retrieved_at": datetime.now(UTC).isoformat(), "kind": "real historical OHLCV, NOT synthetic",
                "requested_symbols": args.symbols, "download_window": window,
                "files": [], "raw_files": [], "errors": [],
                "bias": "preselected convenience sample, not historical market-wide selection"}
    manifest["dataset_role"] = "HOLDOUT_CANDIDATE" if args.reserve_only else "TUNING"
    # Preregistration exists BEFORE the first network request. This establishes
    # collection chronology, not proof about every other computer or account.
    (root / "preregister.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    frames, policies, candidates = {}, {}, []
    cached = json.loads((args.reuse / "manifest.json").read_text(encoding="utf-8")) if args.reuse else None
    for symbol in args.symbols:
        request_started = datetime.now(UTC).isoformat()
        print(f"{'REUSE' if cached is not None else 'DOWNLOAD'} {symbol}", flush=True)
        try:
            if cached is not None:
                record = next(item for item in cached.get("raw_files", cached["files"]) if item["symbol"] == symbol)
                source = args.reuse / record["path"]
                if hashlib.sha256(source.read_bytes()).hexdigest() != record["sha256"]:
                    raise ValueError("cached history hash mismatch")
                raw = pd.read_csv(source, index_col=0, parse_dates=True, float_precision="round_trip")
                metadata = {"instrumentType": record["instrument_type"]}
            else:
                ticker = yf.Ticker(symbol)
                raw = ticker.history(**window, interval="1m", prepost=True, auto_adjust=False,
                                     actions=False, repair=False, keepna=True, timeout=20, raise_errors=True)
                metadata = ticker.get_history_metadata()
            if raw.empty:
                raise ValueError("empty history")
            if raw.index.tz is None:
                raise ValueError("source timezone missing")
            raw_path = root / f"{symbol.replace('.', '_')}-raw.csv"
            if cached is not None:
                shutil.copyfile(source, raw_path)
            else:
                raw.to_csv(raw_path, index_label="timestamp")
            raw_record = {"symbol": symbol, "path": raw_path.name, "raw_rows": len(raw),
                          "instrument_type": metadata.get("instrumentType"),
                          "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                          "request_started_at": request_started,
                          "download_completed_at": datetime.now(UTC).isoformat(),
                          "file_created_at": datetime.fromtimestamp(raw_path.stat().st_ctime, UTC).isoformat(),
                          "file_modified_at": datetime.fromtimestamp(raw_path.stat().st_mtime, UTC).isoformat()}
            manifest["raw_files"].append(raw_record)
            if args.reserve_only:
                print(json.dumps({"symbol": symbol, "reserved": True, "sha256": raw_record["sha256"]}), flush=True)
                (root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
                continue
            market = Market.KR if symbol.endswith((".KS", ".KQ")) else Market.US
            session = TradingSession.KR_REGULAR if market == Market.KR else TradingSession.US_REGULAR
            bars = research_bars(raw, market, args.end_date)
            product = {"EQUITY": "STOCK", "ETF": "ETF"}.get(metadata.get("instrumentType"), "UNKNOWN")
            # Only equities in this first sample; ETF leverage cannot be inferred from quoteType.
            if product != "STOCK":
                raise ValueError(f"unsupported/unverified product: {metadata.get('instrumentType')}")
            candidate = Candidate(symbol, metadata.get("shortName", symbol), float(bars.close.iloc[-1]),
                                  float("nan"), float(bars.volume.iloc[-1]), float(bars.close.iloc[-1] * bars.volume.iloc[-1]),
                                  market=market, exchange="KRX" if market == Market.KR else str(metadata.get("exchangeName", "UNKNOWN")), session=session)
            candidates.append(candidate)
            frames[candidate.key] = bars
            policies[candidate.key] = TradingPolicy(estimated_costs(market, session), product)
            manifest["files"].append({"symbol": symbol, "path": raw_path.name, "raw_rows": len(raw),
                                     "regular_rows": len(bars), "first": str(bars.index.min()), "last": str(bars.index.max()),
                                     "instrument_type": metadata.get("instrumentType"),
                                     "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest()})
            print(json.dumps(manifest["files"][-1]), flush=True)
        except Exception as exc:
            manifest["errors"].append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            print(json.dumps(manifest["errors"][-1]), flush=True)
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["end_date"] = args.end_date
    manifest["reused_from"] = str(args.reuse) if args.reuse else None
    manifest["upstream_exclusions"] = cached.get("errors", []) if cached is not None else []
    manifest["code_sha256"] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                               for name in ("engine.py", "opportunities.py", "policy.py", "backtest.py", "indicators.py",
                                            "sequence.py", "sessions.py", "models.py", "statistics.py", "external_research.py", "__init__.py")}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.download_only:
        print(f"ARTIFACTS {root.resolve()}", flush=True)
        return
    for market in (Market.KR, Market.US):
        selected = [item for item in candidates if item.market == market]
        if not selected:
            continue
        started = perf_counter()
        report = run(None, days=args.days, top_n=5, market=market, candidates_override=selected,
                     history_loader=lambda item: frames[item.key], policy_provider=lambda item: policies[item.key],
                     progress=lambda message: print(message, flush=True))
        report["data_manifest"] = "manifest.json"
        report["elapsed_seconds"] = perf_counter() - started
        report["source_errors"] = manifest["errors"]
        if manifest["errors"]:
            report["sample_goal_met"] = False
            report.update(objective_tables(report["trades"], report["coverage"], selected[0].session,
                                           errors=report["errors"] + manifest["errors"]))
        report["data_source"] = manifest["provider"]
        report["limitations"] = ["sample selection bias", "estimated costs", "not broker fills", "not independent holdout", "regular session only"]
        (root / f"report-{market.value}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("market", "status", "total_trades", "win_rate", "avg_return_pct", "errors")}), flush=True)
    print(f"ARTIFACTS {root.resolve()}", flush=True)


if __name__ == "__main__":
    main()
