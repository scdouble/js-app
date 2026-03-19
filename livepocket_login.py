#!/usr/bin/env python3
"""
LivePocket ログインスクリプト
設定: config.ini に email / password を記入してから実行

試行順:
  1. curl_cffi  (TLSフィンガープリント偽装)
  2. cloudscraper (Cloudflare JSチャレンジ対応)
  3. playwright  (実ブラウザ、最終手段)

依存ライブラリのインストール:
  pip install curl_cffi cloudscraper beautifulsoup4 playwright
  playwright install chromium
"""

import configparser
import asyncio
from pathlib import Path
from bs4 import BeautifulSoup

LOGIN_URL   = "https://livepocket.jp/login"
CONFIG_FILE = Path(__file__).parent / "config.ini"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    "Referer":         "https://livepocket.jp/",
}


# ---------------------------------------------------------------------------
# 設定読み込み
# ---------------------------------------------------------------------------

def load_config() -> tuple[str, str]:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {CONFIG_FILE}")
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    email    = cfg["livepocket"]["email"]
    password = cfg["livepocket"]["password"]
    return email, password


# ---------------------------------------------------------------------------
# 共通: レスポンスHTMLからCSRFトークンを探す
# ---------------------------------------------------------------------------

def extract_csrf(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for name in ("_csrf_token", "csrf_token", "authenticity_token", "_token"):
        tag = soup.find("input", {"name": name})
        if tag:
            return tag.get("value")
    # meta タグも試す
    meta = soup.find("meta", {"name": "csrf-token"})
    if meta:
        return meta.get("content")
    return None


def build_payload(email: str, password: str, csrf: str | None) -> dict:
    payload = {"email": email, "password": password}
    if csrf:
        payload["_csrf_token"] = csrf
    return payload


def check_success(status: int, url: str, html: str) -> bool:
    """ログイン成功かどうかを判定"""
    if "/login" in url:
        return False
    if status in (200, 302) and "/login" not in url:
        return True
    # エラーメッセージがあれば失敗
    if "パスワードが違います" in html or "メールアドレスが違います" in html:
        return False
    return status == 200


# ---------------------------------------------------------------------------
# 方法 1: curl_cffi (TLSフィンガープリント偽装)
# ---------------------------------------------------------------------------

def try_curl_cffi(email: str, password: str) -> dict | None:
    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        print("  [skip] curl_cffi 未インストール")
        return None

    print("[1] curl_cffi で試行中...")
    try:
        session = cf_requests.Session(impersonate="chrome120")
        session.headers.update(HEADERS)

        res = session.get(LOGIN_URL, timeout=15)
        print(f"  GET {res.status_code}")
        if res.status_code != 200:
            return None

        csrf = extract_csrf(res.text)
        print(f"  CSRFトークン: {csrf[:20] + '...' if csrf else 'なし'}")

        payload = build_payload(email, password, csrf)
        res2 = session.post(LOGIN_URL, data=payload, timeout=15)
        print(f"  POST {res2.status_code} → {res2.url}")

        if check_success(res2.status_code, str(res2.url), res2.text):
            cookies = {c.name: c.value for c in session.cookies}
            return {"method": "curl_cffi", "cookies": cookies, "url": str(res2.url)}

    except Exception as e:
        print(f"  エラー: {e}")
    return None


# ---------------------------------------------------------------------------
# 方法 2: cloudscraper (Cloudflare JSチャレンジ対応)
# ---------------------------------------------------------------------------

def try_cloudscraper(email: str, password: str) -> dict | None:
    try:
        import cloudscraper
    except ImportError:
        print("  [skip] cloudscraper 未インストール")
        return None

    print("[2] cloudscraper で試行中...")
    try:
        scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
        scraper.headers.update(HEADERS)

        res = scraper.get(LOGIN_URL, timeout=15)
        print(f"  GET {res.status_code}")
        if res.status_code != 200:
            return None

        csrf = extract_csrf(res.text)
        print(f"  CSRFトークン: {csrf[:20] + '...' if csrf else 'なし'}")

        payload = build_payload(email, password, csrf)
        res2 = scraper.post(LOGIN_URL, data=payload, timeout=15)
        print(f"  POST {res2.status_code} → {res2.url}")

        if check_success(res2.status_code, str(res2.url), res2.text):
            cookies = dict(scraper.cookies)
            return {"method": "cloudscraper", "cookies": cookies, "url": str(res2.url)}

    except Exception as e:
        print(f"  エラー: {e}")
    return None


# ---------------------------------------------------------------------------
# 方法 3: playwright (実ブラウザ、最終手段)
# ---------------------------------------------------------------------------

async def try_playwright_async(email: str, password: str) -> dict | None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  [skip] playwright 未インストール")
        return None

    print("[3] playwright で試行中...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="ja-JP",
            )
            page = await context.new_page()

            await page.goto(LOGIN_URL, wait_until="networkidle")
            print(f"  ページ取得: {page.url}")

            await page.fill('input[type="email"], input[name="email"]', email)
            await page.fill('input[type="password"], input[name="password"]', password)
            await page.click('button[type="submit"]')

            try:
                await page.wait_for_url(
                    lambda url: "/login" not in url,
                    timeout=15_000,
                )
            except Exception:
                await page.screenshot(path="login_failed.png")
                print("  タイムアウト → login_failed.png を確認")
                await browser.close()
                return None

            cookies = {c["name"]: c["value"] for c in await context.cookies()}
            url = page.url
            await browser.close()
            print(f"  ログイン成功: {url}")
            return {"method": "playwright", "cookies": cookies, "url": url}

    except Exception as e:
        print(f"  エラー: {e}")
    return None


def try_playwright(email: str, password: str) -> dict | None:
    return asyncio.run(try_playwright_async(email, password))


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    email, password = load_config()
    print(f"ログイン試行: {email}\n")

    result = (
        try_curl_cffi(email, password)
        or try_cloudscraper(email, password)
        or try_playwright(email, password)
    )

    if result:
        print(f"\n✅ ログイン成功 (方法: {result['method']})")
        print(f"   URL: {result['url']}")
        print("   Cookies:")
        for k, v in result["cookies"].items():
            print(f"     {k}: {str(v)[:60]}")
    else:
        print("\n❌ 全方法でログイン失敗")
        print("   - SMS認証が必要な場合は playwright の headless=True → False に変更")
        print("   - login_failed.png でフォーム構造を確認")


if __name__ == "__main__":
    main()
