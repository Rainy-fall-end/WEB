import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from login_tk55tk import DEFAULT_CONFIG, DEFAULT_URL, OUTPUT_DIR, load_config


DEFAULT_SEARCH_KEYWORD = "\u9752\u94dc"
DEFAULT_LIMIT = 3
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_SLOW_MO = 100
DEFAULT_HEADLESS = False
DEFAULT_FETCH_PRICES = True
TONGBAO_PER_RMB = 10

RESULT_SELECTORS = [
    'a[href*="forum.php?mod=viewthread"]',
    'a[href*="thread-"]',
]


def log(message):
    print(message, file=sys.stderr)


async def save_debug_files(page, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=output_dir / "search_latest.png", full_page=True)
    (output_dir / "search_latest.html").write_text(await page.content(), encoding="utf-8")


async def open_home(context, url, timeout_ms):
    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1_000)
    return page


async def perform_search(context, page, keyword):
    search_box = page.locator("#scbar_txt").first
    search_button = page.locator("#scbar_btn").first

    await search_box.wait_for(state="visible")
    await search_box.fill(keyword)

    try:
        async with context.expect_page(timeout=5_000) as popup_info:
            await search_button.click()
        result_page = await popup_info.value
    except PlaywrightTimeoutError:
        result_page = page
        await search_button.click()

    await result_page.wait_for_load_state("domcontentloaded")
    await result_page.wait_for_timeout(1_000)
    return result_page


async def extract_results(page, limit):
    selector = ", ".join(RESULT_SELECTORS)
    return await page.evaluate(
        """
        ({ selector, limit }) => {
          const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
          const seen = new Set();
          const results = [];

          for (const link of document.querySelectorAll(selector)) {
            const title = normalize(link.textContent);
            if (!title) continue;

            const href = link.getAttribute("href");
            if (!href) continue;

            const url = new URL(href, document.baseURI).href;
            if (seen.has(url)) continue;
            seen.add(url);

            const item = link.closest("li, tbody, tr, .pbw, .tl, .xld") || link.parentElement;
            const snippet = normalize(item ? item.textContent : "");
            const timeNode = item ? item.querySelector("span, em, cite") : null;

            results.push({
              title,
              url,
              snippet,
              meta: normalize(timeNode ? timeNode.textContent : "")
            });

            if (results.length >= limit) break;
          }

          return results;
        }
        """,
        {"selector": selector, "limit": limit},
    )


async def extract_message(page):
    return await page.evaluate(
        """
        () => {
          const selectors = ["#messagetext", ".emp", ".alert_info", ".notice", ".mtm"];
          for (const selector of selectors) {
            const node = document.querySelector(selector);
            const text = node && node.textContent ? node.textContent.replace(/\\s+/g, " ").trim() : "";
            if (text) return text;
          }
          return "";
        }
        """
    )


def result_filename(index, url):
    tid_match = re.search(r"(?:tid=|thread-)(\d+)", url)
    tid = tid_match.group(1) if tid_match else str(index + 1)
    return f"result_{index + 1}_{tid}.html"


async def extract_price(page):
    return await page.evaluate(
        """
        ({ tongbaoPerRmb }) => {
          const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
          const bodyText = normalize(document.body ? document.body.innerText : "");
          const candidates = [];
          const patterns = [
            /(?:售价|价格|价钱|需支付|需要支付|购买需|购买需要|支付|花费|付费)[^0-9]{0,30}(\\d+(?:\\.\\d+)?)\\s*(?:个)?(?:东周列国)?通宝/g,
            /(\\d+(?:\\.\\d+)?)\\s*(?:个)?(?:东周列国)?通宝\\s*(?:\\/|／|一)?\\s*(?:部|份|个|帖|主题)?/g
          ];

          for (const pattern of patterns) {
            for (const match of bodyText.matchAll(pattern)) {
              const value = Number(match[1]);
              if (!Number.isFinite(value)) continue;

              const start = Math.max(0, match.index - 35);
              const end = Math.min(bodyText.length, match.index + match[0].length + 35);
              const context = bodyText.slice(start, end);
              const hasPriceHint = /(售价|价格|价钱|购买|支付|花费|付费|出售|卖|通宝\\s*(?:\\/|／|一)?\\s*(?:部|份|个|帖|主题)?)/.test(context);
              const looksLikeSiteCreditDef = /creditnotice|贡献|东周列国通宝/.test(context) && value <= 10;
              if (!hasPriceHint || looksLikeSiteCreditDef) continue;

              candidates.push({
                tongbao: value,
                rmb: Number((value / tongbaoPerRmb).toFixed(2)),
                source_text: context
              });
            }
          }

          if (!candidates.length) {
            return {
              tongbao: null,
              rmb: null,
              source_text: null
            };
          }

          candidates.sort((a, b) => {
            const aStrong = /(售价|价格|购买|支付|花费|付费)/.test(a.source_text) ? 0 : 1;
            const bStrong = /(售价|价格|购买|支付|花费|付费)/.test(b.source_text) ? 0 : 1;
            return aStrong - bStrong;
          });

          return candidates[0];
        }
        """,
        {"tongbaoPerRmb": TONGBAO_PER_RMB},
    )


