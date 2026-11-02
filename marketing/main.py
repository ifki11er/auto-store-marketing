"""카카오 장소에서 15초 마케팅 영상과 기획안을 생성한다."""
import argparse
import base64
import json
import hashlib
import mimetypes
import os
import re
import subprocess
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import requests
from openai import OpenAI
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from marketing.channel_regions import CHANNEL_ORDER, channel_for_address, iter_region_place_iterators
from marketing.completed_places import CompletedPlaces
from marketing.token_usage import record_token_usage
from marketing.upload_schedule import KOREA_TIME, UploadSchedule
from marketing.youtube_upload import upload_video
from marketing.prompts import build_planning_prompt, select_video_format

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / "workflow" / "Deno_MiniMax_H3_R2V_Turbo_8plus2.json"
GUIDE = ROOT / "docs" / "MINIMAX_H3_SUMMARY.md"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://place.map.kakao.com/"}


def load_env(path):
    """프로젝트 .env의 KEY=VALUE 설정을 읽되 기존 환경 변수는 유지한다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key.isidentifier():
            raise ValueError(f"잘못된 .env 설정: {key}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env(ROOT / ".env")


class CollectionError(Exception):
    pass


def place_availability(browser, place_url):
    """원본 장소 페이지의 기본 탭에서 후기와 사진의 유무를 확인한다."""
    page = browser.new_page(user_agent=HEADERS["User-Agent"])
    try:
        response = page.goto(place_url.split("#")[0], wait_until="domcontentloaded", timeout=30000)
        if response and not response.ok:
            raise CollectionError(f"장소 페이지 HTTP {response.status}")
        page.locator(".main_detail .tab_primary .list_tab").wait_for(state="attached", timeout=15000)
        page.locator(".main_detail .section_grade").wait_for(state="attached", timeout=15000)
        tabs = page.locator(".main_detail .tab_primary .list_tab")
        return (tabs.locator('a[href="#review"]').count() > 0,
                tabs.locator('a[href="#photoview"]').count() > 0)
    except PlaywrightTimeout as exc:
        raise CollectionError("장소 페이지 응답 시간 초과") from exc
    finally:
        page.close()


def reviews_for(browser, place_url, availability=None):
    if availability is None:
        availability = place_availability(browser, place_url)
    if not availability[0]:
        return []
    page = browser.new_page(user_agent=HEADERS["User-Agent"])
    try:
        response = page.goto(place_url.split("#")[0] + "#review", wait_until="domcontentloaded", timeout=30000)
        if response and not response.ok:
            raise CollectionError(f"후기 페이지 HTTP {response.status}")
        empty = page.get_by_text(re.compile(r"등록된 후기가 없습니다|후기가 없습니다|후기가 없어요|등록된 리뷰가 없습니다|리뷰가 없습니다"))
        section = page.locator(".section_review")
        section.or_(empty).first.wait_for(state="attached", timeout=15000)
        if not section.count() and empty.count():
            return []
        items = page.locator(".section_review .list_review > li")
        reviews = []
        for i in range(items.count()):
            item = items.nth(i)
            text = item.locator(".desc_review")
            if not text.count():
                continue
            content = text.first.evaluate("""element => {
                const copy = element.cloneNode(true);
                copy.querySelectorAll('.btn_more').forEach(node => node.remove());
                return copy.textContent.trim();
            }""")
            if content:
                score = item.locator(".review_detail .starred_grade .screen_out")
                date = item.locator(".review_detail .txt_date")
                reviews.append({"내용": content,
                                "평점": score.nth(1).text_content().strip() if score.count() > 1 else None,
                                "작성일": date.first.text_content().strip() if date.count() else None})
        if reviews:
            return reviews
        if items.count():
            return []
        body = page.locator("body").inner_text()
        if any(message in body for message in ("등록된 후기가 없습니다", "후기가 없습니다", "후기가 없어요", "등록된 리뷰가 없습니다", "리뷰가 없습니다")):
            return []
        raise CollectionError("후기 영역을 확인하지 못했습니다. 카카오 페이지 구조를 확인하세요")
    except PlaywrightTimeout as exc:
        raise CollectionError("후기 페이지 응답 시간 초과") from exc
    finally:
        page.close()


def photos_for(browser, place_url, folder, limit, availability=None):
    limit = min(limit, 10) if limit is not None else 10
    if availability is None:
        availability = place_availability(browser, place_url)
    if not availability[1]:
        return []
    page = browser.new_page(user_agent=HEADERS["User-Agent"])
    try:
        response = page.goto(place_url.split("#")[0] + "#photoview", wait_until="domcontentloaded", timeout=30000)
        if response and not response.ok:
            raise CollectionError(f"사진 페이지 HTTP {response.status}")
        empty = page.get_by_text(re.compile(r"등록된 사진이 없습니다|사진이 없습니다|사진이 없어요"))
        section = page.locator(".view_photolist")
        section.or_(empty).first.wait_for(state="attached", timeout=15000)
        if not section.count() and empty.count():
            return []
        # 사진 영역이 먼저 나타나고 이미지 목록은 나중에 채워질 수 있다.
        try:
            page.wait_for_function("""() => Array.from(document.querySelectorAll('.view_photolist .list_photo img')).some(img =>
                ['data-original', 'data-src', 'src'].some(attr => {
                    const value = img.getAttribute(attr);
                    if (!value) return false;
                    try {
                        const host = new URL(value, location.href).hostname.toLowerCase();
                        return host.endsWith('.daumcdn.net') || host.endsWith('.kakaocdn.net');
                    } catch { return false; }
                }))""", timeout=15000)
        except PlaywrightTimeout as exc:
            body = page.locator("body").inner_text()
            if any(s in body for s in ("등록된 사진이 없습니다", "사진이 없습니다", "사진이 없어요")):
                return []
            raise CollectionError("사진 목록이 제한 시간 안에 로딩되지 않았습니다") from exc
        candidates = page.locator(".view_photolist .list_photo img")
        urls = []
        for i in range(candidates.count()):
            el = candidates.nth(i)
            for attr in ("data-original", "data-src", "src"):
                src = el.get_attribute(attr)
                if not src:
                    continue
                src = urljoin(page.url, src)
                host = (urlsplit(src).hostname or "").lower()
                if host.endswith(".daumcdn.net") or host.endswith(".kakaocdn.net"):
                    if src not in urls:
                        urls.append(src)
                    break
        if not urls:
            body = page.locator("body").inner_text()
            if any(s in body for s in ("등록된 사진이 없습니다", "사진이 없습니다", "사진이 없어요")):
                return []
            raise CollectionError("사진 영역을 확인하지 못했습니다. 카카오 페이지 구조를 확인하세요")
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for url in urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                mime = r.headers.get("Content-Type", "").split(";")[0].lower()
                ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime)
                if not ext or not r.content:
                    continue
                path = folder / f"photo_{len(files)+1}{ext}"
                path.write_bytes(r.content)
                files.append(path)
                if limit is not None and len(files) >= limit:
                    break
            except requests.RequestException:
                continue
        if not files:
            raise CollectionError("사진 URL은 있으나 다운로드에 실패했습니다")
        return files
    except PlaywrightTimeout as exc:
        raise CollectionError("사진 페이지 응답 시간 초과") from exc
    finally:
        page.close()


def output_text(response):
    if not response.output_text:
        raise RuntimeError("OpenAI 응답에 텍스트가 없습니다")
    return response.output_text


def analyze(client, photos):
    content = [{"type": "input_text", "text": "사진을 한국어로 분석해 JSON 객체만 반환하세요. 가게_전체_특징은 사진 전체에서 확인되는 가게의 특징을 간결히 요약한 문자열 배열입니다. 사진별_정보는 입력 순서대로 각 사진의 파일명과 보이는 음식·공간·특징을 간결히 설명한 배열입니다. 사진에 보이지 않는 내용은 추측하지 말고 영상 장면, 구도, 연출이나 스토리를 제안하지 마세요."}]
    for index, photo in enumerate(photos, 1):
        content.append({"type": "input_text", "text": f"사진 {index}: {photo.name}"})
        mime = mimetypes.guess_type(photo.name)[0]
        data = base64.b64encode(photo.read_bytes()).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:{mime};base64,{data}", "detail": "auto"})
    return output_text(client.responses.create(model="gpt-6-luna", input=[{"role": "user", "content": content}], text={"format": {"type": "json_object"}}))


def plan(client, place, reviews, analysis, photos, prior_concepts=None):
    video_format = select_video_format()
    if os.environ.get("VIDEO_FORMAT", "").strip().lower() == "random":
        print(f"랜덤 선택 포맷: {video_format}", flush=True)
    instruction = build_planning_prompt(place.get("category_group_code"), video_format=video_format)
    try:
        photo_analysis = json.loads(analysis)
    except (TypeError, ValueError):
        photo_analysis = None
    if isinstance(photo_analysis, dict) and "가게_전체_특징" in photo_analysis and "사진별_정보" in photo_analysis:
        features = photo_analysis["가게_전체_특징"]
        details = photo_analysis["사진별_정보"]
    else:
        features = "별도 요약 없음. 기존 사진 분석 내용을 참고하세요."
        details = analysis
    payload = {"오늘": datetime.now().astimezone().date().isoformat(), "가게": place, "후기": reviews,
               "가게_전체_특징": features}
    if prior_concepts is not None:
        instruction += "\n비교 테스트를 위해 결과 JSON의 중복_비교_요약에 이 기획의 핵심 상황과 전개만 80자 이내로 작성한다. 가게 특징, 포맷명, 기획 의도나 평가를 반복하지 않는다."
        if prior_concepts:
            payload["이전에_기획한_콘셉트"] = prior_concepts
    payload.update({"사진_선택용_상세정보": details,
                    "사진_목록": [{"분석_순번": i, "파일명": photo.name} for i, photo in enumerate(photos, 1)],
                    "MiniMax_가이드": GUIDE.read_text(encoding="utf-8-sig")})
    base_input = "다음 자료를 바탕으로 JSON 객체를 작성하세요.\n" + json.dumps(payload, ensure_ascii=False)
    error = None
    last_result = None
    for attempt in range(3):
        retry_input = base_input
        if error is not None:
            retry_input += "\n이전 결과가 다음 검증에 실패했습니다. 해당 문제를 고쳐 전체 JSON을 다시 작성하세요: " + str(error)
        response = client.responses.create(model="gpt-6-luna", instructions=instruction,
            input=retry_input, text={"format": {"type": "json_object"}})
        try:
            result = json.loads(output_text(response))
            if not isinstance(result, dict):
                raise ValueError("기획 결과는 JSON 객체여야 합니다")
            last_result = result
            validate_plan(result, photos)
            break
        except (ValueError, KeyError, TypeError) as exc:
            error = exc
            print(f"기획 검증 실패 ({attempt + 1}/3): {exc}", flush=True)
    else:
        if last_result is None:
            raise ValueError("세 번의 기획 응답 모두 사용할 수 있는 JSON 객체가 아닙니다") from error
        print("기획 검증에 세 번 실패하여 마지막으로 받은 JSON 객체를 그대로 사용합니다.", flush=True)
        result = last_result
    shots = result.get("15초_스크립트")
    if os.environ.get("VIDEO_FORMAT", "").strip().lower() == "random":
        result["랜덤_선택_포맷"] = video_format
    for shot in shots if isinstance(shots, list) else []:
        if isinstance(shot, dict):
            shot.pop("자막_스타일", None)
    result["분석_사진_목록"] = photo_manifest(photos)
    return result


def validate_plan(result, photos):
    if not isinstance(result.get("영상_형태"), str) or not result["영상_형태"].strip():
        raise ValueError("기획 결과에 영상 형태가 없습니다")
    for key in ("영상_컨셉", "스토리_요약", "기획_차별점", "핵심_메시지", "시청자_반응", "결말_의미"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError(f"기획 결과에 {key}가 없습니다")
    shots = result.get("15초_스크립트")
    if not isinstance(shots, list) or not shots or not result.get("minimax_prompt"):
        raise ValueError("기획 결과에 스크립트 또는 영상 프롬프트가 없습니다")
    end = 0.0
    for shot in shots:
        start, stop = float(shot["시작초"]), float(shot["종료초"])
        if start != end or stop <= start:
            raise ValueError("스크립트 시간이 연속적이지 않습니다")
        if not isinstance(shot.get("자막"), str):
            raise ValueError("자막은 문자열이어야 합니다. 자막이 없으면 빈 문자열을 사용하세요")
        narration = shot.get("내레이션")
        if not isinstance(narration, str) or re.search(r"[0-9]", narration):
            raise ValueError("대사는 문자열이어야 하며 숫자는 실제 읽는 한글 발음으로 작성해야 합니다")
        if shot.get("발화_유형") not in ("나레이션", "화면발화", "없음"):
            raise ValueError("각 장면의 발화_유형은 나레이션, 화면발화, 없음 중 하나여야 합니다")
        if bool(narration.strip()) != (shot["발화_유형"] != "없음"):
            raise ValueError("발화 유형과 대사 유무가 일치하지 않습니다")
        if shot["발화_유형"] == "나레이션":
            count = len(re.findall(r"[가-힣]", narration))
            required = count * 4 / 25
            if stop - start + 1e-9 < required:
                raise ValueError(f"나레이션 한글 {count}자에는 약 {required:.2f}초가 필요하지만 장면은 {stop - start:.2f}초입니다")
        end = stop
    if end != 15:
        raise ValueError("스크립트가 15초가 아닙니다")
    if {"나레이션", "화면발화"} <= {shot["발화_유형"] for shot in shots}:
        raise ValueError("한 영상에서 나레이션과 화면발화를 함께 사용할 수 없습니다")
    voices = result.get("음성_연출")
    if not isinstance(voices, list):
        raise ValueError("음성_연출은 배열이어야 합니다")
    if any(shot["내레이션"].strip() for shot in shots) and not voices:
        raise ValueError("대사가 있는 기획에는 화자별 음성 연출이 필요합니다")
    voice_ids = set()
    for voice in voices:
        if not isinstance(voice, dict) or any(not isinstance(voice.get(key), str) or not voice[key].strip() for key in ("화자_ID", "목소리_설명", "연출_이유")):
            raise ValueError("각 음성 연출에 화자_ID, 목소리_설명, 연출_이유가 필요합니다")
        if voice["화자_ID"] in voice_ids:
            raise ValueError("음성 연출의 화자_ID가 중복되었습니다")
        voice_ids.add(voice["화자_ID"])
    voices_by_id = {voice["화자_ID"]: voice for voice in voices}
    for shot in shots:
        if shot["발화_유형"] == "없음":
            continue
        voice = voices_by_id.get(shot.get("화자_ID"))
        if voice is None:
            raise ValueError("대사가 있는 장면에 음성_연출의 화자_ID가 필요합니다")
    dialogue = re.findall(r"<d>((?:(?!<d>|</d>).)*)</d>", result["minimax_prompt"], flags=re.DOTALL)
    if [text.strip() for text in dialogue] != [shot["내레이션"].strip() for shot in shots if shot["내레이션"].strip()]:
        raise ValueError("영상 프롬프트의 대사가 스크립트 대사와 순서대로 일치하지 않습니다")
    if any(shot["발화_유형"] == "나레이션" for shot in shots) and "<Audio 1>" not in result["minimax_prompt"]:
        raise ValueError("나레이션 프롬프트에 <Audio 1> 목소리 참조가 없습니다")
    selected = result.get("선택_사진")
    available = {photo.name for photo in photos}
    if not isinstance(selected, list) or not 1 <= len(selected) <= 9:
        raise ValueError("영상에 사용할 사진은 1~9장이어야 합니다")
    names = []
    for index, item in enumerate(selected, 1):
        if not isinstance(item, dict) or item.get("파일명") not in available or not item.get("선택_이유"):
            raise ValueError("선택 사진의 파일명 또는 선택 이유가 올바르지 않습니다")
        if item["파일명"] in names:
            raise ValueError("같은 사진을 중복 선택했습니다")
        if f"<Picture {index}>" not in result["minimax_prompt"]:
            raise ValueError(f"영상 프롬프트에 <Picture {index}> 참조가 없습니다")
        names.append(item["파일명"])


def photo_manifest(photos):
    return [{"파일명": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in photos]


def selected_photos(result, photos):
    available = {p.name: p for p in photos}
    selected = result.get("선택_사진")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 9:
        raise ValueError("기획의 선택 사진은 1~9장이어야 합니다. 5번 테스트를 다시 실행하세요")
    names = [item.get("파일명") if isinstance(item, dict) else None for item in selected]
    if len(set(names)) != len(names) or any(name not in available for name in names):
        raise ValueError("기획에 지정된 사진이 없거나 중복되었습니다")
    return [available[name] for name in names]


def narration_reference(shots):
    kinds = {shot.get("발화_유형") for shot in shots}
    if not kinds <= {"나레이션", "화면발화", "없음"}:
        raise ValueError("발화 유형을 확인할 수 없습니다. 5번 기획을 다시 생성하세요")
    if {"나레이션", "화면발화"} <= kinds:
        raise ValueError("한 영상에서 나레이션과 화면발화를 함께 사용할 수 없습니다")
    if "나레이션" not in kinds:
        return None
    filename = os.environ.get("MINIMAX_REFERENCE_VOICE", "").strip()
    if not filename:
        raise ValueError(".env의 MINIMAX_REFERENCE_VOICE에 참조 WAV 파일명을 입력하세요")
    path = ROOT / "res" / filename
    if not path.is_file():
        raise FileNotFoundError(f"MiniMax 참조 음성 파일이 없습니다: {path}")
    return path


def generate_video(base, photos, prompt, output, reference_audio=None):
    if not 1 <= len(photos) <= 9:
        raise ValueError("ComfyUI에 전달할 사진은 1~9장이어야 합니다")
    base = base.rstrip("/")
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8-sig"))
    inputs = workflow["235"]["inputs"]
    for key in list(inputs):
        if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.")):
            del inputs[key]
    for index, path in enumerate(photos):
        with path.open("rb") as image:
            response = requests.post(base + "/upload/image", files={"image": (path.name, image, mimetypes.guess_type(path.name)[0])},
                                     data={"overwrite": "false", "type": "input"}, timeout=60)
        response.raise_for_status()
        uploaded = response.json()
        name = "/".join(filter(None, (uploaded.get("subfolder"), uploaded["name"])))
        node_id = str(9000 + index)
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        inputs[f"ref_images.ref_image_{index}"] = [node_id, 0]
    if reference_audio is not None:
        with reference_audio.open("rb") as audio:
            response = requests.post(base + "/upload/image", files={"image": (reference_audio.name, audio, "audio/wav")},
                                     data={"overwrite": "false", "type": "input"}, timeout=60)
        response.raise_for_status()
        uploaded = response.json()
        name = "/".join(filter(None, (uploaded.get("subfolder"), uploaded["name"])))
        node_id = str(9000 + len(photos))
        workflow[node_id] = {"class_type": "LoadAudio", "inputs": {"audio": name}}
        inputs["ref_audios.ref_audio_0"] = [node_id, 0]
    workflow["186"]["inputs"]["text"] = prompt
    workflow["132"]["inputs"]["value"] = 15
    # Submit only nodes reachable from the video output, removing sample assets.
    needed = set()
    def visit(node):
        if node in needed:
            return
        needed.add(node)
        for value in workflow[node]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in workflow:
                visit(value[0])
    visit("144")
    payload = {key: workflow[key] for key in needed}
    response = requests.post(base + "/prompt", json={"prompt": payload}, timeout=60)
    response.raise_for_status()
    prompt_id = response.json()["prompt_id"]
    print(f"ComfyUI 작업 ID: {prompt_id}", flush=True)
    deadline = time.monotonic() + 7200
    while time.monotonic() < deadline:
        time.sleep(5)
        response = requests.get(base + "/history/" + prompt_id, timeout=30)
        response.raise_for_status()
        history = response.json().get(prompt_id)
        if not history:
            continue
        status = history.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError(f"ComfyUI 생성 실패: {status.get('messages')}")
        files = history.get("outputs", {}).get("144", {}).get("gifs", [])
        if not files:
            raise RuntimeError("ComfyUI 결과에 영상이 없습니다")
        video = files[0]
        response = requests.get(base + "/view", params={"filename": video["filename"], "subfolder": video.get("subfolder", ""), "type": video.get("type", "output")}, timeout=120)
        response.raise_for_status()
        output.write_bytes(response.content)
        return prompt_id
    raise TimeoutError(f"ComfyUI 작업 대기 시간 초과: {prompt_id}")


def add_subtitles(raw_video, output, shots, subtitle_name="subtitles.srt", place_name=""):
    """기획 자막을 SRT와 ASS로 저장하고 장면별 스타일을 합성한다."""
    def timestamp(seconds, ass=False):
        units = round(float(seconds) * (100 if ass else 1000))
        factor = 100 if ass else 1000
        hours, units = divmod(units, 3600 * factor)
        minutes, units = divmod(units, 60 * factor)
        sec, fraction = divmod(units, factor)
        return (f"{hours}:{minutes:02}:{sec:02}.{fraction:02}" if ass else
                f"{hours:02}:{minutes:02}:{sec:02},{fraction:03}")

    entries = []
    events = []
    if place_name.strip():
        title = place_name.strip().replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        events.append(f"Dialogue: 0,{timestamp(0, True)},{timestamp(shots[-1]['종료초'], True)},Default,,0,0,0,,{{\\an8\\fs90\\c&HFFFFFF&}}{title}")
    for shot in shots:
        text = shot["자막"].strip()
        if not text:
            continue
        start, end = shot["시작초"], shot["종료초"]
        entries.append(f"{len(entries) + 1}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n")
        text = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r\n", "\n").replace("\n", r"\N")
        tags = r"{\an5\fs54\c&HFFFFFF&}"
        events.append(f"Dialogue: 0,{timestamp(start, True)},{timestamp(end, True)},Default,,0,0,0,,{tags}{text}")
    subtitles = output.parent / subtitle_name
    subtitles.write_text("\n".join(entries), encoding="utf-8-sig")
    ass_file = subtitles.with_suffix(".ass")
    if events:
        ass_file.write_text("""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Jua,54,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,90,90,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n", encoding="utf-8-sig")
    ffmpeg = ROOT / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not ffmpeg.is_file():
        raise FileNotFoundError(f"FFmpeg 실행 파일이 없습니다: {ffmpeg}")
    watermark = ROOT / "res" / "watermark.png"
    if not watermark.is_file():
        raise FileNotFoundError(f"워터마크 파일이 없습니다: {watermark}")
    with tempfile.TemporaryDirectory(dir=output.parent.resolve()) as temp_dir:
        if events:
            font = ROOT / "font" / "Jua-Regular.ttf"
            if not font.is_file():
                raise FileNotFoundError(f"자막 폰트 파일이 없습니다: {font}")
            shutil.copy2(ass_file, Path(temp_dir) / "subtitles.ass")
            shutil.copy2(font, Path(temp_dir) / font.name)
            video_filter = "[0:v]ass=subtitles.ass:fontsdir=.[base]"
        else:
            video_filter = "[0:v]null[base]"
        filters = (video_filter + ";[1:v]scale=120:-1,format=rgba,colorchannelmixer=aa=0.55[mark];"
                   "[base][mark]overlay=W-w-24:H-h-24:format=auto[v]")
        subprocess.run([str(ffmpeg), "-y", "-i", str(raw_video.resolve()), "-i", str(watermark),
                        "-filter_complex", filters, "-map", "[v]", "-map", "0:a?",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "copy",
                        str(output.resolve())], cwd=temp_dir, check=True)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def recorded_client(client, folder, stage, number):
    """인증 정보를 제외하고 GPT 요청 본문과 전체 응답을 저장한다."""
    attempts = 0
    def create(**kwargs):
        nonlocal attempts
        attempts += 1
        if stage == "plan":
            write_json(folder / f"{number}_gpt_{stage}_request_{attempts}.json", kwargs)
        else:
            write_json(folder / f"{number}_gpt_{stage}_request.json", kwargs)
        response = client.responses.create(**kwargs)
        record_token_usage(folder, stage, response)
        if stage == "plan":
            write_json(folder / f"{number}_gpt_{stage}_response_{attempts}.json", response.model_dump(mode="json"))
        else:
            write_json(folder / f"{number}_gpt_{stage}_response.json", response.model_dump(mode="json"))
        return response

    return SimpleNamespace(responses=SimpleNamespace(create=create))


