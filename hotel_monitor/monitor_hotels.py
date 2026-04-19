#!/usr/bin/env python3
"""
釜山ホテル空室監視スクリプト
Booking.com と Trip.com を監視し、予算内のホテルが見つかったらDiscordに通知する
"""

import asyncio
import os
import re
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# --- 設定 ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
BUDGET_JPY = 20_000
CHECKIN = "2026-06-12"
CHECKOUT = "2026-06-13"
HEADLESS = True

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Discord 通知
# ---------------------------------------------------------------------------

def send_discord_notification(hotels: list[dict]) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] DISCORD_WEBHOOK_URL が未設定のためスキップします")
        return

    embeds = []
    for h in hotels[:10]:  # Discord は embed 最大10件
        embeds.append({
            "title": f"🏨 {h['name']}",
            "description": (
                f"**サイト**: {h['site']}\n"
                f"**金額**: {h['price']}\n"
                f"**URL**: {h.get('url', 'N/A')}"
            ),
            "color": 0x00FF88,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    payload = {
        "content": (
            f"💡 **釜山ホテル空室情報** ({CHECKIN} チェックイン)\n"
            f"予算 ¥{BUDGET_JPY:,} 以下のホテルが **{len(hotels)}** 件見つかりました！"
        ),
        "embeds": embeds,
    }

    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    if resp.status_code == 204:
        print(f"[Discord] 通知送信完了 ({len(hotels)} 件)")
    else:
        print(f"[Discord] 送信失敗: HTTP {resp.status_code} / {resp.text}")


# ---------------------------------------------------------------------------
# 価格テキスト → 整数（円）変換ユーティリティ
# ---------------------------------------------------------------------------

def parse_price_jpy(text: str) -> int | None:
    """
    "¥15,800" / "15800円" / "15,800" などから数値を抽出して返す。
    数値が取れない場合は None。
    """
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


# ---------------------------------------------------------------------------
# Booking.com
# ---------------------------------------------------------------------------

async def check_booking_com(pw) -> list[dict]:
    results = []
    browser = await pw.chromium.launch(headless=HEADLESS)
    ctx = await browser.new_context(locale="ja-JP", user_agent=USER_AGENT)

    try:
        page = await ctx.new_page()

        # 価格フィルター付き釜山検索（円建て・安い順）
        url = (
            "https://www.booking.com/searchresults.ja.html"
            "?ss=%E9%87%9C%E5%B1%B1%2C+%E9%9F%93%E5%9B%BD"
            f"&checkin={CHECKIN}&checkout={CHECKOUT}"
            "&group_adults=2&no_rooms=1"
            "&order=price"
            f"&nflt=price%3DJPY-0-{BUDGET_JPY}-1"
        )

        print(f"  [Booking.com] {url}")
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(3_000)

        cards = await page.query_selector_all('[data-testid="property-card"]')
        print(f"  [Booking.com] {len(cards)} 件のカードを検出")

        for card in cards[:30]:
            try:
                name_el = await card.query_selector('[data-testid="title"]')
                price_el = await card.query_selector('[data-testid="price-and-discounted-price"]')
                if not name_el or not price_el:
                    continue

                name = (await name_el.inner_text()).strip()
                price_text = await price_el.inner_text()
                price = parse_price_jpy(price_text)
                if price is None:
                    continue

                link_el = await card.query_selector('a[data-testid="title-link"]')
                href = (await link_el.get_attribute("href")) if link_el else ""
                hotel_url = (
                    f"https://www.booking.com{href}" if href.startswith("/") else href
                )

                if price <= BUDGET_JPY:
                    print(f"    ✓ {name}: ¥{price:,}")
                    results.append({
                        "site": "Booking.com",
                        "name": name,
                        "price": f"¥{price:,}",
                        "price_num": price,
                        "url": hotel_url,
                    })
            except Exception as e:
                print(f"    [Booking.com] カード解析エラー: {e}")

    except PlaywrightTimeoutError:
        print("  [Booking.com] タイムアウト")
    except Exception as e:
        print(f"  [Booking.com] エラー: {e}")
    finally:
        await ctx.close()
        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Trip.com
# ---------------------------------------------------------------------------

async def check_trip_com(pw) -> list[dict]:
    results = []
    browser = await pw.chromium.launch(headless=HEADLESS)
    ctx = await browser.new_context(locale="ja-JP", user_agent=USER_AGENT)

    try:
        page = await ctx.new_page()

        # 釜山の Trip.com city ID = 10093
        url = (
            "https://jp.trip.com/hotels/list"
            "?city=10093"
            f"&checkin={CHECKIN}&checkout={CHECKOUT}"
            "&adult=2&children=0&rooms=1"
            "&curr=JPY&locale=ja-JP"
            "&sortorder=0"  # 価格昇順
        )

        print(f"  [Trip.com] {url}")
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(5_000)

        # Trip.com はセレクターが変わりやすいため複数候補を試みる
        selectors = [
            ".hotel-list-item",
            '[class*="HotelListItem"]',
            '[class*="hotel-item"]',
            '[class*="hotelItem"]',
            'li[class*="hotel"]',
        ]
        cards = []
        for sel in selectors:
            cards = await page.query_selector_all(sel)
            if cards:
                print(f"  [Trip.com] セレクター '{sel}' で {len(cards)} 件検出")
                break

        if not cards:
            print("  [Trip.com] ホテルカードが見つかりません（セレクター要確認）")

        for card in cards[:30]:
            try:
                name_el = await card.query_selector(
                    '[class*="hotel-name"], [class*="hotelName"], [class*="HotelName"], h2, h3'
                )
                price_el = await card.query_selector(
                    '[class*="price-int"], [class*="priceInt"], [class*="Price"], [class*="price"]'
                )
                if not name_el or not price_el:
                    continue

                name = (await name_el.inner_text()).strip()
                price_text = await price_el.inner_text()
                price = parse_price_jpy(price_text)
                if price is None:
                    continue

                link_el = await card.query_selector("a")
                href = (await link_el.get_attribute("href")) if link_el else ""
                hotel_url = (
                    f"https://jp.trip.com{href}"
                    if href and not href.startswith("http")
                    else href
                )

                if price <= BUDGET_JPY:
                    print(f"    ✓ {name}: ¥{price:,}")
                    results.append({
                        "site": "Trip.com",
                        "name": name,
                        "price": f"¥{price:,}",
                        "price_num": price,
                        "url": hotel_url,
                    })
            except Exception as e:
                print(f"    [Trip.com] カード解析エラー: {e}")

    except PlaywrightTimeoutError:
        print("  [Trip.com] タイムアウト")
    except Exception as e:
        print(f"  [Trip.com] エラー: {e}")
    finally:
        await ctx.close()
        await browser.close()

    return results


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

async def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== ホテル空室監視開始 {now} ===")
    print(f"場所: 釜山　チェックイン: {CHECKIN}　チェックアウト: {CHECKOUT}")
    print(f"予算: ¥{BUDGET_JPY:,} 以下\n")

    async with async_playwright() as pw:
        print("【Booking.com 確認中...】")
        booking = await check_booking_com(pw)

        print("\n【Trip.com 確認中...】")
        trip = await check_trip_com(pw)

    all_hotels = booking + trip
    print(f"\n=== 結果サマリー ===")
    print(f"Booking.com: {len(booking)} 件 / Trip.com: {len(trip)} 件 / 合計: {len(all_hotels)} 件")

    if all_hotels:
        all_hotels.sort(key=lambda h: h["price_num"])
        send_discord_notification(all_hotels)
    else:
        print("予算内のホテルは見つかりませんでした")

    print(f"\n=== 監視完了 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    asyncio.run(main())
