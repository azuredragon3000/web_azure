"""
Chạy 1 lần để tạo warnings.txt với toàn bộ symbols từ Binance.
Sau đó bạn tự edit file warnings.txt để thêm warning cho từng coin.

Usage:
    python generate_warnings_list.py
"""
import requests

r = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=20)
symbols = sorted([
    s['symbol'] for s in r.json()['symbols']
    if s.get('contractType') == 'PERPETUAL' and s['symbol'].endswith('USDT')
])

lines = [
    '# Binance Futures Warning List',
    '# Format: SYMBOL|warning text  (leave blank after | for no warning)',
    '# Example: BRUSDT|⚠ Early stage project, high volatility risk',
    '#',
    '',
]
for sym in symbols:
    lines.append(sym + '|')

with open('warnings.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print(f'Created warnings.txt with {len(symbols)} symbols.')
print('Now open warnings.txt and add warnings after | for each coin.')
