import argparse
import asyncio
import html
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from login_tk55tk import DEFAULT_CONFIG, DEFAULT_URL, OUTPUT_DIR, load_config
from tk55tk_runtime import browser_launch_kwargs, linux_browser_help, setup_logging


DEFAULT_SEARCH_KEYWORD = "\u9752\u94dc"
DEFAULT_LIMIT = 10
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_SLOW_MO = 100
DEFAULT_HEADLESS = True
DEFAULT_FETCH_PRICES = True
DEFAULT_DETAIL_CONCURRENCY = 3
DEFAULT_FAST_HTTP = True
DEFAULT_PREVIEW_IMAGE_LIMIT = 3
TONGBAO_PER_RMB = 100

LOGGER = logging.getLogger("search_tk55tk")
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_HOST_KEYWORDS = (
    "googlesyndication.com",
    "doubleclick.net",
    "google-analytics.com",
    "googletagmanager.com",
    "google.com/recaptcha",
)

RESULT_SELECTORS = [
    'a[href*="forum.php?mod=viewthread"]',
    'a[href*="thread-"]',
]
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def log(message):
    print(message, file=sys.stderr)


def strip_tags(value):
    value = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", "", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def response_text(response):
    response.encoding = "gbk"
    return response.text


def session_from_storage_state(state_path):
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    for cookie in state.get("cookies", []):
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    return session


def get_formhash(page_html):
    match = re.search(r'name="formhash"\s+value="([^"]+)"', page_html)
    if not match:
        raise RuntimeError("Could not find formhash on the home page.")
    return match.group(1)


def parse_http_results(page_html, base_url, limit):
    items = re.findall(r'<li class="pbw"[^>]*>.*?</li>', page_html, flags=re.I | re.S)
    results = []
    seen = set()
    for item in items:
        link_match = re.search(
            r'<a\s+href="(forum\.php\?mod=viewthread[^"]+)"[^>]*>(.*?)</a>',
            item,
            flags=re.I | re.S,
        )
        if not link_match:
            continue

        href = html.unescape(link_match.group(1))
        title = strip_tags(link_match.group(2))
        url = urljoin(base_url, href)
        if not title or url in seen:
            continue
        seen.add(url)

        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", item, flags=re.I | re.S)
        paragraph_texts = [strip_tags(paragraph) for paragraph in paragraphs]
        paragraph_texts = [text for text in paragraph_texts if text]
        meta = next((text for text in paragraph_texts if re.search(r"\d{4}-\d{1,2}-\d{1,2}", text)), "")
        snippet = " ".join(paragraph_texts)

        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "meta": meta.split(" - ")[0].strip() if meta else "",
            }
        )
        if len(results) >= limit:
            break

    return results


def extract_price_from_text(page_text):
    normalized = re.sub(r"\s+", " ", page_text).strip()
    patterns = [
        r"(?:售价|价格|价钱|需支付|需要支付|购买需|购买需要|支付|花费|付费)[^0-9]{0,30}(\d+(?:\.\d+)?)\s*(?:个)?(?:东周列国)?通宝",
        r"(\d+(?:\.\d+)?)\s*(?:个)?(?:东周列国)?通宝\s*(?:/|／|一)?\s*(?:部|份|个|帖|主题)?",
    ]
    candidates = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            value = float(match.group(1))
            start = max(0, match.start() - 35)
            end = min(len(normalized), match.end() + 35)
            context = normalized[start:end]
            has_price_hint = re.search(
                r"(售价|价格|价钱|购买|支付|花费|付费|出售|卖|通宝\s*(?:/|／|一)?\s*(?:部|份|个|帖|主题)?)",
                context,
            )
            looks_like_site_credit_def = re.search(r"creditnotice|贡献|东周列国通宝", context) and value <= 10
            if not has_price_hint or looks_like_site_credit_def:
                continue
            candidates.append(
                {
                    "tongbao": int(value) if value.is_integer() else value,
                    "rmb": round(value / TONGBAO_PER_RMB, 2),
                    "source_text": context,
                }
            )

    if not candidates:
        return {"tongbao": None, "rmb": None, "source_text": None}

    candidates.sort(
        key=lambda item: 0
        if re.search(r"(售价|价格|购买|支付|花费|付费)", item["source_text"])
        else 1
    )
    return candidates[0]


def extract_preview_images_from_html(page_html, base_url, limit=DEFAULT_PREVIEW_IMAGE_LIMIT):
    images = []
    seen = set()
    for match in re.finditer(r"<img\b[^>]*>", page_html, flags=re.I | re.S):
        tag = match.group(0)
        if not re.search(r'\binpost=["\']?1["\']?', tag, flags=re.I):
            continue

        raw_url = ""
        for attr in ("zoomfile", "file", "src"):
            source_match = re.search(rf'\b{attr}=["\']([^"\']+)["\']', tag, flags=re.I)
            if source_match:
                raw_url = html.unescape(source_match.group(1)).strip()
                if raw_url and "static/image/" not in raw_url and "uc_server/" not in raw_url:
                    break

        if not raw_url or "static/image/" in raw_url or "uc_server/" in raw_url:
            continue

        image_url = urljoin(base_url, raw_url)
        if image_url in seen:
            continue

        seen.add(image_url)
        images.append(image_url)
        if len(images) >= limit:
            break

    return images


