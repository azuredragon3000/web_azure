"""
scrape_warnings.py
==================
Step 1: Fetch all USDT-M perpetual futures from fapi exchangeInfo.
Step 2: For each symbol check bapi se=9 (Innovation Zone = warning coin).
Step 3: Save results to warning_coins.json.

No browser / Playwright needed — bapi check is accurate and fast.
Runs on GitHub Actions via .github/workflows/scrape_warnings.yml
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

SESSION = requests.Session()
SESSION.headers.update({
    'Accept': 'application/json',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
})

STABLES = {
    'USDCUSDT', 'BUSDUSDT', 'TUSDUSDT', 'FDUSDUSDT',
    'USDPUSDT', 'DAIUSDT', 'EURUSDT', 'GBPUSDT', 'AEURUSDT',
}

# Innovation Zone warning message (same text for all se=9 coins)
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
    try:
        r = SESSION.get(
            'https://www.binance.com/bapi/asset/v2/public'
            '/asset-service/product/get-product-by-symbol?symbol=' + spot,
            timeout=8
        )
        if r.status_code == 200:
            d = r.json().get('data') or {}
            return {'se': str(d.get('se', '')), 'tags': d.get('tags', [])}
    except Exception:
        pass
    return {'se': '', 'tags': []}


# ── Step 1: all USDT perps ────────────────────────────────────────────────────

def get_all_perps() -> list[dict]:
    print('Fetching fapi exchangeInfo...', flush=True)
    r = SESSION.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=20)
    r.raise_for_status()
    return [
        s for s in r.json()['symbols']
        if s.get('contractType') == 'PERPETUAL'
        and s['symbol'].endswith('USDT')
        and s['symbol'] not in STABLES
    ]


# ── Step 2: bapi check ───────────────────────────────────────────────────────

def build_warn_map(perps: list) -> dict:
    pairs = [
        (s['symbol'], futures_sym_to_spot(s['symbol'], s.get('baseAsset', s['symbol'][:-4])))
        for s in perps
    ]
    print(f'Checking bapi for {len(pairs)} symbols...', flush=True)
    results = {}
    with ThreadPoolExecutor(max_workers=20) as pool:
        fmap = {pool.submit(get_bapi_info, spot): (fsym, spot) for fsym, spot in pairs}
        done = 0
        for f in as_completed(fmap):
            fsym, spot = fmap[f]
            info = f.result()
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
    print(f'bapi done — {warn_count} Innovation Zone coins found.', flush=True)
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
        'coins':   warn_coins,
    }
    with open('warning_coins.json', 'w') as f:
        json.dump(output, f, indent=2)
    print(f'\nSaved {len(warn_coins)} warning coins to warning_coins.json', flush=True)
    # Print summary
    print(f'\n{"Symbol":<18} {"Tags"}')
    print('-' * 60)
    for sym in sorted(warn_coins):
        tags = ', '.join(warn_coins[sym]['tags']) or '-'
        print(f'{sym:<18} {tags}')
    if output.get('coins'):
        sample_msg = next((v['msg'] for v in warn_coins.values() if v['msg']), None)
        if sample_msg:
            print(f'\nWarning message:\n  "{sample_msg[:120]}..."')


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f'\nBinance Warning Scraper  —  {datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")}')
    perps    = get_all_perps()
    warn_map = build_warn_map(perps)
    save_results(warn_map)


if __name__ == '__main__':
    main()
