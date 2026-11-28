"""상품 선택과 사용 이력, 영상 공개 시간 관리."""
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from fasion.config import DATA, HISTORY, OUTPUT, ROOT
from fasion.gpt import ask, image_input
from fasion.prompts import analysis_prompt

def prepare_clothing_image(path, job):
    executable = ".exe" if os.name == "nt" else ""
    probe = ROOT / "bin" / f"ffprobe{executable}"
    ffmpeg = ROOT / "bin" / f"ffmpeg{executable}"
    result = subprocess.run([str(probe), "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=width,height", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True)
    streams = json.loads(result.stdout).get("streams", [])
    if not streams or not streams[0].get("width") or not streams[0].get("height"):
        raise ValueError(f"사진 크기를 확인할 수 없습니다: {path}")
    width, height = streams[0]["width"], streams[0]["height"]
    if max(width, height) <= 1080:
        return path
    folder = job / "01_resized_images"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.stem}.png"
    print(f"옷 사진 크기 조정: {width}x{height} → 최대 1080 ({path.name})", flush=True)
    subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", "-i", str(path), "-vf",
                    "scale=1080:1080:force_original_aspect_ratio=decrease", "-frames:v", "1",
                    "-update", "1", str(target)], check=True)
    return target


def product_key(brand, image, country="kr"):
    prefix = "" if country == "kr" else f"{country}/"
    return f"{prefix}{brand.casefold()}/{Path(image).stem.casefold()}"


def product_group_key(brand, country, name, price):
    normalized_name = " ".join(unicodedata.normalize("NFC", name).casefold().split())
    normalized_price = str(price).strip().replace(",", "")
    value = json.dumps([brand.casefold(), country, normalized_name, normalized_price], ensure_ascii=False)
    return "product_group/" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_history():
    if not HISTORY.exists():
        return {}
    history = json.loads(HISTORY.read_text(encoding="utf-8"))
    if not isinstance(history, dict):
        raise ValueError(f"상품 이력 형식이 올바르지 않습니다: {HISTORY}")
    return history


def next_output_slot(schedule, country):
    allocated = []
    day = schedule.start_date
    while day <= schedule.end_date:
        for clock in schedule.publish_times:
            matches = False
            for folder in (OUTPUT / country / day.isoformat() / clock.strftime("%H%M"), OUTPUT / day.isoformat() / clock.strftime("%H%M")):
              for pattern in ("07_final.mp4", "final.mp4", "*/07_final.mp4", "*/final.mp4"):
                for video in folder.glob(pattern) if folder.is_dir() else []:
                    if video.parent.name.startswith("08_재생성_백업_"):
                        continue
                    if not video.is_file() or not video.stat().st_size:
                        continue
                    metadata = video.parent / "03_metadata.json"
                    if not metadata.is_file():
                        metadata = video.parent / "metadata.json"
                    if not metadata.is_file():
                        raise FileNotFoundError(f"영상의 언어 정보 파일이 없습니다: {video.parent}")
                    data = json.loads(metadata.read_text(encoding="utf-8-sig"))
                    if data.get("country", "kr") == country:
                        matches = True
                        break
                if matches:
                    break
              if matches:
                  break
            if matches:
                from datetime import datetime as dt
                from marketing.upload_schedule import KOREA_TIME
                allocated.append(dt.combine(day, clock, KOREA_TIME))
        day += timedelta(days=1)
    slot = schedule.next_planned_publication(allocated)
    return slot


def save_history(history, brand, category, products, job, country):
    selected = {product_group_key(brand, country, product["name"], product["price"]) for product in products}
    for csv_path in (DATA / brand / country).glob("*/products.csv"):
        with csv_path.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                name, price, image = row.get("name", ""), row.get("price", ""), row.get("image", "")
                if name and price and image and product_group_key(brand, country, name, price) in selected:
                    history[product_key(brand, image, country)] = True
    for product in products:
        history[product_key(brand, product["image"], country)] = True
    write_history(history)


