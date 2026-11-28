"""수집된 패션 상품으로 런웨이 영상을 생성한다."""
import base64
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from datetime import timedelta

import requests
from openai import OpenAI
from fasion.prompts import analysis_prompt, minimax_prompt, planning_prompt
from fasion.locale_settings import selected_locales
from marketing.token_usage import record_token_usage
from marketing.upload_schedule import UploadSchedule

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "fasion" / "scraping"
HISTORY = ROOT / "fasion" / "output" / "history.json"
OUTPUT = ROOT / "fasion" / "output"
EXCLUDED_CATEGORIES = {"향수", "perfumes", "nước hoa"}
WORKFLOWS = {
    "Deno_MiniMax_H3_R2V_Turbo_8plus2": ("235", "186", "text", "144", "gifs"),
    "Deno_minimax_final_R2V_7plus1_memopt_Sol": ("214", "186", "text", "144", "gifs"),
    "video_fasth3_ominiref_r2v": ("136", "138", "value", "92", "images"),
}


def load_env():
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.isidentifier():
            value = value.strip().strip("\"'")
            if key in ("FASION_CLOTHES_COUNT", "FASION_SECONDS_PER_CLOTHES"):
                os.environ[key] = value
            else:
                os.environ.setdefault(key, value)


def configured_file(key):
    value = os.environ.get(key, "").strip()
    if not value:
        raise ValueError(f".env에 {key}를 설정하세요")
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"{key} 파일이 없습니다: {path}")
    return path.resolve()


def configured_bgm_files():
    raw = os.environ.get("FASION_BGM", "").strip()
    if not raw:
        raise ValueError(".env에 FASION_BGM을 설정하세요")
    if raw.startswith("["):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("FASION_BGM에 음악 파일 경로를 쉼표로 구분해 입력하세요") from exc
    else:
        values = raw.split(",")
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("FASION_BGM에 음악 파일 경로를 하나 이상 설정하세요")
    files = []
    for value in values:
        path = Path(value.strip())
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise FileNotFoundError(f"FASION_BGM 파일이 없습니다: {path}")
        files.append(path.resolve())
    return files


def image_input(path):
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise ValueError(f"지원하지 않는 이미지 형식: {path}")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{data}", "detail": "auto"}


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


