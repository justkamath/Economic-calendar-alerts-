"""Keeps exact expiry dates fresh, with no typing.

Once a day it downloads Upstox's public instrument files (every live NSE and MCX
contract, with its expiry date), picks out the instruments this bot covers and
writes them to expiry_synced.json. If anything goes wrong the old dates stay and
only the owner hears about it. Dates further out than the exchanges have listed
fall back to the estimates in expiry.py.
"""
import gzip
import json
import os
import urllib.request
from datetime import datetime, timedelta

import config as cfg
import expiry

URLS = ("https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz",
        "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz")
MAX_DOWNLOAD_BYTES = 120 * 1024 * 1024
SYNC_EVERY = timedelta(hours=int(os.environ.get("EXPIRY_SYNC_HOURS", "24") or 24))
RETRY_AFTER_FAILURE = timedelta(hours=6)
NOTICE_EVERY = timedelta(hours=24)
HORIZON_DAYS = 800
SYNCED_FILE = expiry.SYNCED_FILE

# Upstox names (spaces removed, upper case) -> our instrument key
MCX_NAMES = {"GOLD": "gold", "GOLDM": "goldm", "SILVER": "silver", "SILVERM": "silverm",
             "CRUDEOIL": "crude", "CRUDEOILM": "crudem", "NATURALGAS": "natgas"}
NSE_COMMODITY_NAMES = {"CRUDEOIL": "nsecrude", "NATURALGAS": "nsenatgas"}
NSE_INDEX_NAMES = {"NIFTY": "nifty", "BANKNIFTY": "banknifty", "FINNIFTY": "finnifty",
                   "MIDCPNIFTY": "midcap", "NIFTYNXT50": "next50"}
SEGMENTS = {"MCX_FO": MCX_NAMES, "NSE_COM": NSE_COMMODITY_NAMES, "NSE_FO": NSE_INDEX_NAMES}
NAME_FIELDS = ("underlying_symbol", "name", "asset_symbol")


def _norm(text):
    return "".join(str(text or "").split()).upper()


def _instrument(record, names):
    """Our key for this contract, looking at each name-like field in turn."""
    candidates = [record.get(f) for f in NAME_FIELDS]
    candidates.append((str(record.get("trading_symbol") or "").split() or [""])[0])
    for candidate in candidates:
        key = names.get(_norm(candidate))
        if key:
            return key
    return None


def _kind(segment, record):
    options = record.get("instrument_type") in ("CE", "PE")
    if segment == "NSE_FO":
        return "Weekly options" if options and record.get("weekly") else "Monthly futures & options"
    return "Options" if options else "Futures"


def parse(records, today):
    """Rows [{i, t, d, listed}] for the instruments we cover, soonest first."""
    seen, rows = set(), []
    last = today + timedelta(days=HORIZON_DAYS)
    for record in records:
        if not isinstance(record, dict) or record.get("instrument_type") not in ("FUT", "CE", "PE"):
            continue
        names = SEGMENTS.get(record.get("segment"))
        if not names:
            continue
        key = _instrument(record, names)
        try:
            day = datetime.fromtimestamp(int(record["expiry"]) / 1000, expiry.IST).date()
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if not key or not (today <= day <= last):
            continue
        kind = _kind(record["segment"], record)
        mark = (key, kind, day)
        if mark not in seen:
            seen.add(mark)
            rows.append({"i": key, "t": kind, "d": day.isoformat(), "listed": True})
    return sorted(rows, key=lambda r: (r["d"], r["i"], r["t"]))


def download(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": "economic-calendar-alerts/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise ValueError("download too large")
    return raw


def fetch_records(fetch=download):
    records = []
    for url in URLS:
        data = json.loads(gzip.decompress(fetch(url)))
        if not isinstance(data, list):
            raise ValueError("unexpected file format")
        records.extend(data)
    return records


def _merge(old_rows, new_rows):
    """New rows replace old ones for every instrument the download covered;
    instruments it did not mention keep what they had."""
    covered = {r["i"] for r in new_rows}
    return new_rows + [r for r in old_rows if r["i"] not in covered]


def _write(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, path)


def sync(now, fetch=download, path=None):
    """One sync. Returns {'rows': n, 'covered': [...], 'missing': [...]}; raises on failure."""
    path = path or SYNCED_FILE
    today = expiry.today_ist(now)
    rows = parse(fetch_records(fetch), today)
    covered = sorted({r["i"] for r in rows})
    wanted = set(MCX_NAMES.values()) | set(NSE_COMMODITY_NAMES.values()) | set(NSE_INDEX_NAMES.values())
    missing = sorted(wanted - set(covered))
    if len(covered) < 3:        # a file that mentions almost nothing is a broken file
        raise ValueError(f"only {len(covered)} of our instruments found in the download")
    old = expiry._read_json(path)
    keep = [r for r in old.get("expiries") or () if r.get("d", "") >= today.isoformat()]
    payload = {"synced_at": now.isoformat(), "attempted_at": now.isoformat(),
               "source": "Upstox public instrument files",
               "expiries": _merge(keep, rows)}
    _write(path, payload)
    expiry._cache.clear()
    return {"rows": len(rows), "covered": covered, "missing": missing}


def _when(text):
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def maybe_sync(now, notifier=None, fetch=download, path=None):
    """Sync if due. Never raises. Tells the owner at most once a day when it fails."""
    if os.environ.get("EXPIRY_SYNC", "on").strip().lower() in ("off", "0", "false", "no"):
        return None
    path = path or SYNCED_FILE
    state = expiry._read_json(path)
    synced, attempted = _when(state.get("synced_at")), _when(state.get("attempted_at"))
    failed = bool(attempted and (not synced or attempted > synced))
    wait = RETRY_AFTER_FAILURE if failed else SYNC_EVERY
    if attempted and now - attempted < wait:
        return None
    try:
        result = sync(now, fetch, path)
    except Exception as e:
        print("EXPIRY SYNC FAILED:", type(e).__name__, str(e)[:120])
        state["attempted_at"] = now.isoformat()
        last_notice = _when(state.get("notified_at"))
        if notifier is not None and (not last_notice or now - last_notice >= NOTICE_EVERY):
            try:
                notifier.send("⚠️ <b>Expiry dates could not refresh</b>\n"
                              "The bot keeps using the last dates it has. It will try again later.")
                state["notified_at"] = now.isoformat()
            except Exception as send_error:
                print("EXPIRY SYNC NOTICE FAILED:", type(send_error).__name__)
        try:
            _write(path, state)
        except OSError:
            pass
        return None
    print(f"Expiry sync: {result['rows']} dates for {len(result['covered'])} instruments"
          + (f" | not found: {', '.join(result['missing'])}" if result["missing"] else ""))
    return result
