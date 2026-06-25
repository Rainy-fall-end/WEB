import argparse
import asyncio
import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

import requests

from tk55tk_runtime import setup_logging


DEFAULT_URL = "https://www.qinglanhua2026.com/"
DEFAULT_LIMIT = 3
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_DETAIL_CONCURRENCY = 3
DEFAULT_PREVIEW_IMAGE_LIMIT = 3

LOGGER = logging.getLogger("search_qinglanhua")
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def strip_tags(value):
    value = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", "", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def response_text(response):
    response.encoding = "utf-8"
    return response.text


def result_filename(index, url):
    thread_match = re.search(r"thread-(\d+)-", url)
    thread_id = thread_match.group(1) if thread_match else str(index + 1)
    return f"qinglanhua_result_{index + 1}_{thread_id}.html"


def extract_preview_images_from_html(page_html, base_url, limit=DEFAULT_PREVIEW_IMAGE_LIMIT):
    images = []
    seen = set()
    for match in re.finditer(r"<img\b[^>]*>", page_html, flags=re.I | re.S):
        tag = match.group(0)
        raw_url = ""
        for attr in ("zoomfile", "file", "src"):
            source_match = re.search(rf'\b{attr}=["\']([^"\']+)["\']', tag, flags=re.I)
            if source_match:
                raw_url = html.unescape(source_match.group(1)).strip()
                if raw_url and "static/image/" not in raw_url and "uc_server/" not in raw_url:
                    break

        if not raw_url or "static/image/" in raw_url or "uc_server/" in raw_url:
            continue
        if "template/" in raw_url or "data/attachment/portal/" in raw_url:
            continue
        if not re.search(r'\binpost=["\']?1["\']?', tag, flags=re.I) and "data/attachment/forum/" not in raw_url:
            continue

        image_url = urljoin(base_url, raw_url)
        if image_url in seen:
            continue

        seen.add(image_url)
        images.append(image_url)
        if len(images) >= limit:
            break

    return images


