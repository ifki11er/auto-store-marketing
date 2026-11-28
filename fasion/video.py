"""MiniMax 영상 생성과 FFmpeg 합성."""
import json
import mimetypes
import os
import random
import shutil
import subprocess
import time

import requests

from fasion.config import ROOT, WORKFLOWS
from fasion.pricing import formatted_price
from fasion.prompts import minimax_prompt

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
    subscribe_lines = {
        "kr": ("구독 좋아요", "패션정보는 WOW FIT"),
        "en": ("Subscribe & Like", "Discover fashion with WOW FIT"),
        "vn": ("Đăng ký & Thích", "Thời trang cùng WOW FIT"),
    }[country]
    for line_index, line in enumerate(subscribe_lines):
        (job / f"05_subscribe_{line_index}.txt").write_text(line, encoding="utf-8")
    title_filters = []
    for line_index, line in enumerate(title_lines):
        filename = f"05_title_{line_index}.txt"
        (job / filename).write_text(line, encoding="utf-8")
        y = "(290-text_h)/2" if len(title_lines) == 1 else str(145 - title_size - 8 + line_index * (title_size + 16))
        if country == "kr":
            y = f"({y})+15"
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
        subscribe_start = seconds * len(products) - 2 - seconds * index
        if subscribe_start < seconds:
            subscribe_size = 72 if country == "kr" else 52
            for line_index in range(2):
                y_offset = -subscribe_size - 6 if line_index == 0 else 6
                filters += (f",drawtext=fontfile=05_font.ttf:textfile=05_subscribe_{line_index}.txt:"
                            f"expansion=none:fontcolor=white:fontsize={subscribe_size}:"
                            f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2{y_offset:+d}:"
                            f"enable='gte(t\\,{max(0, subscribe_start)})'")
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
