"""
scrape_warnings.py
==================
Step 1: Fetch all USDT-M perpetual futures symbols from fapi.
Step 2 (fast): Check se=9 from bapi to detect Innovation Zone coins.
Step 3 (slow): For a sample of se=9 coins, use Playwright to intercept 
               the actual Binance futures page and capture the real warning
               message text from the bapi response.
Step 4: Save results to warning_coins.json (read by web app or GitHub Pages).

Run locally:
    pip install requests playwright
    python -m playwright install chromium
    python scrape_warnings.py

Runs automatically via .github/workflows/scrape_warnings.yml
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

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


# ── Step 2: bapi fast-check ───────────────────────────────────────────────────

def build_warn_map(perps: list[dict]) -> dict[str, dict]:
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
            results[fsym] = {
                'se':   info['se'],
                'tags': info['tags'],
                'warn': info['se'] == '9',
                'spot': spot,
                'msg':  None,   # filled in Step 3
            }
            done += 1
            if done % 100 == 0:
                print(f'  ...{done}/{len(pairs)}', flush=True)
    warn_count = sum(1 for v in results.values() if v['warn'])
    print(f'bapi done — {warn_count} Innovation Zone coins found.', flush=True)
    return results


# ── Step 3: Playwright — intercept real warning API message ──────────────────

def intercept_warning_message(symbol: str) -> str | None:
    """
    Load binance.com/en/futures/<symbol> in headless Chromium,
    intercept XHR/fetch responses that contain risk/warning text,
    return the message string or None.
    """
    try:
        from playwright.sync_api import sync_playwright
        captured = {}

        def on_response(response):
            if captured.get('msg'):
                return
            url = response.url
            # Only look at bapi JSON responses
            if 'bapi' not in url and 'risk' not in url.lower() and 'notice' not in url.lower():
                return
            try:
                body = response.text()
            except Exception:
                return
            # Look for known warning phrase keywords
            if 'early-stage' in body or 'high volatility' in body or 'extreme price' in body:
                try:
                    data = json.loads(body)
                    # Walk the JSON tree for string values containing the warning
                    def find_msg(obj):
                        if isinstance(obj, str):
                            if 'early-stage' in obj or 'extreme price' in obj or 'high volatility' in obj:
                                return obj
                        elif isinstance(obj, dict):
                            for v in obj.values():
                                r = find_msg(v)
                                if r: return r
                        elif isinstance(obj, list):
                            for item in obj:
                                r = find_msg(item)
                                if r: return r
                        return None
                    msg = find_msg(data)
                    if msg:
                        captured['msg'] = msg
                        captured['url'] = url
                except Exception:
                    pass

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()
            page.on('response', on_response)
            try:
                page.goto(
                    f'https://www.binance.com/en/futures/{symbol}',
                    wait_until='networkidle', timeout=30000
                )
            except Exception:
                pass
            # Also try clicking through any cookie banner
            try:
                page.wait_for_timeout(3000)
            except Exception:
                pass
            browser.close()

        if captured.get('msg'):
            print(f'  [{symbol}] intercepted warning from: {captured.get("url","?")}', flush=True)
            return captured['msg']
    except Exception as e:
        print(f'  [{symbol}] Playwright error: {e}', flush=True)
    return None


def enrich_with_messages(warn_map: dict, sample: int = 3) -> str | None:
    """
    Run Playwright on up to `sample` warning coins to discover the real
    warning message text. Once found, apply to all warning coins.
    Returns the message string, or None if not found.
    """
    warn_syms = [sym for sym, v in warn_map.items() if v['warn']][:sample]
    if not warn_syms:
        return None
    print(f'Playwright: intercepting warning messages for {warn_syms}...', flush=True)
    for sym in warn_syms:
        msg = intercept_warning_message(sym)
        if msg:
            # Apply to all warning coins
            for v in warn_map.values():
                if v['warn']:
                    v['msg'] = msg
            return msg
    return None


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

    # Try to intercept real warning message via Playwright (best effort)
    try:
        enrich_with_messages(warn_map, sample=3)
    except Exception as e:
        print(f'Playwright step skipped: {e}', flush=True)

    save_results(warn_map)


if __name__ == '__main__':
    main()
