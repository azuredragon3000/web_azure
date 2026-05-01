"""
scrape_warnings.py
==================
Step 1: Fetch all USDT-M perpetual futures from fapi exchangeInfo.
Step 2: For each symbol check bapi se=9 (Innovation Zone = warning coin).
Step 3: Save results to warning_coins.json.

No browser / Playwright needed - bapi check is accurate and fast.
Runs on GitHub Actions via .github/workflows/scrape_warnings.yml
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

SESSION = requests.Session()
SESSION.headers.update({
    'Accept': 'application/json',
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


# ── Helpers ──────────────────────────────────────────────────────────────────

def futures_sym_to_spot(sym: str, base: str) -> str:
    stripped = base.lstrip('0123456789')
    return (stripped or base) + 'USDT'


def get_bapi_info(spot: str) -> dict:
    """Check bapi for se field. Retries once on failure."""
    for attempt in range(2):
        try:
            r = SESSION.get(
                'https://www.binance.com/bapi/asset/v2/public'
                '/asset-service/product/get-product-by-symbol?symbol=' + spot,
                timeout=10
            )
            if r.status_code == 200:
                d = r.json().get('data') or {}
                return {'se': str(d.get('se', '')), 'tags': d.get('tags', [])}
            elif r.status_code == 451:
                # Binance geo-blocks this IP (common on GitHub Actions)
                print(f'  [bapi] HTTP 451 geo-block on {spot} - Binance blocking this IP region', flush=True)
                return {'se': 'blocked', 'tags': []}
        except Exception as e:
            if attempt == 1:
                print(f'  [bapi] failed {spot}: {e}', flush=True)
            time.sleep(0.5)
    return {'se': '', 'tags': []}


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


# ── Step 2: bapi check ───────────────────────────────────────────────────────

def build_warn_map(perps: list) -> dict:
    if not perps:
        return {}
    pairs = [
        (s['symbol'], futures_sym_to_spot(s['symbol'], s.get('baseAsset', s['symbol'][:-4])))
        for s in perps
    ]
    print(f'Checking bapi for {len(pairs)} symbols (20 concurrent)...', flush=True)
    results = {}
    blocked_count = 0
    with ThreadPoolExecutor(max_workers=20) as pool:
        fmap = {pool.submit(get_bapi_info, spot): (fsym, spot) for fsym, spot in pairs}
        done = 0
        for f in as_completed(fmap):
            fsym, spot = fmap[f]
            info = f.result()
            if info['se'] == 'blocked':
                blocked_count += 1
            is_warn = info['se'] == '9'
            results[fsym] = {
                'se':   info['se'],
                'tags': info['tags'],
                'warn': is_warn,
                'spot': spot,
                'msg':  WARNING_MSG if is_warn else None,
            }
            done += 1
            if done % 100 == 0:
                print(f'  ...{done}/{len(pairs)}', flush=True)
    warn_count = sum(1 for v in results.values() if v['warn'])
    if blocked_count > 10:
        print(f'WARNING: {blocked_count} symbols were geo-blocked by Binance bapi.', flush=True)
        print('  Consider using a proxy or alternative data source.', flush=True)
    print(f'bapi done - {warn_count} Innovation Zone coins found.', flush=True)
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
            print('ERROR: could not fetch any perpetual symbols (network blocked?)', flush=True)
            # Keep existing warning_coins.json unchanged, exit 0 so workflow doesn't fail
            sys.exit(0)
        warn_map = build_warn_map(perps)
        save_results(warn_map)
        print('Done.', flush=True)
    except Exception as e:
        print(f'FATAL: {e}', flush=True)
        import traceback
        traceback.print_exc()
        # Exit 0 so the workflow doesn't fail and leave warning_coins.json broken
        sys.exit(0)


if __name__ == '__main__':
    main()
