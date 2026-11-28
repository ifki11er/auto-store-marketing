"""기존 영상의 지정한 씬만 다시 만들고 최종 영상을 재합성한다."""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from openai import OpenAI
from fasion.config import WORKFLOWS, configured_file, load_env
from fasion.gpt import ask, image_input
from fasion.prompts import analysis_prompt
from fasion.video import generate_clip, render


# 씬 번호는 영상에 표시되는 순서대로 1부터 시작한다.
JOBS = [
    {"folder": r"C:\d\develop\auto_store_marketing\fasion\output\kr\2026-11-29\1900", "scenes": [1, 2, 3, 4]},
]


def required_file(path, label):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"{label} 파일이 없습니다: {path}")
    return path.resolve()


def clip_duration(folder, count):
    ffprobe = ROOT / "bin" / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    durations = []
    for index in range(count):
        clip = folder / f"06_clip_{index}.mp4"
        if not clip.is_file():
            clip = folder / f"clip_{index}.mp4"
        if not clip.is_file():
            continue
        output = subprocess.check_output([str(ffprobe), "-v", "error", "-show_entries", "format=duration",
                                          "-of", "default=noprint_wrappers=1:nokey=1", str(clip)], text=True)
        durations.append(float(output.strip()))
    if not durations:
        raise ValueError(f"기존 합성 씬에서 길이를 확인할 수 없습니다: {folder}")
    seconds = round(durations[0])
    if seconds < 1 or any(abs(duration - seconds) > 0.1 for duration in durations):
        raise ValueError(f"기존 씬의 길이가 서로 다르거나 정수가 아닙니다: {durations}")
    return seconds


