"""
scrape_warnings.py
==================
Step 1: Fetch all USDT-M perpetual futures from fapi exchangeInfo.
Step 2: Fetch ALL spot products in ONE request via get-all-product endpoint.
        Build se=9 map from that (avoids 600+ individual bapi calls that get geo-blocked).
Step 3: Save results to warning_coins.json.

Runs on GitHub Actions via .github/workflows/scrape_warnings.yml
"""

import json
import sys
import time
from datetime import datetime, timezone

import requests

SESSION = requests.Session()
SESSION.headers.update({
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Cache-Control': 'no-cache',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
})

STABLES = {
    'USDCUSDT', 'BUSDUSDT', 'TUSDUSDT', 'FDUSDUSDT',
    'USDPUSDT', 'DAIUSDT', 'EURUSDT', 'GBPUSDT', 'AEURUSDT',
}

# Innovation Zone warning message (same for all se=9 coins on Binance)
WARNING_MSG = (
    'The underlying asset is an early-stage crypto project. '
    'Relatively extreme price fluctuations may occur due to limited liquidity, '
    'market dynamics and tokenomics. Conduct your own research, evaluate risk '
    'and exercise caution.'
)

# Try multiple endpoints + subdomains in order
BULK_PRODUCT_URLS = [
    'https://www.binance.com/exchange-api/v1/public/asset-service/product/get-all-product',
    'https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-all-product',
    'https://api4.binance.com/bapi/asset/v2/public/asset-service/product/get-all-product',
    'https://api.binance.com/bapi/asset/v2/public/asset-service/product/get-all-product',
]


def fetch_json(url: str, timeout: int = 30):
    try:
        r = SESSION.get(url, timeout=timeout)
        print(f'  GET {url.split("/")[-1]} -> HTTP {r.status_code} ({len(r.content)} bytes)', flush=True)
        if r.status_code == 200:
            j = r.json()
            return j
        else:
            print(f'    Body preview: {r.text[:200]}', flush=True)
    except Exception as e:
        print(f'  GET {url} -> ERROR: {e}', flush=True)
    return None


# ── Step 1: all USDT perps ────────────────────────────────────────────────────

def get_all_perps() -> list:
    print('Fetching fapi exchangeInfo...', flush=True)
    for attempt in range(3):
        try:
            r = SESSION.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=20)
            r.raise_for_status()
            perps = [
                s for s in r.json()['symbols']
                if s.get('contractType') == 'PERPETUAL'
                and s['symbol'].endswith('USDT')
                and s['symbol'] not in STABLES
            ]
            print(f'Found {len(perps)} USDT perpetuals.', flush=True)
            return perps
        except Exception as e:
            print(f'  fapi attempt {attempt+1}/3 failed: {e}', flush=True)
            time.sleep(3)
    return []


# ── Step 2: bulk product lookup (1 request) ───────────────────────────────────

def get_bulk_se_map():
    """
    Fetch all Binance products in one bulk request.
    Returns dict: spot_symbol -> {'se': str, 'tags': list}
    """
    print('\nFetching bulk product data (1 request)...', flush=True)
    for url in BULK_PRODUCT_URLS:
        print(f'  Trying: {url}', flush=True)
        data = fetch_json(url)
        if not data:
            continue
        products = data.get('data') or []
        if not isinstance(products, list) or len(products) == 0:
            print(f'    Response keys: {list(data.keys())[:10]}', flush=True)
            print(f'    data field type: {type(products)}, len: {len(products) if isinstance(products,list) else "N/A"}', flush=True)
            continue
        se_map = {}
        for p in products:
            sym  = p.get('s', '')       # e.g. "BRUSDT"
            se   = str(p.get('se', '')) # e.g. "9"
            tags = p.get('tags') or []
            if sym:
                se_map[sym] = {'se': se, 'tags': tags}
        count9 = sum(1 for v in se_map.values() if v['se'] == '9')
        print(f'    Loaded {len(se_map)} products, {count9} with se=9.', flush=True)
        return se_map
    print('  All bulk product endpoints failed.', flush=True)
    return {}


# ── Step 3: build warn map ───────────────────────────────────────────────────

def build_warn_map(perps: list, se_map: dict) -> dict:
    if not perps:
        return {}
    results = {}
    for s in perps:
        fsym = s['symbol']                 # e.g. "BRUSDT"
        base = s.get('baseAsset', fsym[:-4])
        # spot symbol: strip leading digits from base then add USDT
        stripped = base.lstrip('0123456789')
        spot = (stripped or base) + 'USDT'

        # Look up in bulk se_map using spot symbol (same as futures for USDT pairs)
        info = se_map.get(spot) or se_map.get(fsym) or {'se': '', 'tags': []}
        is_warn = info['se'] == '9'
        results[fsym] = {
            'se':   info['se'],
            'tags': info['tags'],
            'warn': is_warn,
            'spot': spot,
            'msg':  WARNING_MSG if is_warn else None,
        }

    warn_count = sum(1 for v in results.values() if v['warn'])
    no_data    = sum(1 for v in results.values() if v['se'] == '')
    print(f'Result: {warn_count} Innovation Zone (se=9) coins, {no_data} with no se data.', flush=True)
    return results


# ── Step 4: save JSON ─────────────────────────────────────────────────────────

def save_results(warn_map: dict):
    warn_coins = {
        sym: {
            'tags': info['tags'],
            'spot': info['spot'],
            'msg':  info['msg'],
        }
        for sym, info in warn_map.items()
        if info['warn']
    }
    output = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count':   len(warn_coins),
        'symbols': sorted(warn_coins.keys()),
        'coins':   warn_coins,
    }
    with open('warning_coins.json', 'w') as f:
        json.dump(output, f, indent=2)
    print(f'\nSaved {len(warn_coins)} warning coins to warning_coins.json', flush=True)
    for sym in sorted(warn_coins):
        tags = ', '.join(warn_coins[sym]['tags']) or '-'
        print(f'  {sym:<18} {tags}')


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f'\nBinance Warning Scraper  -  {datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")}', flush=True)
    try:
        perps = get_all_perps()
        if not perps:
            print('ERROR: could not fetch any perpetual symbols.', flush=True)
            _write_empty('fapi_failed')
            sys.exit(0)

        se_map = get_bulk_se_map()
        if not se_map:
            print('All Binance web endpoints are blocked from this GitHub Actions IP.', flush=True)
            print('Keeping existing warning_coins.json (will retry next scheduled run).', flush=True)
            # Touch the file with a new timestamp so git sees a change and can commit
            _write_empty('geo_blocked')
            sys.exit(0)

        warn_map = build_warn_map(perps, se_map)
        save_results(warn_map)
        print('Done.', flush=True)
    except Exception as e:
        print(f'FATAL: {e}', flush=True)
        import traceback
        traceback.print_exc()
        _write_empty('exception')
        sys.exit(0)


def _write_empty(reason: str):
    """Write warning_coins.json with updated timestamp so git always has something to commit."""
    try:
        existing = {}
        try:
            with open('warning_coins.json') as f:
                existing = json.load(f)
        except Exception:
            pass
        existing['updated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        existing['scrape_status'] = reason
        with open('warning_coins.json', 'w') as f:
            json.dump(existing, f, indent=2)
        print(f'Updated warning_coins.json timestamp (reason: {reason}).', flush=True)
    except Exception as e:
        print(f'Could not write warning_coins.json: {e}', flush=True)


if __name__ == '__main__':
    main()