def ask(client, content, job, stage):
    request = {"model": "gpt-6-luna", "input": [{"role": "user", "content": content}],
               "text": {"format": {"type": "json_object"}}}
    (job / f"{stage}_request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    result = client.responses.create(**request)
    (job / f"{stage}_response.json").write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    record_token_usage(job, "analysis" if stage.startswith("01_gpt_analysis_") else "plan", result)
    if not result.output_text:
        raise RuntimeError("GPT 응답에 텍스트가 없습니다")
    return json.loads(result.output_text)


def product_key(brand, image, country="kr"):
    prefix = "" if country == "kr" else f"{country}/"
    return f"{prefix}{brand.casefold()}/{Path(image).stem.casefold()}"


def product_group_key(brand, country, name, price):
    normalized_name = " ".join(unicodedata.normalize("NFC", name).casefold().split())
    normalized_price = str(price).strip().replace(",", "")
    value = json.dumps([brand.casefold(), country, normalized_name, normalized_price], ensure_ascii=False)
    return "product_group/" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def formatted_price(value, country):
    text = str(value).strip()
    if country == "en":
        if not re.fullmatch(r"(?:\$\s*)?\d[\d,]*(?:\.\d{1,2})?", text):
            raise ValueError(f"상품 가격 형식이 올바르지 않습니다: {value}")
        try:
            amount = Decimal(text.replace("$", "").replace(",", "").strip())
        except InvalidOperation as exc:
            raise ValueError(f"상품 가격 형식이 올바르지 않습니다: {value}") from exc
        formatted = f"{amount:,.2f}"
        return "$" + (formatted[:-3] if formatted.endswith(".00") else formatted)
    if not re.fullmatch(r"(?:₩\s*)?\d[\d,]*(?:\s*원)?", text):
        raise ValueError(f"상품 가격 형식이 올바르지 않습니다: {value}")
    digits = re.sub(r"[^0-9]", "", text)
    amount = f"{int(digits):,}"
    return f"{amount}원" if country == "kr" else f"{amount} ₫"


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
    for product in products:
        entry = {
            "brand": brand, "country": country, "category": category, "name": product["name"],
            "price": product["price"], "image": product["image"], "video": str(job / "07_final.mp4"),
            "scene": product.get("scene"),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        history[product_key(brand, product["image"], country)] = entry
        history[product_group_key(brand, country, product["name"], product["price"])] = entry
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
            background, action = result.get("background"), result.get("action")
            if (not isinstance(background, str) or not background.strip() or len(background) > 300 or any(c in background for c in "<>{}")
                    or not isinstance(action, str) or not action.strip()
                    or len(action) > 300 or any(c in action for c in "<>{}")):
                raise ValueError(f"GPT 장면 연출 정보가 올바르지 않습니다: {path.name}")
            use_runway = bool(re.search(r"\b(?:indoor|interior)\b", background, re.IGNORECASE)
                              and re.search(r"\b(?:runway|catwalk)\b", background, re.IGNORECASE))
            scene = {"background": background.strip(), "use_runway_reference": use_runway, "action": action.strip()}
            chosen.append({"name": item.get("name", ""), "price": item.get("price", ""), "image": str(path),
                           "scene": scene,
                           "reference_image": str(reference.relative_to(job)) if reference != path else str(path)})
            selected_keys.add(group_key)
            if len(chosen) == count:
                return chosen
        else:
            history[image_key] = {
                "status": "unusable", "brand": brand, "country": country, "category": category.name,
                "name": item.get("name", ""), "image": str(path),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            write_history(history)
            print(f"사용 불가 사진 이력 저장: {path.name}", flush=True)
    raise RuntimeError(f"{category.name}: 사용하지 않은 적합한 상품이 {len(chosen)}개뿐입니다. {count}개가 필요합니다.")


def plan(client, brand, category, products, job, seconds, country, settings):
    prompt = planning_prompt(brand, category, products, seconds, settings["language"], country)
    title_limit = 10
    name_limit = 20 if country == "kr" else 24

    def valid_lines(value, limit):
        if not isinstance(value, str) or "\r" in value:
            return False
        lines = value.split("\n")
        return 1 <= len(lines) <= 2 and all(line.strip() and len(line.strip()) <= limit for line in lines)

    for attempt in range(1, 4):
        stage = "02_gpt_plan" if attempt == 1 else f"02_gpt_plan_{attempt}"
        result = ask(client, [{"type": "input_text", "text": prompt}], job, stage)
        names = result.get("display_names")
        valid_names = (isinstance(names, list) and len(names) == len(products) and
                       all(valid_lines(name, name_limit) and
                           (len(product["name"]) > name_limit * 2 or
                            "".join(name.split()) == "".join(product["name"].split()))
                           for product, name in zip(products, names)))
        if (valid_lines(result.get("video_title"), title_limit)
                and brand.casefold() in result["video_title"].casefold() and valid_names):
            result["video_title"] = re.sub(re.escape(brand), lambda match: match.group().upper(), result["video_title"].strip(), flags=re.IGNORECASE)
            if not valid_lines(result["video_title"], title_limit):
                continue
            result["display_names"] = ["\n".join(line.strip() for line in name.split("\n")) for name in names]
            break
    else:
        raise ValueError(f"GPT가 브랜드명을 포함한 줄당 {title_limit}자 이내 제목과 줄당 {name_limit}자 이내 상품명을 최대 두 줄로 생성하지 못했습니다")
    if not all(isinstance(result.get(k), str) and result[k].strip() for k in ("video_title", "youtube_title", "description")) or not isinstance(result.get("tags"), list):
        raise ValueError("GPT 영상 기획 응답 형식이 올바르지 않습니다")
    result["description"] += "\n\n" + "\n".join(f"{p['name']} | {formatted_price(p['price'], country)}" for p in products)
    return result


def generate_clip(base, references, seconds, output, workflow_name, scene):
    reference_node, prompt_node, prompt_field, output_node, output_field = WORKFLOWS[workflow_name]
    workflow = json.loads((ROOT / "workflow" / f"{workflow_name}.json").read_text(encoding="utf-8-sig"))
    seed = random.getrandbits(63)
    workflow["129"]["inputs"]["noise_seed"] = seed
    print(f"MiniMax 시드: {seed}", flush=True)
    inputs = workflow[reference_node]["inputs"]
    for key in list(inputs):
        if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.")):
            del inputs[key]
    for index, path in enumerate(references):
        with path.open("rb") as file:
            response = requests.post(base + "/upload/image", files={"image": (path.name, file, mimetypes.guess_type(path.name)[0])}, data={"overwrite": "false", "type": "input"}, timeout=60)
        response.raise_for_status()
        uploaded = response.json()
        node = str(9000 + index)
        workflow[node] = {"class_type": "LoadImage", "inputs": {"image": "/".join(filter(None, (uploaded.get("subfolder"), uploaded["name"])))}}
        inputs[f"ref_images.ref_image_{index}"] = [node, 0]
    inputs["width"] = 640
    inputs["height"] = 640
    workflow["132"]["inputs"]["value"] = seconds
    full_prompt = minimax_prompt(scene, seconds, references[0])
    if len(full_prompt) > 5000:
        raise ValueError("고정 지시를 포함한 MiniMax 프롬프트가 5000자를 초과했습니다")
    (output.parent / f"04_minimax_prompt_{output.stem.removeprefix('04_raw_')}.txt").write_text(full_prompt, encoding="utf-8")
    workflow[prompt_node]["inputs"][prompt_field] = full_prompt
    needed = set()
    def visit(node):
        if node in needed:
            return
        needed.add(node)
        for value in workflow[node]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in workflow:
                visit(value[0])
    visit(output_node)
    response = requests.post(base + "/prompt", json={"prompt": {key: workflow[key] for key in needed}}, timeout=60)
    response.raise_for_status()
    prompt_id = response.json()["prompt_id"]
    print(f"MiniMax 작업: {prompt_id}", flush=True)
    deadline = time.monotonic() + 7200
    while time.monotonic() < deadline:
        time.sleep(5)
        response = requests.get(base + "/history/" + prompt_id, timeout=30)
        response.raise_for_status()
        history = response.json().get(prompt_id)
        if not history:
            continue
        if history.get("status", {}).get("status_str") == "error":
            raise RuntimeError(f"MiniMax 생성 실패: {history['status'].get('messages')}")
        files = history.get("outputs", {}).get(output_node, {}).get(output_field, [])
        if not files:
            raise RuntimeError("MiniMax 영상 결과가 없습니다")
        video = files[0]
        response = requests.get(base + "/view", params={"filename": video["filename"], "subfolder": video.get("subfolder", ""), "type": video.get("type", "output")}, timeout=120)
        response.raise_for_status()
        output.write_bytes(response.content)
        return
    raise TimeoutError(f"MiniMax 작업 시간 초과: {prompt_id}")


def render(job, products, title, bgm, seconds, display_names, country):
    ffmpeg = ROOT / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not ffmpeg.is_file():
        raise FileNotFoundError(f"FFmpeg가 없습니다: {ffmpeg}")
    watermark_setting = os.environ.get("WATERMARK_ENABLED", "true").strip().lower()
    if watermark_setting not in ("true", "false"):
        raise ValueError("WATERMARK_ENABLED는 true 또는 false로 설정하세요")
    watermark = ROOT / "res" / "watermark.png"
    if watermark_setting == "true" and not watermark.is_file():
        raise FileNotFoundError(f"워터마크 파일이 없습니다: {watermark}")
    font_name = {"kr": "Jua-Regular.ttf", "vn": "BeVietnamPro-Bold.ttf", "en": "Montserrat-Bold.ttf"}[country]
    font = ROOT / "font" / font_name
    if not font.is_file():
        raise FileNotFoundError(f"언어에 필요한 글꼴이 없습니다: {font}")
    shutil.copyfile(font, job / "05_font.ttf")
    title_lines = [line.strip().replace("·", " ") if country == "kr" else line.strip() for line in title.split("\n")]
    title_size = min(96 if country == "kr" else 72, max(28, 980 // max(map(len, title_lines))))
    title_filters = []
    for line_index, line in enumerate(title_lines):
        filename = f"05_title_{line_index}.txt"
        (job / filename).write_text(line, encoding="utf-8")
        y = "(290-text_h)/2" if len(title_lines) == 1 else str(145 - title_size - 8 + line_index * (title_size + 16))
        title_filters.append(f"drawtext=fontfile=05_font.ttf:textfile={filename}:expansion=none:fontcolor=orange:fontsize={title_size}:x=(w-text_w)/2:y={y}")
    for index, product in enumerate(products):
        display_name = display_names[index].replace("®", "")
        if country == "kr":
            display_name = display_name.replace("·", " ")
        name_lines = [line.strip() for line in display_name.split("\n")]
        (job / f"05_price_{index}.txt").write_text(formatted_price(product["price"], country), encoding="utf-8")
        name_size = 52 if country == "kr" else 40
        name_filters = []
        for line_index, line in enumerate(name_lines):
            filename = f"05_name_{index}_{line_index}.txt"
            (job / filename).write_text(line, encoding="utf-8")
            name_y = 1450 if len(name_lines) == 1 else 1420 + line_index * (name_size + 10)
            name_filters.append(f"drawtext=fontfile=05_font.ttf:textfile={filename}:expansion=none:fontcolor=white:fontsize={name_size}:x=(w-text_w)/2:y={name_y}")
        price_y = 1525 if len(name_lines) == 1 else 1420 + len(name_lines) * (name_size + 10) + 15
        filters = ("fps=24,scale=1080:1080:force_original_aspect_ratio=decrease,"
                   "pad=1080:1080:(ow-iw)/2:(oh-ih)/2:black,"
                   "tpad=stop_mode=clone:stop_duration=60,"
                   "pad=1080:1920:0:290:black,"
                   + ",".join(title_filters + name_filters) + ","
                   + f"drawtext=fontfile=05_font.ttf:textfile=05_price_{index}.txt:expansion=none:fontcolor=white:fontsize={48 if country == 'kr' else 40}:x=(w-text_w)/2:y={price_y}")
        subprocess.run([str(ffmpeg), "-y", "-i", f"04_raw_{index}.mp4", "-vf", filters, "-t", str(seconds), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", f"06_clip_{index}.mp4"], cwd=job, check=True)
    (job / "06_concat.txt").write_text("".join(f"file '06_clip_{i}.mp4'\n" for i in range(len(products))), encoding="utf-8")
    command = [str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", "06_concat.txt", "-stream_loop", "-1", "-i", str(bgm)]
    if watermark_setting == "true":
        command += ["-i", str(watermark), "-filter_complex",
                    "[2:v]scale=120:-1,format=rgba,colorchannelmixer=aa=0.55[mark];[0:v][mark]overlay=W-w-24:H-h-24:format=auto[v]",
                    "-map", "[v]", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    else:
        command += ["-c:v", "copy"]
    command += ["-t", str(seconds * len(products)), "-c:a", "aac", "-shortest", "07_final.mp4"]
    subprocess.run(command, cwd=job, check=True)


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