async def enrich_results_with_prices(context, results, output_dir, timeout_ms):
    detail_dir = output_dir / "details"
    detail_dir.mkdir(parents=True, exist_ok=True)

    for index, result in enumerate(results):
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            await page.goto(result["url"], wait_until="domcontentloaded")
            await page.wait_for_timeout(1_000)
            result["price"] = await extract_price(page)
            html_path = detail_dir / result_filename(index, result["url"])
            html_path.write_text(await page.content(), encoding="utf-8")
        except Exception as exc:
            result["price"] = {
                "tongbao": None,
                "rmb": None,
                "source_text": None,
                "error": str(exc),
            }
        finally:
            await page.close()


async def run(args):
    payload = await search_tk55tk(
        keyword=args.keyword,
        config_path=args.config,
        url=args.url,
        output_dir=args.output_dir,
        limit=args.limit,
        timeout_ms=args.timeout_ms,
        slow_mo=args.slow_mo,
        headless=args.headless,
        fetch_prices=args.fetch_prices,
        allow_config_price_override=not args.no_config_price_override,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def search_tk55tk(
    keyword=None,
    config_path=DEFAULT_CONFIG,
    url=None,
    output_dir=None,
    limit=None,
    timeout_ms=DEFAULT_TIMEOUT_MS,
    slow_mo=DEFAULT_SLOW_MO,
    headless=DEFAULT_HEADLESS,
    fetch_prices=DEFAULT_FETCH_PRICES,
    allow_config_price_override=True,
):
    config = load_config(config_path)
    keyword = keyword or config.get("search_keyword") or DEFAULT_SEARCH_KEYWORD
    if not keyword:
        raise SystemExit("Missing keyword. Usage: python search_tk55tk.py <keyword>")

    url = url or config.get("url") or DEFAULT_URL
    output_dir = Path(output_dir or config.get("output_dir") or OUTPUT_DIR)
    limit = limit or config.get("search_limit") or DEFAULT_LIMIT
    if "fetch_prices" in config and allow_config_price_override:
        fetch_prices = bool(config["fetch_prices"])
    state_path = output_dir / "storage_state.json"
    if not state_path.exists():
        raise SystemExit(f"Missing login state: {state_path}. Run login_tk55tk.py first.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = await browser.new_context(storage_state=state_path)
        try:
            home_page = await open_home(context, url, timeout_ms)
            result_page = await perform_search(context, home_page, keyword)
            results = await extract_results(result_page, limit)
            if fetch_prices and results:
                await enrich_results_with_prices(context, results, output_dir, timeout_ms)
            message = "" if results else await extract_message(result_page)
            await save_debug_files(result_page, output_dir)

            payload = {
                "keyword": keyword,
                "count": len(results),
                "exchange_rate": {
                    "rmb": 1,
                    "tongbao": TONGBAO_PER_RMB,
                },
                "results": results,
            }
            if message:
                payload["message"] = message

            return payload
        finally:
            await context.close()
            await browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Search tk55tk.com and return JSON results.")
    parser.add_argument("keyword", nargs="?", help="Search keyword. Overrides config search_keyword.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to JSON config file.")
    parser.add_argument("--url", help="Override URL from config.")
    parser.add_argument("--output-dir", help="Override output directory from config.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--slow-mo", type=int, default=DEFAULT_SLOW_MO)
    parser.add_argument(
        "--fetch-prices",
        action="store_true",
        default=DEFAULT_FETCH_PRICES,
        help="Open each result and extract Tongbao/RMB price.",
    )
    parser.add_argument(
        "--no-fetch-prices",
        action="store_false",
        dest="fetch_prices",
        help="Only return search results without visiting detail pages.",
    )
    parser.add_argument(
        "--no-config-price-override",
        action="store_true",
        help="Ignore fetch_prices from config and use CLI/default instead.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=DEFAULT_HEADLESS,
        help="Run without showing the browser.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