def fetch_detail_price_http(session, result, index, total, detail_dir, timeout_ms):
    LOGGER.info("HTTP opening detail page %s/%s: %s", index + 1, total, result["url"])
    response = session.get(result["url"], timeout=timeout_ms / 1000)
    response.raise_for_status()
    page_html = response_text(response)
    page_text = strip_tags(page_html)
    result["price"] = extract_price_from_text(page_text)
    result["preview_images"] = extract_preview_images_from_html(page_html, result["url"])
    html_path = detail_dir / result_filename(index, result["url"])
    html_path.write_text(page_html, encoding="utf-8")
    LOGGER.info(
        "HTTP extracted detail for result %s: price=%s preview_images=%s",
        index + 1,
        result["price"],
        len(result["preview_images"]),
    )


def enrich_results_with_prices_http(session, results, output_dir, timeout_ms, detail_concurrency):
    detail_dir = output_dir / "details"
    detail_dir.mkdir(parents=True, exist_ok=True)
    total = len(results)
    max_workers = max(1, detail_concurrency)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_detail_price_http, session, result, index, total, detail_dir, timeout_ms): (index, result)
            for index, result in enumerate(results)
        }
        for future in as_completed(futures):
            index, result = futures[future]
            try:
                future.result()
            except Exception as exc:
                LOGGER.exception("HTTP failed to extract price for result %s.", index + 1)
                result["price"] = {
                    "tongbao": None,
                    "rmb": None,
                    "source_text": None,
                    "error": str(exc),
                }
                result["preview_images"] = []


def search_tk55tk_http_sync(keyword, url, output_dir, limit, timeout_ms, fetch_prices, detail_concurrency, state_path):
    session = session_from_storage_state(state_path)
    session.headers.update({"Referer": url})

    home_response = session.get(url, timeout=timeout_ms / 1000)
    home_response.raise_for_status()
    home_html = response_text(home_response)
    if "Rainy_fall" not in home_html and "退出" not in home_html:
        LOGGER.warning("HTTP home page did not clearly show a logged-in session.")

    formhash = get_formhash(home_html)
    body = urlencode(
        {
            "mod": "forum",
            "srchtxt": keyword,
            "formhash": formhash,
            "searchsubmit": "true",
        },
        encoding="gbk",
    ).encode("ascii")
    search_response = session.post(
        urljoin(url, "search.php?searchsubmit=yes"),
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout_ms / 1000,
        allow_redirects=True,
    )
    search_response.raise_for_status()
    search_html = response_text(search_response)
    results = parse_http_results(search_html, url, limit)
    if not results:
        raise RuntimeError("HTTP fast search returned no parseable results.")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "search_latest.html").write_text(search_html, encoding="utf-8")

    if fetch_prices:
        enrich_results_with_prices_http(session, results, output_dir, timeout_ms, detail_concurrency)

    LOGGER.info("HTTP fast search returned %s results.", len(results))
    return {
        "keyword": keyword,
        "count": len(results),
        "exchange_rate": {
            "rmb": 1,
            "tongbao": TONGBAO_PER_RMB,
        },
        "results": results,
    }


async def install_fast_route(context):
    async def route_handler(route):
        request = route.request
        if request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return

        url = request.url.lower()
        if any(keyword in url for keyword in BLOCKED_HOST_KEYWORDS):
            await route.abort()
            return

        await route.continue_()

    await context.route("**/*", route_handler)
    LOGGER.info("Installed resource blocking route for faster page loads.")


async def save_debug_files(page, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=output_dir / "search_latest.png", full_page=True)
    (output_dir / "search_latest.html").write_text(await page.content(), encoding="utf-8")
    LOGGER.info("Saved search debug files under: %s", output_dir.resolve())


async def open_home(context, url, timeout_ms):
    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    LOGGER.info("Opening home page: %s", url)
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1_000)
    return page


async def perform_search(context, page, keyword):
    search_box = page.locator("#scbar_txt").first
    search_button = page.locator("#scbar_btn").first

    await search_box.wait_for(state="visible")
    await search_box.fill(keyword)
    LOGGER.info("Filled search keyword.")

    try:
        async with context.expect_page(timeout=5_000) as popup_info:
            await search_button.click()
        result_page = await popup_info.value
        LOGGER.info("Search result opened in a new page.")
    except PlaywrightTimeoutError:
        result_page = page
        await search_button.click()
        LOGGER.info("Search result stayed in the current page.")

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


async def extract_preview_images(page, limit=DEFAULT_PREVIEW_IMAGE_LIMIT):
    return await page.evaluate(
        """
        ({ limit }) => {
          const images = [];
          const seen = new Set();
          for (const img of document.querySelectorAll('img[inpost="1"]')) {
            const rawUrl = img.getAttribute("zoomfile") || img.getAttribute("file") || img.getAttribute("src");
            if (!rawUrl || rawUrl.includes("static/image/") || rawUrl.includes("uc_server/")) continue;

            const url = new URL(rawUrl, document.baseURI).href;
            if (seen.has(url)) continue;
            seen.add(url);
            images.push(url);
            if (images.length >= limit) break;
          }
          return images;
        }
        """,
        {"limit": limit},
    )


