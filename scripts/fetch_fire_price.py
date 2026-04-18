"""
交易猫火价爬取（GitHub Actions 用）
Playwright 无头浏览器拦截 API 响应，提取火炬之光游戏币实时价格。
"""
import asyncio
import json
import re
import sys
import os
from datetime import datetime


async def fetch_fire_price():
    from playwright.async_api import async_playwright

    target_url = 'https://www.jiaoyimao.com/jg2000492/f5055087-c5055088/o110/?enforcePlat=2&newPage=true'
    result = {'listings': [], 'raw_responses': []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
        )
        page = await context.new_page()

        # 拦截所有 JSON 响应
        async def on_response(response):
            url = response.url
            ct = response.headers.get('content-type', '')
            if response.status == 200 and ('json' in ct or 'mtop' in url.lower() or 'goodslist' in url.lower() or 'goods' in url.lower()):
                try:
                    body = await response.text()
                    if len(body) > 200 and ('deliverComps' in body or 'unitPrices' in body or 'price' in body):
                        result['raw_responses'].append(body)
                        print(f'[CAPTURED] {url[:100]} ({len(body)} bytes)')
                except Exception:
                    pass

        page.on('response', on_response)

        try:
            print(f'Navigating to {target_url}')
            await page.goto(target_url, wait_until='domcontentloaded', timeout=30000)
            # 等待更长时间让 JS 加载完
            await asyncio.sleep(8)
            # 尝试滚动触发懒加载
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await asyncio.sleep(3)
            # 截图调试
            await page.screenshot(path='/tmp/jym_debug.png')
            print(f'Page title: {await page.title()}')
            print(f'Captured {len(result["raw_responses"])} API responses')
        except Exception as e:
            print(f'Page load error: {e}', file=sys.stderr)

        # 如果没拦截到 API，尝试直接从页面 HTML 提取
        if not result['raw_responses']:
            try:
                html = await page.content()
                # 交易猫的商品数据可能内嵌在 script 标签里
                import re as _re
                for m in _re.finditer(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html, _re.DOTALL):
                    result['raw_responses'].append(m.group(1))
                    print(f'[HTML EXTRACT] Found __INITIAL_STATE__ ({len(m.group(1))} bytes)')
                # 也尝试从 DOM 直接提取价格
                prices = await page.evaluate('''() => {
                    const items = document.querySelectorAll('[class*="price"], [class*="goods"], [class*="item"]');
                    return Array.from(items).slice(0, 20).map(el => el.textContent.trim()).filter(t => t.includes('游戏币') || t.includes('元'));
                }''')
                if prices:
                    print(f'[DOM] Found {len(prices)} price elements')
                    for p in prices[:5]:
                        print(f'  {p[:100]}')
            except Exception as e:
                print(f'HTML extract error: {e}', file=sys.stderr)

        await browser.close()

    # 解析响应
    listings = []
    for raw in result['raw_responses']:
        try:
            data = json.loads(raw)
            comps = (data.get('data', {}).get('result', {}).get('deliverComps', [])
                     or data.get('data', {}).get('result', {}).get('goods', []))
            for comp in comps:
                item = comp.get('data', comp)
                title = item.get('title', '')
                price_rmb = float(item.get('price', 0))
                server = item.get('serverName', '')

                # 从标题提取游戏币数量
                m = re.search(r'([\d,]+)\s*游戏币', title)
                coin_amount = int(m.group(1).replace(',', '')) if m else 0

                if coin_amount > 0 and price_rmb > 0:
                    rate = round(coin_amount / price_rmb, 2)
                    listings.append({
                        'title': title,
                        'price_rmb': price_rmb,
                        'coin_amount': coin_amount,
                        'rate': rate,  # 火/元
                        'server': server,
                    })
        except Exception:
            continue

    if not listings:
        print('No listings found', file=sys.stderr)
        return None

    # 过滤异常数据（汇率 > 100 火/元 的是刷单）
    valid = [x for x in listings if x['rate'] < 100]
    if not valid:
        valid = listings

    # 计算主流火价：取中位数汇率
    rates = sorted([x['rate'] for x in valid])
    median_rate = rates[len(rates) // 2]

    # 火价 = 1火值多少人民币
    fire_price_rmb = round(1.0 / median_rate, 4) if median_rate > 0 else 0

    output = {
        'ok': True,
        'fire_price_rmb': fire_price_rmb,
        'median_rate': median_rate,  # 火/元
        'listings_count': len(valid),
        'sample_listings': valid[:5],
        'fetched_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'jiaoyimao',
    }

    return output


async def main():
    result = await fetch_fire_price()
    if result:
        out_path = os.path.join(os.path.dirname(__file__), '..', 'fire_price_rmb.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'OK: 1火={result["fire_price_rmb"]}元, 1元={result["median_rate"]}火, {result["listings_count"]}条有效挂单')
    else:
        print('FAILED: no data', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
