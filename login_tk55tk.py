import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


DEFAULT_URL = "https://www.tk55tk.com/"
DEFAULT_CONFIG = Path("tk55tk_config.json")
OUTPUT_DIR = Path("tk55tk_output")
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_SLOW_MO = 100
DEFAULT_REUSE_STATE = False
DEFAULT_KEEP_OPEN = False
DEFAULT_HEADLESS = False

USERNAME_SELECTORS = [
    'input[name*="user" i]',
    'input[name*="account" i]',
    'input[name*="login" i]',
    'input[name*="phone" i]',
    'input[name*="email" i]',
    'input[placeholder*="账号"]',
    'input[placeholder*="帳號"]',
    'input[placeholder*="用户名"]',
    'input[placeholder*="用戶名"]',
    'input[placeholder*="手机"]',
    'input[placeholder*="手機"]',
    'input[type="text"]',
    "input:not([type])",
]

PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[name*="pass" i]',
    'input[placeholder*="密码"]',
    'input[placeholder*="密碼"]',
]

LOGIN_OPENERS = [
    'text=/^\\s*登录\\s*$/',
    'text=/^\\s*登錄\\s*$/',
    'text=/^\\s*登入\\s*$/',
    'text=/^\\s*Login\\s*$/i',
    'a:has-text("登录")',
    'a:has-text("登錄")',
    'button:has-text("登录")',
    'button:has-text("登錄")',
]

SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("登录")',
    'button:has-text("登錄")',
    'button:has-text("登入")',
    'input[value*="登录"]',
    'input[value*="登錄"]',
    'text=/^\\s*登录\\s*$/',
    'text=/^\\s*登錄\\s*$/',
    'text=/^\\s*Login\\s*$/i',
]


async def first_visible(frame, selectors, timeout_ms=600):
    for selector in selectors:
        locator = frame.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return locator, selector
        except PlaywrightTimeoutError:
            continue
    return None, None


async def find_login_fields(page):
    for frame in page.frames:
        password, password_selector = await first_visible(frame, PASSWORD_SELECTORS)
        if not password:
            continue

        username, username_selector = await first_visible(frame, USERNAME_SELECTORS)
        if username:
            return frame, username, username_selector, password, password_selector
    return None, None, None, None, None


async def try_open_login(page):
    for frame in page.frames:
        opener, selector = await first_visible(frame, LOGIN_OPENERS, timeout_ms=300)
        if not opener:
            continue

        try:
            await opener.click(timeout=2_000)
            print(f"Clicked login opener: {selector}")
            await page.wait_for_timeout(1_000)
            return True
        except Exception as exc:
            print(f"Could not click login opener {selector!r}: {exc}")
    return False


async def submit_login(password_box, page):
    form_submit = password_box.locator(
        'xpath=ancestor::form[1]//button[@type="submit" or @name="loginsubmit"]'
        '|ancestor::form[1]//input[@type="submit" or @name="loginsubmit"]'
    ).first
    try:
        await form_submit.wait_for(state="visible", timeout=1_000)
        await form_submit.click(timeout=3_000)
        print("Clicked submit button in the login form.")
        return
    except PlaywrightTimeoutError:
        pass

    await page.keyboard.press("Enter")
    print("Pressed Enter to submit.")


async def follow_discuz_redirect(page):
    redirect_link = page.locator('a:has-text("点击此链接"), a:has-text("點擊此鏈接")').first
    try:
        await redirect_link.wait_for(state="visible", timeout=3_000)
        await redirect_link.click(timeout=3_000)
        print("Followed Discuz redirect link.")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(1_000)
    except PlaywrightTimeoutError:
        await page.wait_for_timeout(3_000)


async def save_debug_files(page, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=output_dir / "latest.png", full_page=True)
    (output_dir / "latest.html").write_text(await page.content(), encoding="utf-8")
    print(f"Saved debug files under: {output_dir.resolve()}")


def load_config(path):
    if not path:
        return {}

    config_path = Path(path)
    if not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


async def run(args):
    config = load_config(args.config)
    username = args.username or os.getenv("TK55TK_USERNAME") or config.get("username")
    password = args.password or os.getenv("TK55TK_PASSWORD") or config.get("password")
    url = args.url or config.get("url") or DEFAULT_URL
    output_dir = Path(args.output_dir or config.get("output_dir") or OUTPUT_DIR)

    if not username:
        username = input("TK55TK username: ").strip()
    if not password:
        password = getpass.getpass("TK55TK password: ")

    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "storage_state.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless, slow_mo=args.slow_mo)
        context_kwargs = {}
        if args.reuse_state and state_path.exists():
            context_kwargs["storage_state"] = state_path

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        page.set_default_timeout(args.timeout_ms)

        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(1_000)

            frame, username_box, username_selector, password_box, password_selector = await find_login_fields(page)
            if not password_box:
                await try_open_login(page)
                frame, username_box, username_selector, password_box, password_selector = await find_login_fields(page)

            if not username_box or not password_box:
                await save_debug_files(page, output_dir)
                raise RuntimeError(
                    "Could not find login fields. Check tk55tk_output/latest.png and "
                    "tk55tk_output/latest.html, then update selectors in this script."
                )

            await username_box.fill(username)
            await password_box.fill(password)
            print(f"Filled username via {username_selector}; password via {password_selector}.")

            await submit_login(password_box, page)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=args.timeout_ms)
            except PlaywrightTimeoutError:
                pass
            await follow_discuz_redirect(page)

            await context.storage_state(path=state_path)
            await save_debug_files(page, output_dir)
            print(f"Saved login session to: {state_path.resolve()}")

            if args.keep_open:
                print("Browser will stay open. Press Ctrl+C in this terminal to stop.")
                while True:
                    await page.wait_for_timeout(1_000)

        finally:
            if not args.keep_open:
                await context.close()
                await browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Login helper for tk55tk.com")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to a JSON config file.")
    parser.add_argument("--url", help="Override URL from config.")
    parser.add_argument("--username", help="Override username from config or env var.")
    parser.add_argument("--password", help="Override password from config or env var.")
    parser.add_argument("--output-dir", help="Override output directory from config.")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--slow-mo", type=int, default=DEFAULT_SLOW_MO)
    parser.add_argument(
        "--reuse-state",
        action="store_true",
        default=DEFAULT_REUSE_STATE,
        help="Reuse saved browser session if available.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        default=DEFAULT_KEEP_OPEN,
        help="Keep browser open after login.",
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