async def enrich_one_result_with_price(context, result, index, total, detail_dir, timeout_ms):
    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    try:
        LOGGER.info("Opening detail page %s/%s: %s", index + 1, total, result["url"])
        await page.goto(result["url"], wait_until="domcontentloaded")
        await page.wait_for_timeout(500)
        result["price"] = await extract_price(page)
        result["preview_images"] = await extract_preview_images(page)
        html_path = detail_dir / result_filename(index, result["url"])
        html_path.write_text(await page.content(), encoding="utf-8")
        LOGGER.info(
            "Extracted detail for result %s: price=%s preview_images=%s",
            index + 1,
            result["price"],
            len(result["preview_images"]),
        )
    except Exception as exc:
        LOGGER.exception("Failed to extract price for result %s.", index + 1)
        result["price"] = {
            "tongbao": None,
            "rmb": None,
            "source_text": None,
            "error": str(exc),
        }
        result["preview_images"] = []
    finally:
        await page.close()


async def enrich_results_with_prices(context, results, output_dir, timeout_ms, detail_concurrency):
    detail_dir = output_dir / "details"
    detail_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max(1, detail_concurrency))
    total = len(results)

    async def guarded(index, result):
        async with semaphore:
            await enrich_one_result_with_price(context, result, index, total, detail_dir, timeout_ms)

    LOGGER.info("Extracting detail prices with concurrency=%s.", detail_concurrency)
    await asyncio.gather(*(guarded(index, result) for index, result in enumerate(results)))


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
        detail_concurrency=args.detail_concurrency,
        fast_http=args.fast_http,
        log_level=args.log_level,
        log_file=args.log_file,
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
    fetch_prices=None,
    allow_config_price_override=True,
    detail_concurrency=DEFAULT_DETAIL_CONCURRENCY,
    fast_http=DEFAULT_FAST_HTTP,
    log_level="INFO",
    log_file=None,
):
    config = load_config(config_path)
    keyword = keyword or config.get("search_keyword") or DEFAULT_SEARCH_KEYWORD
    if not keyword:
        raise ValueError("Missing keyword. Usage: python search_tk55tk.py <keyword>")

    url = url or config.get("url") or DEFAULT_URL
    output_dir = Path(output_dir or config.get("output_dir") or OUTPUT_DIR)
    log_file = log_file or output_dir / "search.log"
    global LOGGER
    LOGGER = setup_logging("search_tk55tk", log_level, log_file)
    limit = limit or config.get("search_limit") or DEFAULT_LIMIT
    if fetch_prices is None and "fetch_prices" in config and allow_config_price_override:
        fetch_prices = bool(config["fetch_prices"])
    if fetch_prices is None:
        fetch_prices = DEFAULT_FETCH_PRICES
    state_path = output_dir / "storage_state.json"
    if not state_path.exists():
        raise RuntimeError(f"Missing login state: {state_path}. Run login_tk55tk.py first.")
    LOGGER.info(
        "Starting search: url=%s keyword_length=%s limit=%s fetch_prices=%s detail_concurrency=%s fast_http=%s headless=%s",
        url,
        len(keyword),
        limit,
        fetch_prices,
        detail_concurrency,
        fast_http,
        headless,
    )

    if fast_http:
        try:
            return await asyncio.to_thread(
                search_tk55tk_http_sync,
                keyword,
                url,
                output_dir,
                limit,
                timeout_ms,
                fetch_prices,
                detail_concurrency,
                state_path,
            )
        except Exception:
            LOGGER.exception("HTTP fast search failed; falling back to Playwright.")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(**browser_launch_kwargs(headless, slow_mo, LOGGER))
        except Exception as exc:
            LOGGER.exception("Failed to launch Chromium.")
            raise RuntimeError(linux_browser_help()) from exc

        context = await browser.new_context(storage_state=state_path)
        await install_fast_route(context)
        try:
            home_page = await open_home(context, url, timeout_ms)
            result_page = await perform_search(context, home_page, keyword)
            results = await extract_results(result_page, limit)
            LOGGER.info("Extracted %s search results.", len(results))
            if fetch_prices and results:
                await enrich_results_with_prices(context, results, output_dir, timeout_ms, detail_concurrency)
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
            LOGGER.info("Closing browser.")
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
    parser.add_argument("--detail-concurrency", type=int, default=DEFAULT_DETAIL_CONCURRENCY)
    parser.add_argument("--fast-http", action="store_true", default=DEFAULT_FAST_HTTP)
    parser.add_argument("--no-fast-http", action="store_false", dest="fast_http")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", help="Write logs to this file. Defaults to output_dir/search.log.")
    parser.add_argument(
        "--fetch-prices",
        action="store_true",
        default=None,
        dest="fetch_prices",
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
    parser.add_argument(
        "--headed",
        action="store_false",
        dest="headless",
        help="Show the browser window. Use only on machines with a desktop display.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
