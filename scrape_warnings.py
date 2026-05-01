"""
scrape_warnings.py
==================
Fetch ALL Binance products in ONE request via get-all-product.
Filter USDT pairs where se=9 (Innovation Zone = warning coin).
Upload result to warning_coins.json via GitHub API.

No fapi.binance.com needed — avoids US geo-block entirely.
Runs on GitHub Actions via .github/workflows/scrape_warnings.yml
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

import cloudscraper

SESSION = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'linux', 'mobile': False}
)

# Use Tor/SOCKS proxy if available (set via HTTP_PROXY env in GitHub Actions)
_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
if _proxy:
    SESSION.proxies.update({'http': _proxy, 'https': _proxy})
    print(f'Using proxy: {_proxy}', flush=True)

STABLES = {
    'USDCUSDT', 'BUSDUSDT', 'TUSDUSDT', 'FDUSDUSDT',
    'USDPUSDT', 'DAIUSDT', 'EURUSDT', 'GBPUSDT', 'AEURUSDT',
}

WARNING_MSG = (
    'The underlying asset is an early-stage crypto project. '
    'Relatively extreme price fluctuations may occur due to limited liquidity, '
    'market dynamics and tokenomics. Conduct your own research, evaluate risk '
    'and exercise caution.'
)

BULK_URLS = [
    'https://www.binance.com/exchange-api/v1/public/asset-service/product/get-all-product',
    'https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-all-product',
]

# Free relay services — request goes through their servers (not GitHub/Azure IP)
# Used as fallback when direct access is blocked
RELAY_PREFIXES = [
    'https://api.allorigins.win/raw?url=',
    'https://corsproxy.io/?url=',
    'https://api.codetabs.com/v1/proxy?quest=',
]


def fetch_all_products() -> list:
    """Fetch all Binance products. Tries direct first, then relay services."""
    import urllib.parse

    targets = [
        # Direct
        ('direct', BULK_URLS[0]),
        ('direct', BULK_URLS[1]),
    ]
    # Add relay combinations as fallback
    for relay in RELAY_PREFIXES:
        for base in BULK_URLS:
            targets.append(('relay:' + relay.split('/')[2], relay + urllib.parse.quote(base, safe='')))

    for label, url in targets:
        try:
            r = SESSION.get(url, timeout=30)
            print(f'  [{label}] HTTP {r.status_code} ({len(r.content):,} bytes)', flush=True)
            if r.status_code != 200:
                print(f'    Body: {r.text[:120]}', flush=True)
                continue
            data = r.json()
            # allorigins wraps response in {"contents": "..."}
            if 'contents' in data:
                data = json.loads(data['contents'])
            products = data.get('data') or []
            if products:
                print(f'  Got {len(products):,} products via [{label}].', flush=True)
                return products
            print(f'  Empty data.', flush=True)
        except Exception as e:
            print(f'  [{label}] ERROR: {e}', flush=True)
    return []


def build_warning_coins(products: list) -> dict:
    """Filter products: USDT perp symbols with se=9."""
    results = {}
    for p in products:
        sym  = p.get('s', '')        # e.g. "BRUSDT"
        se   = str(p.get('se', ''))  # "9" = Innovation Zone
        tags = p.get('tags') or []
        cs   = p.get('cs', '')       # contract status / trading status

        if not sym.endswith('USDT'):
            continue
        if sym in STABLES:
            continue
        if se != '9':
            continue

        results[sym] = {
            'tags': tags,
            'msg':  WARNING_MSG,
        }

    return results


def save_json(coins: dict) -> dict:
    output = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count':   len(coins),
        'symbols': sorted(coins.keys()),
        'coins':   coins,
    }
    with open('warning_coins.json', 'w') as f:
        json.dump(output, f, indent=2)
    return output


def upload_to_github():
    """Upload warning_coins.json via GitHub REST API — no git push needed."""
    token = os.environ.get('GITHUB_TOKEN')
    repo  = os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:
        print('Not in GitHub Actions — skipping upload.', flush=True)
        return

    with open('warning_coins.json', 'rb') as f:
        content_b64 = base64.b64encode(f.read()).decode()

    api_url = f'https://api.github.com/repos/{repo}/contents/warning_coins.json'
    hdrs = {
        'Authorization': f'token {token}',
        'Accept':        'application/vnd.github.v3+json',
        'Content-Type':  'application/json',
    }

    sha = None
    try:
        res = json.loads(urllib.request.urlopen(
            urllib.request.Request(api_url, headers=hdrs)
        ).read())
        sha = res.get('sha')
        print(f'  Existing sha: {sha[:7]}', flush=True)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f'  GET sha failed: HTTP {e.code}', flush=True)

    payload = {
        'message': 'chore: update warning coins list [skip ci]',
        'content': content_b64,
    }
    if sha:
        payload['sha'] = sha

    try:
        res2 = json.loads(urllib.request.urlopen(urllib.request.Request(
            api_url, data=json.dumps(payload).encode(), method='PUT', headers=hdrs,
        )).read())
        print(f'  Uploaded! Commit: {res2["commit"]["sha"][:7]}', flush=True)
    except urllib.error.HTTPError as e:
        print(f'  Upload failed: HTTP {e.code} — {e.read().decode()[:200]}', flush=True)


def main():
    print(f'\nBinance Warning Scraper  -  {datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")}', flush=True)

    print('\nFetching all Binance products...', flush=True)
    products = fetch_all_products()

    if not products:
        print('FAILED: could not fetch products (geo-blocked?)', flush=True)
        # Keep existing file, just update timestamp
        try:
            existing = json.load(open('warning_coins.json'))
        except Exception:
            existing = {'count': 0, 'symbols': [], 'coins': {}}
        existing['updated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        existing['scrape_status'] = 'geo_blocked'
        json.dump(existing, open('warning_coins.json', 'w'), indent=2)
        upload_to_github()
        sys.exit(0)

    coins = build_warning_coins(products)
    output = save_json(coins)

    print(f'\nFound {len(coins)} warning coins (se=9):', flush=True)
    for sym in output['symbols']:
        print(f'  {sym}', flush=True)

    print('\nUploading to GitHub...', flush=True)
    upload_to_github()
    print('Done.', flush=True)


if __name__ == '__main__':
    main()