def write_history(history):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    temporary = HISTORY.with_suffix(".tmp")
    temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(HISTORY)


def select_products(client, category, count, history, job, country, seconds):
    with (category / "products.csv").open(encoding="utf-8-sig", newline="") as file:
        candidates = list(csv.DictReader(file))
    random.shuffle(candidates)
    chosen = []
    selected_keys = set()
    attempts = 0
    for item in candidates:
        path = (category / item.get("image", "")).resolve()
        if not path.is_relative_to(category.resolve()) or not path.is_file():
            continue
        brand = category.parent.parent.name
        key = product_key(brand, path, country)
        name, price = item.get("name", ""), item.get("price", "")
        if not name or not price:
            continue
        group_key = product_group_key(brand, country, name, price)
        legacy_used = any(
            isinstance(entry, dict) and entry.get("video") and entry.get("brand", "").casefold() == brand.casefold()
            and entry.get("country", "kr") == country and " ".join(str(entry.get("name", "")).casefold().split()) == " ".join(name.casefold().split())
            and (not entry.get("price") or str(entry["price"]).strip().replace(",", "") == str(price).strip().replace(",", ""))
            for entry in history.values()
        )
        image_prefix = "invalid_image/v3/" if country == "kr" else f"invalid_image/v3/{country}/"
        image_key = image_prefix + hashlib.sha256(path.read_bytes()).hexdigest()
        non_clothing_key = "non_clothing/" + group_key
        if key in history or group_key in history or non_clothing_key in history or legacy_used or group_key in selected_keys or image_key in history:
            continue
        attempts += 1
        print(f"상품 사진 확인: {item.get('name', '')}", flush=True)
        reference = prepare_clothing_image(path, job)
        (job / f"01_gpt_analysis_{attempts:03d}_product.json").write_text(
            json.dumps({"name": item.get("name", ""), "image": str(path)}, ensure_ascii=False, indent=2), encoding="utf-8")
        context = {"product_name": item.get("name", ""), "seconds": seconds}
        result = ask(client, [{"type": "input_text", "text": analysis_prompt() + "\n" + json.dumps(context, ensure_ascii=False)}, image_input(reference)], job, f"01_gpt_analysis_{attempts:03d}")
        if not isinstance(result.get("usable"), bool):
            raise ValueError(f"GPT 사진 분석 결과에 사용 여부가 없습니다: {path.name}")
        if result["usable"]:
            background, action, accessories = result.get("background"), result.get("action"), result.get("accessories", "")
            if (not isinstance(background, str) or not background.strip() or len(background) > 300 or any(c in background for c in "<>{}")
                    or not isinstance(action, str) or not action.strip()
                    or len(action) > 300 or any(c in action for c in "<>{}")
                    or not isinstance(accessories, str) or len(accessories) > 300 or any(c in accessories for c in "<>{}")):
                raise ValueError(f"GPT 장면 연출 정보가 올바르지 않습니다: {path.name}")
            use_runway = bool(re.search(r"\b(?:indoor|interior)\b", background, re.IGNORECASE)
                              and re.search(r"\b(?:runway|catwalk)\b", background, re.IGNORECASE))
            scene = {"background": background.strip(), "use_runway_reference": use_runway, "action": action.strip(), "accessories": accessories.strip()}
            chosen.append({"name": item.get("name", ""), "price": item.get("price", ""), "image": str(path),
                           "scene": scene,
                           "reference_image": str(reference.relative_to(job)) if reference != path else str(path)})
            selected_keys.add(group_key)
            if len(chosen) == count:
                return chosen
        else:
            history[image_key] = True
            write_history(history)
            print(f"사용 불가 사진 이력 저장: {path.name}", flush=True)
    raise RuntimeError(f"{category.name}: 사용하지 않은 적합한 상품이 {len(chosen)}개뿐입니다. {count}개가 필요합니다.")