def regenerate(job_config):
    folder = Path(job_config["folder"]).resolve()
    output_root = (ROOT / "fasion" / "output").resolve()
    if not folder.is_relative_to(output_root) or not folder.is_dir():
        raise ValueError(f"패션 결과 폴더를 확인하세요: {folder}")
    metadata_path = folder / "03_metadata.json"
    if not metadata_path.is_file():
        metadata_path = folder / "metadata.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    products = data.get("products")
    names = data.get("display_names")
    title = data.get("video_title")
    country = data.get("country", "kr")
    if (not isinstance(products, list) or not products or not isinstance(names, list)
            or len(names) != len(products) or not isinstance(title, str) or not title.strip()
            or country not in ("kr", "vn", "en")):
        raise ValueError(f"재합성에 필요한 메타데이터가 올바르지 않습니다: {metadata_path}")
    scenes = job_config.get("scenes")
    if (not isinstance(scenes, list) or not scenes or any(type(i) is not int or i < 1 or i > len(products) for i in scenes)
            or len(set(scenes)) != len(scenes)):
        raise ValueError(f"씬 번호는 1~{len(products)} 범위의 중복 없는 목록이어야 합니다: {folder}")
    selected = {number - 1 for number in scenes}
    final = folder / "07_final.mp4"
    if not final.is_file():
        final = folder / "final.mp4"
    if not final.is_file() or not final.stat().st_size:
        raise FileNotFoundError(f"기존 최종 영상이 없습니다: {folder}")
    raw_files = []
    for index in range(len(products)):
        raw = folder / f"04_raw_{index}.mp4"
        if not raw.is_file():
            raw = folder / f"raw_{index}.mp4"
        if index not in selected and (not raw.is_file() or not raw.stat().st_size):
            raise FileNotFoundError(f"유지할 원본 씬이 없습니다: {raw}")
        raw_files.append(raw)
    seconds = clip_duration(folder, len(products))
    face = required_file(data["influencer_image"], "인플루언서") if data.get("influencer_image") else configured_file(f"FASION_FACE_{country.upper()}")
    if not data.get("bgm"):
        raise ValueError(f"기존 BGM 정보가 없습니다. 원본 음악 경로를 메타데이터에서 확인하세요: {metadata_path}")
    bgm = required_file(data["bgm"], "기존 BGM")
    workflow = os.environ.get("VIDEO_WORKFLOW", "Deno_MiniMax_H3_R2V_Turbo_8plus2").strip()
    if workflow not in WORKFLOWS:
        raise ValueError(f"지원하지 않는 VIDEO_WORKFLOW: {workflow}")
    base = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").strip().rstrip("/")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    staging = output_root / "작업중" / f"씬재생성_{stamp}"
    staging.mkdir(parents=True)
    token_usage = folder / "05_gpt_token_usage.json"
    if token_usage.is_file():
        shutil.copy2(token_usage, staging / token_usage.name)
    client = OpenAI()
    print(f"재생성: {folder} | 씬 {scenes} | 씬당 {seconds}초", flush=True)
    for index, product in enumerate(products):
        output = staging / f"04_raw_{index}.mp4"
        if index not in selected:
            shutil.copy2(raw_files[index], output)
            continue
        reference = Path(product.get("reference_image", ""))
        if not reference.is_absolute():
            reference = folder / reference
        reference = required_file(reference, f"씬 {index + 1} 옷 사진")
        print(f"씬 {index + 1} GPT 사진 분석 및 배경·동작 생성 중...", flush=True)
        context = {"product_name": product.get("name", ""), "seconds": seconds}
        stage = f"01_gpt_analysis_regen_{stamp}_{index + 1:03d}"
        result = ask(client, [{"type": "input_text", "text": analysis_prompt() + "\n" + json.dumps(context, ensure_ascii=False)}, image_input(reference)], staging, stage)
        if result.get("usable") is not True:
            raise ValueError(f"씬 {index + 1}의 옷 사진을 GPT가 사용할 수 없다고 판단했습니다: {reference}")
        background, action, accessories = result.get("background"), result.get("action"), result.get("accessories", "")
        if (not isinstance(background, str) or not background.strip() or len(background) > 300 or any(c in background for c in "<>{}")
                or not isinstance(action, str) or not action.strip() or len(action) > 300 or any(c in action for c in "<>{}")
                or not isinstance(accessories, str) or len(accessories) > 300 or any(c in accessories for c in "<>{}")):
            raise ValueError(f"씬 {index + 1}의 GPT 배경·동작 정보가 올바르지 않습니다: {reference}")
        use_runway = bool(re.search(r"\b(?:indoor|interior)\b", background, re.IGNORECASE)
                          and re.search(r"\b(?:runway|catwalk)\b", background, re.IGNORECASE))
        scene = {"background": background.strip(), "use_runway_reference": use_runway, "action": action.strip(), "accessories": accessories.strip()}
        product["scene"] = scene
        references = [face]
        if scene["use_runway_reference"]:
            references.append(configured_file("FASION_STAGE"))
        references.append(reference)
        print(f"씬 {index + 1} 재생성 중: {product.get('name', '')}", flush=True)
        generate_clip(base, references, seconds, output, workflow, scene)

    print("최종 영상 재합성 중...", flush=True)
    render(staging, products, title, bgm, seconds, names, country)
    (staging / metadata_path.name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    backup = folder / f"08_재생성_백업_{stamp}"
    backup.mkdir()
    replacements = [f"04_raw_{i}.mp4" for i in sorted(selected)]
    replacements += [f"04_minimax_prompt_{i}.txt" for i in sorted(selected)]
    replacements += [p.name for p in staging.iterdir() if p.is_file() and p.name.startswith("01_gpt_analysis_regen_")]
    replacements.append(metadata_path.name)
    replacements += [p.name for p in staging.iterdir() if p.is_file() and p.name.startswith(("05_", "06_"))]
    replacements.append("07_final.mp4")
    for name in replacements:
        destination = folder / name
        if destination.is_file():
            shutil.copy2(destination, backup / name)
    if final.name != "07_final.mp4":
        shutil.copy2(final, backup / final.name)
    for name in replacements:
        source = staging / name
        target = folder / name
        temporary = folder / (name + ".tmp")
        shutil.copy2(source, temporary)
        temporary.replace(target)
    shutil.rmtree(staging)
    print(f"완료: {folder / '07_final.mp4'} | 원본 백업: {backup}", flush=True)


def main():
    load_env()
    if not JOBS:
        raise ValueError("스크립트 상단 JOBS에 결과 폴더와 재생성할 씬 번호를 설정하세요")
    folders = [str(Path(job["folder"]).resolve()).casefold() for job in JOBS]
    if len(set(folders)) != len(folders):
        raise ValueError("같은 결과 폴더를 JOBS에 중복 등록할 수 없습니다")
    failures = []
    for index, job in enumerate(JOBS, 1):
        print(f"작업 {index}/{len(JOBS)}", flush=True)
        try:
            regenerate(job)
        except Exception as exc:
            failures.append(f"{job.get('folder')}: {exc}")
            print(f"재생성 실패: {exc}", flush=True)
    if failures:
        raise RuntimeError("일부 작업 실패: " + "; ".join(failures))
    print("모든 재생성 작업이 완료됐습니다.", flush=True)


if __name__ == "__main__":
    main()
