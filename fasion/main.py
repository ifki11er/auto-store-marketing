"""수집된 패션 상품으로 영상을 생성한다."""
import json
import os
import random
import shutil
import unicodedata
from datetime import datetime

from openai import OpenAI
from fasion.config import DATA, EXCLUDED_CATEGORIES, OUTPUT, WORKFLOWS, configured_bgm_files, configured_file, load_env
from fasion.gpt import plan
from fasion.locale_settings import selected_locales
from fasion.products import load_history, next_output_slot, save_history, select_products
from fasion.video import generate_clip, render
from marketing.upload_schedule import UploadSchedule


def main():
    load_env()
    locales = selected_locales()
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY가 필요합니다")
    stage = os.environ.get("FASION_STAGE", "").strip()
    bgm_files = configured_bgm_files()
    count = int(os.environ.get("FASION_CLOTHES_COUNT", "4"))
    seconds = int(os.environ.get("FASION_SECONDS_PER_CLOTHES", "5"))
    if count < 1 or seconds < 1:
        raise ValueError("옷 개수와 옷당 초수는 1 이상이어야 합니다")
    print(f"영상 설정: 옷 {count}개, 옷당 {seconds}초", flush=True)
    workflow_name = os.environ.get("VIDEO_WORKFLOW", "Deno_MiniMax_H3_R2V_Turbo_8plus2").strip()
    if workflow_name not in WORKFLOWS:
        raise ValueError(f"지원하지 않는 VIDEO_WORKFLOW: {workflow_name}")
    schedule = UploadSchedule.from_environment(os.environ)
    category_setting = os.environ.get("FASION_CATEGORY", "random").strip()
    client = OpenAI()
    errors = []
    for country, settings in locales:
        try:
            face = configured_file(f"FASION_FACE_{country.upper()}")
            categories = sorted(p.parent for p in DATA.glob(f"*/{country}/*/products.csv")
                                if unicodedata.normalize("NFC", p.parent.name).casefold() not in EXCLUDED_CATEGORIES)
            if not categories:
                raise ValueError(f"{country} 상품 CSV가 없습니다: {DATA}")
            if category_setting.lower() != "random":
                requested = category_setting.replace("\\", "/")
                categories = [category for category in categories if f"{category.parent.parent.name}/{category.name}" == requested or category.relative_to(DATA).as_posix() == requested]
                if not categories:
                    raise ValueError(f"{country} 카테고리 폴더를 찾을 수 없습니다: {requested}")
            while True:
                slot = next_output_slot(schedule, country)
                if slot is None:
                    print(f"{country}: 설정한 기간의 영상 공개 시간 슬롯이 모두 채워졌습니다.", flush=True)
                    break
                print(f"{country} 영상 생성 시작: 공개 예정 {slot:%Y-%m-%d %H:%M}", flush=True)
                bgm = random.choice(bgm_files)
                print(f"배경 음악 선택: {bgm.name}", flush=True)
                generate_one(client, categories, slot, face, stage, bgm, count, seconds, workflow_name, country, settings)
        except Exception as exc:
            errors.append(f"{country}: {exc}")
            print(f"언어별 영상 생성 실패: {country} ({exc})", flush=True)
    if errors:
        raise RuntimeError("일부 언어 영상 생성 실패: " + "; ".join(errors))


def generate_one(client, categories, slot, face, stage, bgm, count, seconds, workflow_name, country, settings):
    category = random.choice(categories)
    brand = category.parent.parent.name
    print(f"선택 카테고리: {brand} / {country} / {category.name}", flush=True)
    history = load_history()
    job = OUTPUT / "작업중" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    job.mkdir(parents=True)
    print(f"GPT 기록 저장 위치: {job}", flush=True)
    products = select_products(client, category, count, history, job, country, seconds)
    metadata = plan(client, brand, category.name, products, job, seconds, country, settings)
    (job / "03_metadata.json").write_text(json.dumps({**metadata, "brand": brand, "country": country, "currency": settings["currency"], "category": category.name, "influencer_image": str(face), "bgm": str(bgm), "products": products}, ensure_ascii=False, indent=2), encoding="utf-8")
    base = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").strip().rstrip("/")
    for index, product in enumerate(products):
        print(f"런웨이 생성 중 ({index + 1}/{count}): {product['name']}", flush=True)
        scene = product["scene"]
        if scene["use_runway_reference"]:
            if not stage:
                raise ValueError("실내 런웨이 장면에는 .env의 FASION_STAGE가 필요합니다")
            references = [face, configured_file("FASION_STAGE"), job / product["reference_image"]]
        else:
            references = [face, job / product["reference_image"]]
        print(f"배경: {scene['background']}, 동작: {scene['action']}", flush=True)
        generate_clip(base, references, seconds, job / f"04_raw_{index}.mp4", workflow_name, scene)
    print("영상과 BGM 합성 중", flush=True)
    render(job, products, metadata["video_title"], bgm, seconds, metadata["display_names"], country)
    destination = OUTPUT / country / slot.strftime("%Y-%m-%d") / slot.strftime("%H%M")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"결과 폴더가 이미 있습니다: {destination}")
    shutil.move(str(job), str(destination))
    job = destination
    save_history(history, brand, category.name, products, job, country)
    print(f"영상 생성 완료: {job / '07_final.mp4'}", flush=True)


if __name__ == "__main__":
    main()