def is_chain_place(place):
    category = (place.get("category_name") or "").rsplit(">", 1)[-1].strip()
    name = (place.get("place_name") or "").strip()
    return bool(category and name.startswith(category))


def main(argv=None, single=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "kakao_places_filtered.db")
    parser.add_argument("--place-id", help="특정 가게만 처리")
    parser.add_argument("--after-id", default="", help="이 ID 다음부터 검색")
    parser.add_argument("--max-photos", type=int, default=10, help="다운로드할 최대 사진 수 (최대 10장)")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    args = parser.parse_args(argv)
    build_planning_prompt(None)  # 영상 생성 전에 포맷 설정을 확인한다.
    if args.max_photos is not None and args.max_photos < 1:
        parser.error("--max-photos는 1 이상이어야 합니다")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY 환경 변수가 필요합니다")
    history = CompletedPlaces(ROOT / "completed_places.json")
    history.import_videos((args.output, ROOT / "test" / "results"))
    upload_setting = os.environ.get("YOUTUBE_UPLOAD_ENABLED", "true").strip().lower()
    if not single and upload_setting not in {"true", "false"}:
        raise ValueError("YOUTUBE_UPLOAD_ENABLED는 true 또는 false로 설정하세요")
    upload_enabled = not single and upload_setting == "true"
    schedule = UploadSchedule.from_environment(os.environ) if upload_enabled else None
    if not single:
        print(f"유튜브 업로드: {'사용' if upload_enabled else '사용 안 함'}", flush=True)
    if schedule:
        missing = [channel for channel in CHANNEL_ORDER if not (ROOT / "res" / f"{channel}.pickle").is_file()]
        if missing:
            raise ValueError("유튜브 인증 파일이 없습니다: " + ", ".join(missing))
        if any(entry.get("업로드_상태") == "진행중" for entry in history.places.values()):
            raise ValueError("업로드 결과가 확인되지 않은 이력이 있습니다. 유튜브에서 확인한 뒤 completed_places.json을 수정하세요")
    client = OpenAI()
    uri = args.db.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db, sync_playwright() as playwright:
        db.row_factory = sqlite3.Row
        if args.place_id:
            groups = (iter(db.execute("SELECT * FROM places WHERE place_id = ?", (args.place_id,))),)
        else:
            groups = iter_region_place_iterators(db, args.after_id)
        browser = playwright.chromium.launch(headless=True)
        try:
            completed = 0
            for rows in groups:
                if schedule and all(schedule.next_publication(datetime.now(KOREA_TIME),
                    [datetime.fromisoformat(entry["업로드_시간"]) for entry in history.places.values()
                     if entry.get("지역") == channel and entry.get("업로드_상태") == "완료" and entry.get("업로드_시간")]) is None
                    for channel in CHANNEL_ORDER):
                    print("모든 지역의 공개 기간 일정이 완료되었습니다", flush=True)
                    break
                for row in rows:
                    place = dict(row)
                    place_id = str(place["place_id"])
                    if place.get("category_group_code") in {"MT1", "PS3", "SC4", "OL7", "PO3", "AG2", "BK9", "CS2", "HP8", "PK6", "PM9", "SW8"}:
                        print(f"제외 업종: {place_id} ({place['category_group_code']}) 건너뜀", flush=True)
                        continue
                    if is_chain_place(place):
                        print(f"체인점: {place_id} {place['place_name']} 건너뜀", flush=True)
                        continue
                    if not place_id.isdigit() or not place.get("place_url"):
                        print(f"건너뜀: 유효하지 않은 ID 또는 URL ({place_id})", flush=True)
                        continue
                    parsed = urlsplit(place["place_url"])
                    if parsed.hostname != "place.map.kakao.com" or parsed.path.strip("/") != place_id:
                        print(f"건너뜀: 유효하지 않은 카카오 장소 URL ({place_id})", flush=True)
                        continue
                    channel = channel_for_address(place.get("address_name") or place.get("road_address_name"))
                    if schedule:
                        if not channel:
                            print(f"지역 채널 없음: {place_id} 건너뜀", flush=True)
                            continue
                        allocated = [datetime.fromisoformat(entry["업로드_시간"]) for entry in history.places.values()
                                     if entry.get("지역") == channel and entry.get("업로드_상태") == "완료" and entry.get("업로드_시간")]
                        if schedule.next_publication(datetime.now(KOREA_TIME), allocated) is None:
                            break
                    completed_videos = (args.output / place_id / "video.mp4", ROOT / "test" / "results" / place_id / "video.mp4")
                    if history.contains(place_id) or any(path.is_file() and path.stat().st_size > 0 for path in completed_videos):
                        print(f"이미 영상이 완성된 가게: {place_id} 건너뜀", flush=True)
                        continue
                    print(f"가게 처리: {place_id} {place['place_name']}", flush=True)
                    folder = args.output / place_id
                    try:
                        availability = place_availability(browser, place["place_url"])
                        if not availability[1]:
                            print("사진 없음: 건너뜀", flush=True)
                            continue
                        reviews = reviews_for(browser, place["place_url"], availability)
                    except CollectionError as exc:
                        if single or args.place_id or not isinstance(exc.__cause__, PlaywrightTimeout):
                            raise
                        print(f"후기 수집 시간 초과: {place_id} 건너뜀 ({exc})", flush=True)
                        continue
                    if not reviews:
                        print("후기 없음: 후기 없이 진행", flush=True)
                    with tempfile.TemporaryDirectory() as temp_dir:
                        try:
                            photos = photos_for(browser, place["place_url"], Path(temp_dir) / "photos", args.max_photos, availability)
                        except CollectionError as exc:
                            if single or args.place_id or not isinstance(exc.__cause__, PlaywrightTimeout):
                                raise
                            print(f"사진 수집 시간 초과: {place_id} 건너뜀 ({exc})", flush=True)
                            continue
                        if not photos:
                            print("사진 없음: 건너뜀", flush=True)
                            continue
                        photo_folder = folder / "03_photos"
                        photo_folder.mkdir(parents=True, exist_ok=True)
                        photos = [Path(shutil.move(str(photo), str(photo_folder / photo.name))) for photo in photos]
                    write_json(folder / "01_place.json", place)
                    write_json(folder / "02_reviews.json", reviews)
                    write_json(folder / "04_analysis_photos.json", photo_manifest(photos))
                    analysis = analyze(recorded_client(client, folder, "analysis", "04"), photos)
                    (folder / "04_analysis.txt").write_text(analysis, encoding="utf-8")
                    result = plan(recorded_client(client, folder, "plan", "05"), place, reviews, analysis, photos)
                    write_json(folder / "05_plan.json", result)
                    write_json(folder / "05_marketing.json", {"가게": place, "후기": reviews, "사진_분석": analysis, "기획": result})
                    reference_audio = narration_reference(result["15초_스크립트"])
                    raw_video = folder / "06_video_raw.mp4"
                    generate_video(args.comfy_url, selected_photos(result, photos), result["minimax_prompt"], raw_video, reference_audio)
                    add_subtitles(raw_video, folder / "video.mp4", result["15초_스크립트"], subtitle_name="06_subtitles.srt", place_name=place["place_name"])
                    history.add(place_id, channel)
                    if schedule:
                        publication = schedule.next_publication(datetime.now(KOREA_TIME), allocated)
                        if publication is None:
                            print(f"공개 기간 종료: {place_id} 영상은 생성되었으나 업로드하지 않았습니다", flush=True)
                            return
                        publish_at, immediate = publication
                        history.set_upload(place_id, publish_at, "진행중")
                        video_id = upload_video(ROOT / "res" / f"{channel}.pickle", folder / "video.mp4", result, publish_at, immediate)
                        history.set_upload(place_id, publish_at, "완료", video_id)
                        print(f"유튜브 업로드 완료: {channel} {publish_at.isoformat()} ({video_id})", flush=True)
                    print(f"완료: {folder}", flush=True)
                    completed += 1
                    if single:
                        return
                    break
            print(f"처리 종료: {completed}개 가게 완료", flush=True)
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except (CollectionError, requests.RequestException, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)