def extract_thread_links(page_html, base_url):
    results = []
    seen = set()
    for match in re.finditer(r'<a\s+href=["\'](thread-\d+-\d+-\d+\.html)["\'][^>]*>(.*?)</a>', page_html, flags=re.I | re.S):
        href = html.unescape(match.group(1))
        content = match.group(2)
        title = strip_tags(content)
        image_match = re.search(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>', content, flags=re.I | re.S)
        alt_match = re.search(r'\balt=["\']([^"\']+)["\']', content, flags=re.I | re.S)

        if not title and alt_match:
            title = html.unescape(alt_match.group(1)).strip()

        if not title or title.isdigit():
            continue

        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        preview_images = []
        if image_match:
            image_url = urljoin(base_url, html.unescape(image_match.group(1)).strip())
            if "static/image/" not in image_url:
                preview_images.append(image_url)

        results.append(
            {
                "title": title,
                "url": url,
                "snippet": title,
                "meta": "",
                "price": {"tongbao": None, "rmb": None, "source_text": None},
                "preview_images": preview_images,
            }
        )

    return results


def fetch_source_pages(session, base_url, timeout_ms):
    pages = []
    home_response = session.get(base_url, timeout=timeout_ms / 1000)
    home_response.raise_for_status()
    home_html = response_text(home_response)
    pages.append((base_url, home_html))

    forum_links = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', home_html, flags=re.I):
        href = html.unescape(href)
        if re.search(r"(?:forum-\d+-\d+\.html|forum\.php\?mod=forumdisplay)", href) and href not in forum_links:
            forum_links.append(href)

    for href in forum_links[:3]:
        page_url = urljoin(base_url, href)
        try:
            response = session.get(page_url, timeout=timeout_ms / 1000)
            response.raise_for_status()
            pages.append((page_url, response_text(response)))
        except Exception:
            LOGGER.exception("Failed to fetch Qinglanhua forum page: %s", page_url)

    return pages


def match_results(keyword, candidates, limit):
    normalized_keyword = keyword.strip().casefold()
    matched = []
    seen = set()
    for result in candidates:
        haystack = f"{result.get('title', '')} {result.get('snippet', '')}".casefold()
        if normalized_keyword not in haystack:
            continue
        if result["url"] in seen:
            continue
        seen.add(result["url"])
        matched.append(result)
        if len(matched) >= limit:
            break
    return matched


def enrich_one_detail(session, result, index, total, detail_dir, timeout_ms):
    LOGGER.info("Opening Qinglanhua detail page %s/%s: %s", index + 1, total, result["url"])
    response = session.get(result["url"], timeout=timeout_ms / 1000)
    response.raise_for_status()
    page_html = response_text(response)
    detail_images = extract_preview_images_from_html(page_html, result["url"])
    if detail_images:
        result["preview_images"] = detail_images
    html_path = detail_dir / result_filename(index, result["url"])
    html_path.write_text(page_html, encoding="utf-8")
    LOGGER.info("Extracted %s Qinglanhua preview images for result %s.", len(result["preview_images"]), index + 1)


def enrich_details(session, results, output_dir, timeout_ms, detail_concurrency):
    detail_dir = output_dir / "details"
    detail_dir.mkdir(parents=True, exist_ok=True)
    total = len(results)
    with ThreadPoolExecutor(max_workers=max(1, detail_concurrency)) as executor:
        futures = {
            executor.submit(enrich_one_detail, session, result, index, total, detail_dir, timeout_ms): (index, result)
            for index, result in enumerate(results)
        }
        for future in as_completed(futures):
            index, result = futures[future]
            try:
                future.result()
            except Exception as exc:
                LOGGER.exception("Failed to enrich Qinglanhua result %s.", index + 1)
                result.setdefault("preview_images", [])
                result["detail_error"] = str(exc)


def search_qinglanhua_sync(keyword, url, output_dir, limit, timeout_ms, detail_concurrency):
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    pages = fetch_source_pages(session, url, timeout_ms)
    candidates = []
    for page_url, page_html in pages:
        candidates.extend(extract_thread_links(page_html, page_url))

    results = match_results(keyword, candidates, limit)
    if results:
        enrich_details(session, results, output_dir, timeout_ms, detail_concurrency)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "qinglanhua_latest.html").write_text(pages[0][1], encoding="utf-8")
    return {
        "keyword": keyword,
        "count": len(results),
        "exchange_rate": {"rmb": 1, "tongbao": 100},
        "results": results,
    }


async def search_qinglanhua(
    keyword=None,
    config_path=None,
    url=None,
    output_dir=None,
    limit=None,
    timeout_ms=DEFAULT_TIMEOUT_MS,
    slow_mo=0,
    headless=True,
    fetch_prices=True,
    allow_config_price_override=True,
    detail_concurrency=DEFAULT_DETAIL_CONCURRENCY,
    log_level="INFO",
    log_file=None,
):
    if not keyword:
        raise SystemExit("Missing keyword for Qinglanhua search.")

    url = url or DEFAULT_URL
    output_dir = Path(output_dir or "tk55tk_output")
    logger_file = log_file or output_dir / "qinglanhua.log"
    global LOGGER
    LOGGER = setup_logging("search_qinglanhua", log_level, logger_file)
    LOGGER.info("Starting Qinglanhua public-page search: keyword_length=%s limit=%s", len(keyword), limit or DEFAULT_LIMIT)

    return await asyncio.to_thread(
        search_qinglanhua_sync,
        keyword,
        url,
        output_dir,
        limit or DEFAULT_LIMIT,
        timeout_ms,
        detail_concurrency,
    )


async def run(args):
    payload = await search_qinglanhua(
        keyword=args.keyword,
        url=args.url,
        output_dir=args.output_dir,
        limit=args.limit,
        timeout_ms=args.timeout_ms,
        detail_concurrency=args.detail_concurrency,
        log_level=args.log_level,
        log_file=args.log_file,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args():
    parser = argparse.ArgumentParser(description="Search Qinglanhua public pages and return JSON results.")
    parser.add_argument("keyword")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output-dir", default="tk55tk_output")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--detail-concurrency", type=int, default=DEFAULT_DETAIL_CONCURRENCY)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
