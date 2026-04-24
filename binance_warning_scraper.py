"""
Binance Futures Warning Scraper
================================
Fetches all USDT-M perpetual futures and identifies coins that Binance marks
with a risk warning in the app:
  "The underlying asset is an early-stage crypto project..."
  or "The symbol is subject to high volatility..."

Detection method: Binance bapi `se` field
  se=9   → Innovation Zone = warning shown in app
  se=521 → Main market     = no warning
  (other values / not found in spot bapi → treated as no warning)

usage:
    pip install requests
    python binance_warning_scraper.py
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

SESSION = requests.Session()
SESSION.headers.update({
    'Accept': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
})

STABLES = {
    'USDCUSDT', 'BUSDUSDT', 'TUSDUSDT', 'FDUSDUSDT',
    'USDPUSDT', 'DAIUSDT', 'EURUSDT', 'GBPUSDT', 'AEURUSDT',
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def futures_symbol_to_spot(futures_sym: str, base_asset: str) -> str:
    """
    Map a futures symbol/baseAsset to its spot bapi equivalent.
    e.g. '1000PEPE' -> 'PEPEUSDT', 'BTC' -> 'BTCUSDT'
    """
    # Strip leading digits (e.g. "1000" from "1000PEPE")
    stripped = base_asset.lstrip('0123456789')
    if stripped:
        return stripped + 'USDT'
    return futures_sym  # fallback


def get_bapi_info(spot_symbol: str) -> dict:
    """Return {'se': str, 'tags': list} from Binance bapi, or defaults on failure."""
    try:
        url = ('https://www.binance.com/bapi/asset/v2/public'
               '/asset-service/product/get-product-by-symbol?symbol=' + spot_symbol)
        r = SESSION.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json().get('data')
            if data:
                return {'se': str(data.get('se', '')), 'tags': data.get('tags', [])}
    except Exception:
        pass
    return {'se': '', 'tags': []}


# ── Step 1: fetch all USDT perpetual futures ─────────────────────────────────

def get_all_perps() -> list[dict]:
    print('Fetching futures exchange info...', end=' ', flush=True)
    r = SESSION.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=15)
    r.raise_for_status()
    symbols = [
        s for s in r.json()['symbols']
        if s.get('contractType') == 'PERPETUAL'
        and s['symbol'].endswith('USDT')
        and s['symbol'] not in STABLES
    ]
    print(f'{len(symbols)} symbols found.')
    return symbols


# ── Step 2: fetch bapi warning tags for all symbols concurrently ─────────────

def build_warning_set(perps: list[dict]) -> dict[str, dict]:
    """
    Returns {futures_symbol: {warn_early: bool, warn_volatile: bool, tags: [...]}}
    """
    # Build (futures_sym, spot_sym) pairs
    pairs = []
    for s in perps:
        spot = futures_symbol_to_spot(s['symbol'], s.get('baseAsset', s['symbol'][:-4]))
        pairs.append((s['symbol'], spot))

    print(f'Fetching bapi data for {len(pairs)} symbols (concurrent)...', flush=True)

    results = {}
    with ThreadPoolExecutor(max_workers=20) as pool:
        future_map = {
            pool.submit(get_bapi_info, spot): (fut_sym, spot)
            for fut_sym, spot in pairs
        }
        done = 0
        for future in as_completed(future_map):
            fut_sym, spot = future_map[future]
            info = future.result()
            # se=9 = Innovation Zone = has warning in Binance app
            has_warn = info['se'] == '9'
            results[fut_sym] = {
                'warn':      has_warn,
                'se':        info['se'],
                'tags':      info['tags'],
                'spot_symbol': spot,
            }
            done += 1
            if done % 50 == 0:
                print(f'  ...{done}/{len(pairs)}', flush=True)

    print(f'Done. {sum(1 for v in results.values() if v["warn"])} symbols with Binance warning.')
    return results


# ── Step 3: top gainers / losers ─────────────────────────────────────────────

def get_top_n(n: int = 20) -> tuple[list, list]:
    print('Fetching futures 24h ticker...', end=' ', flush=True)
    r = SESSION.get('https://fapi.binance.com/fapi/v1/ticker/24hr', timeout=15)
    r.raise_for_status()
    tickers = [
        t for t in r.json()
        if t['symbol'].endswith('USDT') and t['symbol'] not in STABLES
    ]
    by_pct   = sorted(tickers, key=lambda t: float(t['priceChangePercent']), reverse=True)
    gainers  = by_pct[:n]
    losers   = sorted(tickers, key=lambda t: float(t['priceChangePercent']))[:n]
    print(f'{len(tickers)} tickers. Top gainer: {gainers[0]["symbol"]} +{float(gainers[0]["priceChangePercent"]):.2f}%')
    return gainers, losers


# ── Step 4: print annotated table ────────────────────────────────────────────

def fmt_price(p: float) -> str:
    if p >= 1000: return f'${p:,.2f}'
    if p >= 1:    return f'${p:.4f}'
    return f'${p:.6f}'

def fmt_pct(p: float) -> str:
    return f'{p:+.2f}%'

COL = {
    'rank':    4,
    'symbol':  14,
    'price':   14,
    'chg':     9,
    'volume':  11,
    'warn':    8,
}

def row_str(rank: int, t: dict, info: dict) -> str:
    sym    = t['symbol'].replace('USDT', '')
    price  = fmt_price(float(t['lastPrice']))
    pct    = fmt_pct(float(t['priceChangePercent']))
    vol    = f"${float(t['quoteVolume'])/1e6:.1f}M"
    w      = '⚠ Yes  <<<' if info.get('warn') else '-'
    return (
        str(rank).rjust(COL['rank']) + '  ' +
        sym.ljust(COL['symbol']) +
        price.ljust(COL['price']) +
        pct.ljust(COL['chg']) +
        vol.ljust(COL['volume']) +
        w.ljust(COL['warn'])
    )

HEADER = (
    '#'.rjust(COL['rank']) + '  ' +
    'Symbol'.ljust(COL['symbol']) +
    'Price'.ljust(COL['price']) +
    '24h %'.ljust(COL['chg']) +
    'Volume'.ljust(COL['volume']) +
    'Warning'.ljust(COL['warn'])
)

def print_table(title: str, tickers: list[dict], warnings: dict):
    print(f'\n{"="*65}')
    print(f'  {title}')
    print(f'  Warning = Binance Innovation Zone (se=9 in bapi)')
    print(f'{"="*65}')
    print(HEADER)
    print('-' * len(HEADER))
    for i, t in enumerate(tickers, 1):
        info = warnings.get(t['symbol'], {})
        print(row_str(i, t, info))


# ── Step 5: summary of all warning coins ─────────────────────────────────────

def print_warning_summary(warnings: dict):
    warn_coins = {
        sym: info for sym, info in warnings.items()
        if info['warn']
    }
    print(f'\n{"="*70}')
    print(f'  ALL FUTURES USDT COINS WITH BINANCE WARNING  ({len(warn_coins)} total)')
    print(f'{"="*70}')
    print(f'{"Symbol":<18} {"se":<6} {"Tags"}')
    print('-' * 70)
    for sym, info in sorted(warn_coins.items()):
        tags = ', '.join(info['tags']) if info['tags'] else '-'
        print(f'{sym:<18} {info["se"]:<6} {tags}')


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    from datetime import datetime
    print(f'\nBinance Warning Scraper  —  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

    perps    = get_all_perps()
    warnings = build_warning_set(perps)
    gainers, losers = get_top_n(20)

    print_table('TOP 20 GAINERS — with warning flags', gainers, warnings)
    print_table('TOP 20 LOSERS  — with warning flags', losers,  warnings)
    print_warning_summary(warnings)


if __name__ == '__main__':
    main()
