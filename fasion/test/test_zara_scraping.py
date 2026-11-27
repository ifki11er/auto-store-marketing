"""자라 사이트에서 상품 정보와 이미지를 수집한다."""

import csv
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fasion.main import load_env
from fasion.locale_settings import selected_locales


OUTPUT = Path(__file__).resolve().parent.parent / "scraping" / "zara"
PROFILE = OUTPUT / "browser_profile"
MARKER = "window.zara.viewPayload ="


def find_products(value):
    if isinstance(value, dict):
        if value.get("type") == "Product" and isinstance(value.get("detail"), dict):
            yield value
        for child in value.values():
            yield from find_products(child)
    elif isinstance(value, list):
        for child in value:
            yield from find_products(child)


def main():
    load_env()
    errors = []
    for country, settings in selected_locales():
        try:
            scrape_country(country, settings)
        except Exception as exc:
            errors.append(f"{country}: {exc}")
            print(f"언어별 수집 실패: {country} ({exc})", flush=True)
    if errors:
        raise RuntimeError("일부 언어 수집 실패: " + "; ".join(errors))


def scrape_country(country, settings):
    output = OUTPUT / country
    print(f"자라 홈 접속 중: {settings['url']}", flush=True)
    total_saved = 0
    with sync_playwright() as playwright:
        print("브라우저 실행 중...", flush=True)
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE / country), headless=False, locale=settings["locale"]
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            response = page.goto(settings["url"], wait_until="domcontentloaded", timeout=60000)
            title = page.title()
            if title.lower() == "access denied" or (response and response.status in (401, 403)):
                raise RuntimeError("자라 사이트가 브라우저의 접근을 거부했습니다. 상품 데이터를 가져올 수 없습니다.")
            print(f"페이지 접속 완료: {title}", flush=True)
            categories = page.locator('li[data-layout="products-category-view"] > a[href*="/woman-"]').evaluate_all(
                "elements => elements.map(a => ({name: a.innerText.trim() || a.textContent.trim(), url: a.href}))"
            )
            categories = list({item["url"]: item for item in categories}.values())
            if not categories:
                raise RuntimeError("홈 메뉴에서 여성 상품 카테고리를 찾지 못했습니다.")
            print(f"여성 상품 메뉴 {len(categories)}개 발견", flush=True)
            for index, category in enumerate(categories, 1):
                print(f"메뉴 수집 중 ({index}/{len(categories)}): {category['name']}", flush=True)
                category_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', category["name"]).strip(' .') or "category"
                category_output = output / category_name
                rows = {}
                csv_path = category_output / "products.csv"
                if csv_path.exists():
                    with csv_path.open(encoding="utf-8-sig", newline="") as file:
                        for row in csv.DictReader(file):
                            if row.get("image"):
                                rows[Path(row["image"]).stem] = row
                    print(f"기존 상품 {len(rows)}개 확인", flush=True)
                response = page.goto(category["url"], wait_until="domcontentloaded", timeout=60000)
                if page.title().lower() == "access denied" or (response and response.status in (401, 403)):
                    print(f"접근 거부로 수집 중단: {category['name']}", flush=True)
                    break
                html = page.content()
                position = html.find(MARKER)
                if position < 0:
                    print(f"상품 데이터 없음: {category['name']}", flush=True)
                    continue
                payload, _ = json.JSONDecoder().raw_decode(html[position + len(MARKER):].lstrip())
                visible_prices = page.locator('a.product-grid-product-info__name[href*="-p"]').evaluate_all("""links => Object.fromEntries(links.map(a => {
                    const id = (a.getAttribute('href') || '').match(/-p(\\d+)\\.html/);
                    const container = a.closest('.product-grid-product-info') || a.closest('li');
                    const amount = container && container.querySelector('.price-current data[data-currency]');
                    return id && amount ? [id[1], {amount: amount.value, currency: amount.dataset.currency}] : null;
                }).filter(Boolean))""")
                products = {}
                for product in find_products(payload):
                    product_id = str(product.get("id", ""))
                    if product_id and product_id.isdigit() and product_id not in rows and product.get("name") and product.get("price") is not None:
                        seo_id = str(product.get("seo", {}).get("seoProductId", ""))
                        price = visible_prices.get(seo_id)
                        if price and price.get("currency") == settings["currency"]:
                            product["display_price"] = price["amount"]
                        elif country == "kr":
                            product["display_price"] = product["price"]
                        else:
                            print(f"화면 가격 확인 불가: {product['name']} (건너뜀)", flush=True)
                            continue
                        products.setdefault(product_id, product)
                print(f"현재 메뉴 상품 {len(products)}개 처리 시작", flush=True)
                save_products(context, products, rows, category_output)
                total_saved += len(rows)
                print(f"메뉴 수집 완료: {len(rows)}개 저장 ({category_output})", flush=True)
            if not total_saved:
                raise RuntimeError("저장된 상품이 없습니다. 수집 및 이미지 다운로드 오류를 확인하세요.")
        finally:
            context.close()


def save_products(context, products, rows, category_output):
    if not products:
        return

    images = category_output / "images"
    images.mkdir(parents=True, exist_ok=True)
    image_page = context.new_page()
    for index, (product_id, product) in enumerate(products.items(), 1):
        name = product["name"]
        print(f"상품 처리 중 ({index}/{len(products)}): {name}", flush=True)
        image_url = None
        for color in product["detail"].get("colors", []):
            for media in color.get("xmedia", []):
                if media.get("type") == "image":
                    image_url = media.get("extraInfo", {}).get("deliveryUrl")
                    if image_url:
                        break
            if image_url:
                break
        if not image_url:
            print(f"이미지 주소 없음: {name}", flush=True)
            continue
        try:
            response = image_page.goto(image_url, wait_until="commit", timeout=30000)
            if response is None or not response.ok:
                status = response.status if response else "응답 없음"
                print(f"이미지 다운로드 실패: {name} (HTTP {status})", flush=True)
                continue
            if not response.headers.get("content-type", "").startswith("image/"):
                print(f"이미지 다운로드 실패: {name} (이미지 응답이 아님)", flush=True)
                continue
            image_data = response.body()
        except PlaywrightError as error:
            print(f"이미지 다운로드 실패: {name} ({error})", flush=True)
            continue
        image_path = images / f"{product_id}.jpg"
        image_path.write_bytes(image_data)
        row = {"name": name, "price": product["display_price"], "image": f"images/{image_path.name}"}
        with (category_output / "products.csv").open("a" if rows else "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["name", "price", "image"])
            if not rows:
                writer.writeheader()
            writer.writerow(row)
        rows[product_id] = row

    image_page.close()


if __name__ == "__main__":
    main()
