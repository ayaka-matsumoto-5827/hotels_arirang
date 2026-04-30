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
BUDGET_JPY = 30_000
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
        # 釜山のcity ID = 253 を直接指定
        url = (
            "https://jp.trip.com/hotels/list"
            "?city=253&cityName=Busan&countryId=42"
            f"&checkin={CHECKIN}&checkout={CHECKOUT}"
            "&adult=2&children=0&rooms=1"
            "&curr=JPY&locale=ja-JP&sortorder=1"
        )
        print(f"  [Trip.com] アクセス中...")
        driver.get(url)
        time.sleep(8)

        # スクロールして遅延ロードを発生させる
        for scroll_y in [300, 600, 1000, 1500]:
            driver.execute_script(f"window.scrollTo(0, {scroll_y})")
            time.sleep(1)
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(3)

        driver.save_screenshot(f"{SCREENSHOT_DIR}/trip_com.png")
        print(f"  [Trip.com] スクリーンショット保存完了")

        # A/Bテスト対応: 複数セレクターを試みる
        card_selectors = [
            ".list-item-versionb",
            ".compressmeta-hotel-wrap-v8",
            ".hotel-card",
        ]
        cards = []
        for sel in card_selectors:
            cards = driver.find_elements(By.CSS_SELECTOR, sel)
            if cards:
                print(f"  [Trip.com] '{sel}' で {len(cards)} 件のカードを検出")
                break

        if not cards:
            print("  [Trip.com] ホテルカードが見つかりません")

        # カードのinnerTextから直接ホテル名・価格・URLを抽出
        hotel_data = driver.execute_script("""
            var results = [];
            var seen = new Set();
            var cards = document.querySelectorAll(
                '.hotel-card, .list-item-versionb, .compressmeta-hotel-wrap-v8'
            );
            cards.forEach(function(card) {
                var lines = (card.innerText || '').split('\\n').map(function(l) { return l.trim(); }).filter(Boolean);
                if (lines.length === 0) return;
                var name = lines[0];
                if (seen.has(name)) return;
                seen.add(name);
                // 「円」を含む最後の行から価格を取得
                var priceText = '';
                for (var i = lines.length - 1; i >= 0; i--) {
                    if (lines[i].includes('円')) { priceText = lines[i]; break; }
                }
                var linkEl = card.querySelector('a[href*="hotel"], a[href*="hotels"]');
                results.push({
                    name: name,
                    price: priceText,
                    url: linkEl ? linkEl.href : ''
                });
            });
            return results.slice(0, 30);
        """)
        print(f"  [Trip.com] JS抽出: {len(hotel_data)} 件")

        for h in hotel_data:
            price = parse_price_jpy(h.get("price", ""))
            name = h.get("name", "").strip()
            if price is None or not name:
                continue
            if price <= BUDGET_JPY:
                print(f"    ✓ {name}: ¥{price:,}")
                results.append({
                    "site": "Trip.com",
                    "name": name,
                    "price": f"¥{price:,}",
                    "price_num": price,
                    "url": h.get("url", ""),
                })

    except Exception as e:
        print(f"  [Trip.com] エラー: {e}")
    finally:
        driver.quit()

    return results


# ---------------------------------------------------------------------------
# 東横INN釜山駅1（公式サイト直接）
# ---------------------------------------------------------------------------

KRW_TO_JPY = 0.11  # 1 KRW ≈ 0.11 JPY（固定レート）

def check_toyoko_inn() -> list[dict]:
    results = []
    try:
        headers = {"User-Agent": USER_AGENT}
        # buildIdをトップページから取得
        r = requests.get("https://www.toyoko-inn.com/", headers=headers, timeout=10)
        m = re.search(r'"buildId":"([^"]+)"', r.text)
        if not m:
            print("  [東横INN] buildId取得失敗")
            return results
        bid = m.group(1)

        url = (
            f"https://www.toyoko-inn.com/_next/data/{bid}/ja/search/result/room_plan.json"
            f"?hotel=00194&people=2&room=1&smoking=noSmoking&start={CHECKIN}&end={CHECKOUT}"
        )
        data = requests.get(url, headers=headers, timeout=15).json()
        plan = data["pageProps"]["planResponse"]

        if not plan.get("canReservation"):
            print("  [東横INN] 予約不可")
            return results

        for rt in plan.get("roomTypeList", []):
            for p in rt.get("plans", []):
                general_vacant = p.get("vacant", {}).get("generalVacantRoom", 0)
                member_vacant = p.get("vacant", {}).get("membershipVacantRoom", 0)
                if general_vacant == 0 and member_vacant == 0:
                    continue
                price_krw = p.get("price", {}).get("generalPrice", 0)
                price_jpy = int(price_krw * KRW_TO_JPY)
                room_name = rt.get("roomTypeName", "")
                plan_name = p.get("planName", "")
                print(f"    ✓ {room_name}({plan_name}): ₩{price_krw:,} ≈ ¥{price_jpy:,} 空室:{general_vacant}")
                results.append({
                    "site": "東横INN釜山駅1",
                    "name": f"東横INN釜山駅1 {room_name}（{plan_name}）",
                    "price": f"₩{price_krw:,}（≈¥{price_jpy:,}）",
                    "price_num": price_jpy,
                    "url": "https://www.toyoko-inn.com/search/result/room_plan/"
                           f"?hotel=00194&people=2&room=1&smoking=noSmoking&start={CHECKIN}&end={CHECKOUT}",
                })

        if not results:
            print("  [東横INN] 空室なし（全プラン満室）")
        else:
            print(f"  [東横INN] {len(results)} 件の空室あり")

    except Exception as e:
        print(f"  [東横INN] エラー: {e}")

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

    print("\n【東横INN釜山駅1 確認中...】")
    toyoko = check_toyoko_inn()

    all_hotels = booking + trip + toyoko
    print(f"\n=== 結果サマリー ===")
    print(f"Booking.com: {len(booking)} 件 / Trip.com: {len(trip)} 件 / 東横INN: {len(toyoko)} 件 / 合計: {len(all_hotels)} 件")

    if all_hotels:
        all_hotels.sort(key=lambda h: h["price_num"])
        send_discord_notification(all_hotels)
    else:
        print("予算内のホテルは見つかりませんでした")

    print(f"\n=== 監視完了 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    main()
