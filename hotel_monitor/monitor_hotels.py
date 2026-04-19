#!/usr/bin/env python3
"""
釜山ホテル空室監視スクリプト
Booking.com と Trip.com を監視し、予算内のホテルが見つかったらDiscordに通知する
"""

import os
import re
import time
from datetime import datetime, timezone

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium_stealth import stealth

# --- 設定 ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
BUDGET_JPY = 20_000
CHECKIN = "2026-06-12"
CHECKOUT = "2026-06-13"
SCREENSHOT_DIR = "screenshots"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def make_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    stealth(
        driver,
        languages=["ja-JP", "ja"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )
    return driver


# ---------------------------------------------------------------------------
# Discord 通知
# ---------------------------------------------------------------------------

def send_discord_notification(hotels: list[dict]) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] DISCORD_WEBHOOK_URL が未設定のためスキップします")
        return

    embeds = []
    for h in hotels[:10]:
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


def parse_price_jpy(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


# ---------------------------------------------------------------------------
# Booking.com
# ---------------------------------------------------------------------------

def check_booking_com() -> list[dict]:
    results = []
    driver = make_driver()

    try:
        # 価格フィルターはURLに含めず、コード側でフィルタリング
        url = (
            "https://www.booking.com/searchresults.ja.html"
            "?ss=Busan%2C+South+Korea"
            f"&checkin={CHECKIN}&checkout={CHECKOUT}"
            "&group_adults=2&no_rooms=1"
            "&order=price"
            "&selected_currency=JPY"
        )
        print(f"  [Booking.com] アクセス中...")
        driver.get(url)
        time.sleep(8)

        # ポップアップを閉じる
        try:
            from selenium.webdriver.common.keys import Keys
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(1)
        except Exception:
            pass
        try:
            close_btn = driver.find_element(
                By.CSS_SELECTOR,
                'button[aria-label="閉じる"], button[aria-label="Close"], [data-testid="modal-mask"] button'
            )
            close_btn.click()
            time.sleep(1)
        except Exception:
            pass

        driver.save_screenshot(f"{SCREENSHOT_DIR}/booking_com.png")
        print(f"  [Booking.com] スクリーンショット保存完了")

        cards = driver.find_elements(By.CSS_SELECTOR, '[data-testid="property-card"]')
        print(f"  [Booking.com] {len(cards)} 件のカードを検出")

        for card in cards[:30]:
            try:
                name_el = card.find_element(By.CSS_SELECTOR, '[data-testid="title"]')
                price_el = card.find_element(By.CSS_SELECTOR, '[data-testid="price-and-discounted-price"]')

                name = name_el.text.strip()
                price = parse_price_jpy(price_el.text)
                if price is None:
                    continue

                try:
                    link_el = card.find_element(By.CSS_SELECTOR, 'a[data-testid="title-link"]')
                    hotel_url = link_el.get_attribute("href")
                except Exception:
                    hotel_url = ""

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

    except Exception as e:
        print(f"  [Booking.com] エラー: {e}")
    finally:
        driver.quit()

    return results


# ---------------------------------------------------------------------------
# Trip.com
# ---------------------------------------------------------------------------

def check_trip_com() -> list[dict]:
    results = []
    driver = make_driver()

    try:
        # Trip.com 釜山ホテル検索（フォーム操作 → 検索ボタンで結果ページへ）
        print(f"  [Trip.com] アクセス中...")
        driver.get("https://jp.trip.com/hotels/")
        time.sleep(5)

        try:
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            # 目的地入力欄をクリアして「Busan」を入力
            dest = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR,
                    'input[data-testid*="dest"], input[placeholder*="目的"], '
                    'input[placeholder*="都市"], input[placeholder*="Where"], '
                    'input[class*="dest"]'
                ))
            )
            dest.click()
            dest.send_keys(Keys.CONTROL + "a")
            dest.send_keys("Busan")
            time.sleep(3)

            # ドロップダウンからBusan/釜山/Koreaの候補を選択
            suggestions = driver.find_elements(By.CSS_SELECTOR,
                '[class*="suggestItem"], [class*="suggest-item"], '
                '[class*="SuggestItem"], li[class*="item"], [class*="option"]'
            )
            clicked = False
            for s in suggestions:
                text = s.text
                if any(kw in text for kw in ["Busan", "釜山", "Korea", "韓国", "부산"]):
                    s.click()
                    clicked = True
                    print(f"  [Trip.com] 候補を選択: {text[:50]}")
                    break
            if not clicked and suggestions:
                suggestions[0].click()
                print(f"  [Trip.com] 先頭候補を選択: {suggestions[0].text[:50]}")
            time.sleep(2)

            # 検索ボタンをクリックして結果ページへ
            search_btn = driver.find_element(By.CSS_SELECTOR,
                'button[type="submit"], [class*="searchBtn"], [class*="search-btn"], '
                'button[class*="Search"], [data-testid*="search"]'
            )
            search_btn.click()
            time.sleep(10)

        except Exception as e:
            print(f"  [Trip.com] フォーム操作エラー: {e}")
            # フォールバック: 直接URLで試みる
            driver.get(
                "https://jp.trip.com/hotels/list"
                f"?checkin={CHECKIN}&checkout={CHECKOUT}"
                "&adult=2&children=0&rooms=1&curr=JPY&locale=ja-JP&sortorder=0"
            )
            time.sleep(8)

        driver.save_screenshot(f"{SCREENSHOT_DIR}/trip_com.png")
        print(f"  [Trip.com] スクリーンショット保存完了")

        selectors = [
            ".hotel-list-item",
            '[class*="HotelListItem"]',
            '[class*="hotel-item"]',
            '[class*="hotelItem"]',
        ]
        cards = []
        for sel in selectors:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                print(f"  [Trip.com] '{sel}' で {len(cards)} 件検出")
                break

        if not cards:
            print("  [Trip.com] ホテルカードが見つかりません（セレクター要確認）")

        for card in cards[:30]:
            try:
                name_el = card.find_element(
                    By.CSS_SELECTOR,
                    '[class*="hotel-name"], [class*="hotelName"], [class*="HotelName"], h2, h3'
                )
                price_el = card.find_element(
                    By.CSS_SELECTOR,
                    '[class*="price-int"], [class*="priceInt"], [class*="Price"], [class*="price"]'
                )

                name = name_el.text.strip()
                price = parse_price_jpy(price_el.text)
                if price is None:
                    continue

                try:
                    link_el = card.find_element(By.TAG_NAME, "a")
                    hotel_url = link_el.get_attribute("href") or ""
                except Exception:
                    hotel_url = ""

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

    except Exception as e:
        print(f"  [Trip.com] エラー: {e}")
    finally:
        driver.quit()

    return results


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== ホテル空室監視開始 {now} ===")
    print(f"場所: 釜山　チェックイン: {CHECKIN}　チェックアウト: {CHECKOUT}")
    print(f"予算: ¥{BUDGET_JPY:,} 以下\n")

    print("【Booking.com 確認中...】")
    booking = check_booking_com()

    print("\n【Trip.com 確認中...】")
    trip = check_trip_com()

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
    main()
