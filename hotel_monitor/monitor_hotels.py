#!/usr/bin/env python3
"""
釜山ホテル空室監視スクリプト
Booking.com / Trip.com / 東横INN を監視し、予算内のホテルが見つかったらDiscordに通知する
"""

import json
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
DATE_RANGES = [
    ("2026-06-11", "2026-06-12"),
    ("2026-06-12", "2026-06-13"),
    ("2026-06-13", "2026-06-14"),
]
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
                f"**日程**: {h['checkin']} チェックイン\n"
                f"**金額**: {h['price']}\n"
                f"**URL**: {h.get('url', 'N/A')}"
            ),
            "color": 0x00FF88,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    payload = {
        "content": (
            f"💡 **釜山ホテル空室情報**\n"
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

def check_booking_com(checkin: str, checkout: str) -> list[dict]:
    results = []
    driver = make_driver()

    try:
        url = (
            "https://www.booking.com/searchresults.ja.html"
            "?ss=Busan%2C+South+Korea"
            f"&checkin={checkin}&checkout={checkout}"
            "&group_adults=2&no_rooms=1"
            "&order=price"
            "&selected_currency=JPY"
        )
        print(f"  [Booking.com] アクセス中...")
        driver.get(url)
        time.sleep(8)

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

        driver.save_screenshot(f"{SCREENSHOT_DIR}/booking_com_{checkin}.png")

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
                        "checkin": checkin,
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

def check_trip_com(checkin: str, checkout: str) -> list[dict]:
    results = []
    driver = make_driver()

    try:
        url = (
            "https://jp.trip.com/hotels/list"
            "?city=253&cityName=Busan&countryId=42"
            f"&checkin={checkin}&checkout={checkout}"
            "&adult=2&children=0&rooms=1"
            "&curr=JPY&locale=ja-JP&sortorder=1"
        )
        print(f"  [Trip.com] アクセス中...")
        driver.get(url)
        time.sleep(8)

        for scroll_y in [300, 600, 1000, 1500]:
            driver.execute_script(f"window.scrollTo(0, {scroll_y})")
            time.sleep(1)
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(3)

        driver.save_screenshot(f"{SCREENSHOT_DIR}/trip_com_{checkin}.png")

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
                    "checkin": checkin,
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

TOYOKO_INN_HOTELS = [
    "00194",
    "00221",
    "00178",
    "00256",
]

def _check_toyoko_inn_one(bid: str, hotel_code: str, checkin: str, checkout: str) -> list[dict]:
    results = []
    headers = {"User-Agent": USER_AGENT}
    url = (
        f"https://www.toyoko-inn.com/_next/data/{bid}/ja/search/result/room_plan.json"
        f"?hotel={hotel_code}&people=2&room=1&smoking=noSmoking&start={checkin}&end={checkout}"
    )
    data = requests.get(url, headers=headers, timeout=15).json()
    plan = data["pageProps"]["planResponse"]
    hotel_title = plan.get("hotelTitle", f"東横INN({hotel_code})")

    if not plan.get("canReservation"):
        print(f"    [{hotel_title}] 予約不可")
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
            print(f"    ✓ [{hotel_title}] {room_name}({plan_name}): ₩{price_krw:,} ≈ ¥{price_jpy:,} 空室:{general_vacant}")
            results.append({
                "site": "東横INN",
                "name": f"{hotel_title} {room_name}（{plan_name}）",
                "checkin": checkin,
                "price": f"₩{price_krw:,}（≈¥{price_jpy:,}）",
                "price_num": price_jpy,
                "url": (
                    "https://www.toyoko-inn.com/search/result/room_plan/"
                    f"?hotel={hotel_code}&people=2&room=1&smoking=noSmoking&start={checkin}&end={checkout}"
                ),
            })

    if not results:
        print(f"    [{hotel_title}] 空室なし")
    return results


def check_toyoko_inn(checkin: str, checkout: str) -> list[dict]:
    results = []
    try:
        headers = {"User-Agent": USER_AGENT}
        r = requests.get("https://www.toyoko-inn.com/", headers=headers, timeout=10)
        m = re.search(r'"buildId":"([^"]+)"', r.text)
        if not m:
            print("  [東横INN] buildId取得失敗")
            return results
        bid = m.group(1)

        for hotel_code in TOYOKO_INN_HOTELS:
            try:
                results += _check_toyoko_inn_one(bid, hotel_code, checkin, checkout)
            except Exception as e:
                print(f"    [東横INN {hotel_code}] エラー: {e}")

        print(f"  [東横INN] 合計 {len(results)} 件の空室あり" if results else "  [東横INN] 全ホテル満室")

    except Exception as e:
        print(f"  [東横INN] エラー: {e}")

    return results


# ---------------------------------------------------------------------------
# Solaria Nishitetsu Hotel Busan（公式直販サイト）
# ---------------------------------------------------------------------------

SOLARIA_CODE = "d368e5b5-6868-4d64-8372-a91d5547031c"

def check_solaria_busan(checkin: str, checkout: str) -> list[dict]:
    results = []
    driver = make_driver()
    try:
        ci = checkin.replace("-", "/").replace("/", "%2F")
        co = checkout.replace("-", "/").replace("/", "%2F")
        url = (
            f"https://booking-kr.nnr-h.com/booking/result"
            f"?code={SOLARIA_CODE}&checkin={ci}&checkout={co}"
            "&type=rooms&is_day_use=false&order=price_low_to_high"
            "&is_including_occupied=false&rooms=%5B%7B%22adults%22%3A2%7D%5D"
        )
        driver.get(url)
        time.sleep(12)

        text = driver.find_element(By.TAG_NAME, "body").text

        if "空室が見つかりませんでした" in text:
            print("  [Solaria Busan] 空室なし（満室）")
            return results

        # 通常価格を抽出: "通常価格\n₩ 168,740 1泊の料金" のパターン
        prices_krw = re.findall(r"通常価格\s*\n?₩\s*([\d,]+)", text)
        # 部屋タイプを抽出（検索結果の後、最初の「客室構造」の前のテキスト）
        room_types = re.findall(r"^(.+)\n客室構造", text, re.MULTILINE)

        if not prices_krw:
            # ₩が見つからない場合はページテキストだけで判断
            print(f"  [Solaria Busan] 空室あり（価格取得失敗）")
            results.append({
                "site": "Solaria Busan",
                "name": "Solaria Nishitetsu Hotel Busan",
                "checkin": checkin,
                "price": "要確認",
                "price_num": 0,
                "url": url.replace("%2F", "/"),
            })
            return results

        booking_url = url.replace("%2F", "/")
        seen_prices = set()
        for i, price_str in enumerate(prices_krw):
            price_krw = int(price_str.replace(",", ""))
            if price_krw in seen_prices:
                continue
            seen_prices.add(price_krw)
            price_jpy = int(price_krw * KRW_TO_JPY)
            room_name = room_types[i] if i < len(room_types) else "客室"
            if price_jpy <= BUDGET_JPY:
                print(f"    ✓ {room_name}: ₩{price_krw:,} ≈ ¥{price_jpy:,}")
                results.append({
                    "site": "Solaria Busan",
                    "name": f"Solaria Nishitetsu Hotel Busan {room_name}",
                    "checkin": checkin,
                    "price": f"₩{price_krw:,}（≈¥{price_jpy:,}）",
                    "price_num": price_jpy,
                    "url": booking_url,
                })

        if not results:
            print(f"  [Solaria Busan] 空室あり（最安値₩{min(int(p.replace(',','')) for p in prices_krw):,}、予算超過）")
        else:
            print(f"  [Solaria Busan] {len(results)} 件の空室あり")

    except Exception as e:
        print(f"  [Solaria Busan] エラー: {e}")
    finally:
        driver.quit()

    return results


# ---------------------------------------------------------------------------
# Haeundae Hound Hotel Signature（公式直販サイト）
# ---------------------------------------------------------------------------

HOUND_SESSION_OBJ = {
    "SS_PMS_SEQ_NO": "461",
    "SS_PMS_CODE": "HHD1",
    "SS_MEMB_SEQ_NO": "",
    "SS_MEMB_MASTER_NO": "",
    "SS_MEMB_LASTNAME": "",
    "SS_MEMB_FIRSTNAME": "",
    "SS_MEMB_EMAIL": "",
    "SS_MEMB_TEL": "",
    "SS_LANG_TYPE": "KO",
    "SS_REMOTE_IP": "",
    "SS_LOGIN_TYPE": "",
    "SS_SNS_NAVER_CLIENT_ID": "hayDtzmpoiuhJl1srBnV",
    "SS_SNS_NAVER_CLIENT_SECRET": "iuzEyiZE8y",
    "SS_SNS_NAVER_RETURN_HOST": "https://be4.wingsbooking.com",
    "SS_OPERATION_MODE": "prod",
    "SS_PRIVACY_HOTEL": "false",
    "SS_CURRENCY_TYPE": "KRW",
    "SS_MEMBERSHIP_SEQ_NO": "",
    "SS_MEMBERSHIP_TYPE": "",
    "SS_MEMBERSHIP_POINT_TYPE": "",
    "SS_MEMBERSHIP_COUP_CNT": "",
    "SS_MEMBERSHIP_COUP_PRICE": "",
    "SS_MEMBERSHIP_POINT_PRICE": "",
    "SS_EXT_CHANNEL_SEQ_NO": "",
    "SS_ARRIVAL_TIME_FLAG": "N",
    "SS_ARRIVAL_TIME_START": "",
    "SS_ARRIVAL_TIME_END": "",
    "SS_USE_LANG_TYPE": "KO|EN",
}

def _hound_make_param(params: dict) -> dict:
    merged = {**params, **HOUND_SESSION_OBJ}
    return {"parameter": json.dumps(merged)}


def check_hound_hotel(checkin: str, checkout: str) -> list[dict]:
    results = []
    try:
        ua = USER_AGENT
        session = requests.Session()
        session.headers["User-Agent"] = ua

        # 1) Establish JSESSIONID
        session.get("https://be4.wingsbooking.com/HHD1", timeout=15)

        # 2) Load roomSelect so the server stores the date params in session
        session.get(
            "https://be4.wingsbooking.com/HHD1/roomSelect",
            params={
                "check_in": checkin,
                "check_out": checkout,
                "rooms": "1",
                "adult": "2",
                "children": "0",
                "channel_code": "WINGS_B2C",
            },
            timeout=15,
        )

        # 3) Call roomList API
        session.headers.update({
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": (
                f"https://be4.wingsbooking.com/HHD1/roomSelect"
                f"?check_in={checkin}&check_out={checkout}"
                "&rooms=1&adult=2&children=0&channel_code=WINGS_B2C"
            ),
        })
        resp = session.post(
            "https://be4.wingsbooking.com/HHD1/user/hotel/roomList",
            data=_hound_make_param({
                "pms_seq_no": "461",
                "check_in": checkin,
                "check_out": checkout,
                "rooms": "1",
                "adult": "2",
                "children": "0",
                "channel_code": "WINGS_B2C",
                "lang_type": "KO",
                "prm_seq_no": "",
                "cpny_seq_no": "",
                "mmbrs_seq_no": "",
                "ext_channel_seq_no": "",
            }),
            timeout=15,
        )
        rooms = resp.json().get("result", [])

        if not rooms:
            print("  [Hound Hotel] 空室なし")
            return results

        booking_url = (
            f"https://be4.wingsbooking.com/HHD1/roomSelect"
            f"?check_in={checkin}&check_out={checkout}"
            "&rooms=1&adult=2&children=0&channel_code=WINGS_B2C"
        )
        seen = set()
        for room in rooms:
            room_name = room.get("room_name", "客室")
            daily = room.get("daily_rate", [])
            price_krw = int(daily[0]["day_rate"]) if daily else int(room.get("basic_rate", 0))
            if price_krw in seen:
                continue
            seen.add(price_krw)
            price_jpy = int(price_krw * KRW_TO_JPY)
            if price_jpy <= BUDGET_JPY:
                print(f"    ✓ {room_name}: ₩{price_krw:,} ≈ ¥{price_jpy:,}")
                results.append({
                    "site": "Hound Hotel Signature",
                    "name": f"Haeundae Hound Hotel Signature {room_name}",
                    "checkin": checkin,
                    "price": f"₩{price_krw:,}（≈¥{price_jpy:,}）",
                    "price_num": price_jpy,
                    "url": booking_url,
                })

        if not results:
            min_krw = min(int((r.get("daily_rate") or [{}])[0].get("day_rate", 0)) for r in rooms)
            print(f"  [Hound Hotel] 空室あり（最安値₩{min_krw:,}、予算超過）")
        else:
            print(f"  [Hound Hotel] {len(results)} 件の空室あり")

    except Exception as e:
        print(f"  [Hound Hotel] エラー: {e}")

    return results


# ---------------------------------------------------------------------------
# Ramada Encore by Wyndham Busan Station（hotelcheckins.com）
# ---------------------------------------------------------------------------

RAMADA_HOTEL_ID = 48848742

def check_ramada_busan(checkin: str, checkout: str) -> list[dict]:
    results = []
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://ramada-encore-wyndham-busan-station.hotelcheckins.com",
            "Referer": "https://ramada-encore-wyndham-busan-station.hotelcheckins.com/",
        }
        body = {
            "searchRequest": {
                "dates": {"checkIn": checkin, "checkOut": checkout},
                "guests": {"adults": 2, "childrenAge": [], "rooms": 1},
            },
            "hotelId": RAMADA_HOTEL_ID,
            "internalSuppliers": False,
        }
        api_url = (
            f"https://api.hotelcheckins.com/hotel-search/availability"
            f"?destinationId={RAMADA_HOTEL_ID}&destinationType=3"
            f"&checkIn={checkin}&checkOut={checkout}&adults=2&rooms=1"
        )
        resp = requests.post(api_url, headers=headers, json=body, timeout=20)
        room_types = resp.json().get("roomTypes", [])

        if not room_types:
            print("  [Ramada Busan] 空室なし")
            return results

        booking_url = (
            f"https://ramada-encore-wyndham-busan-station.hotelcheckins.com/ja/reservation"
            f"?destinationId={RAMADA_HOTEL_ID}&destinationType=3"
            f"&checkIn={checkin}&checkOut={checkout}&adults=2&rooms=1"
        )
        seen = set()
        for rt in room_types:
            if rt.get("needsSignIn"):
                continue
            room_name = rt.get("room", {}).get("title", "客室")
            plans = rt.get("roomRate", {}).get("roomPlans", {})
            for plan in plans.values():
                pricing = plan.get("perRoomPricing", {})
                price_jpy = pricing.get("chargeTotal", {}).get("amount")
                if price_jpy is None:
                    price_jpy = pricing.get("allInclusivePrice", {}).get("amount", 0)
                price_jpy = int(price_jpy)
                key = (room_name, price_jpy)
                if key in seen:
                    continue
                seen.add(key)
                if price_jpy <= BUDGET_JPY:
                    print(f"    ✓ {room_name}: ¥{price_jpy:,}")
                    results.append({
                        "site": "Ramada Busan (hotelcheckins)",
                        "name": f"Ramada Encore by Wyndham Busan Station {room_name}",
                        "checkin": checkin,
                        "price": f"¥{price_jpy:,}",
                        "price_num": price_jpy,
                        "url": booking_url,
                    })

        if not results:
            # 最安値を報告
            all_prices = []
            for rt in room_types:
                for plan in rt.get("roomRate", {}).get("roomPlans", {}).values():
                    p = plan.get("perRoomPricing", {}).get("chargeTotal", {}).get("amount")
                    if p:
                        all_prices.append(int(p))
            min_price = min(all_prices) if all_prices else 0
            print(f"  [Ramada Busan] 空室あり（最安値¥{min_price:,}、予算超過）")
        else:
            print(f"  [Ramada Busan] {len(results)} 件の空室あり")

    except Exception as e:
        print(f"  [Ramada Busan] エラー: {e}")

    return results


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== ホテル空室監視開始 {now} ===")
    print(f"予算: ¥{BUDGET_JPY:,} 以下\n")

    all_hotels = []

    for checkin, checkout in DATE_RANGES:
        print(f"--- {checkin} チェックイン ---")

        print("  【Booking.com】")
        all_hotels += check_booking_com(checkin, checkout)

        print("  【Trip.com】")
        all_hotels += check_trip_com(checkin, checkout)

        print("  【東横INN釜山駅1】")
        all_hotels += check_toyoko_inn(checkin, checkout)

        print("  【Solaria Nishitetsu Hotel Busan】")
        all_hotels += check_solaria_busan(checkin, checkout)

        print("  【Haeundae Hound Hotel Signature】")
        all_hotels += check_hound_hotel(checkin, checkout)

        print("  【Ramada Encore by Wyndham Busan Station】")
        all_hotels += check_ramada_busan(checkin, checkout)
        print()

    print(f"=== 結果サマリー ===")
    print(f"合計: {len(all_hotels)} 件")

    if all_hotels:
        all_hotels.sort(key=lambda h: (h["checkin"], h["price_num"]))
        send_discord_notification(all_hotels)
    else:
        print("予算内のホテルは見つかりませんでした")

    print(f"\n=== 監視完了 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    main()
