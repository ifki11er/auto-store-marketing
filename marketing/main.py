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
import struct
import tempfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import sys
import time
import wave
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import requests
from openai import OpenAI
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from marketing.channel_regions import CHANNEL_ORDER, channel_for_address, iter_region_place_iterators
from marketing.place_history import PlaceHistory
from marketing.output_schedule import folder_allocations
from marketing.process_log import record_process
from marketing.token_usage import record_token_usage
from marketing.upload_schedule import KOREA_TIME, UploadSchedule
from marketing.prompts import build_planning_prompt, qwen_tts_enabled, select_video_format

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


class PageHTTPError(CollectionError):
    def __init__(self, page_name, status):
        self.status = status
        super().__init__(f"{page_name} HTTP {status}")


def failure_reason(exc, stage):
    if isinstance(exc, PageHTTPError):
        return f"http {exc.status}"
    if isinstance(exc, NoUsableReferencePhotos):
        return "too much text"
    if isinstance(exc, (TimeoutError, PlaywrightTimeout, requests.Timeout)) or isinstance(exc.__cause__, PlaywrightTimeout):
        return f"{stage} timeout"
    return f"{stage} {type(exc).__name__}"[:80]


def place_availability(browser, place_url):
    """원본 장소 페이지의 기본 탭에서 후기와 사진의 유무를 확인한다."""
    page = browser.new_page(user_agent=HEADERS["User-Agent"])
    try:
        response = page.goto(place_url.split("#")[0], wait_until="domcontentloaded", timeout=30000)
        if response and not response.ok:
            raise PageHTTPError("장소 페이지", response.status)
        page.locator(".main_detail .tab_primary .list_tab").wait_for(state="attached", timeout=15000)
        page.locator(".main_detail .section_grade").wait_for(state="attached", timeout=15000)
        tabs = page.locator(".main_detail .tab_primary .list_tab")
        return (tabs.locator('a[href="#review"]').count() > 0,
                tabs.locator('a[href="#photoview"]').count() > 0)
    except PlaywrightTimeout as exc:
        raise CollectionError("장소 페이지 응답 시간 초과") from exc
    finally:
        page.close()


def reviews_for(browser, place_url, availability=None, photo_folder=None, review_photos=None):
    if availability is None:
        availability = place_availability(browser, place_url)
    if not availability[0]:
        return []
    page = browser.new_page(user_agent=HEADERS["User-Agent"])
    try:
        response = page.goto(place_url.split("#")[0] + "#review", wait_until="domcontentloaded", timeout=30000)
        if response and not response.ok:
            raise PageHTTPError("후기 페이지", response.status)
        empty = page.get_by_text(re.compile(r"등록된 후기가 없습니다|후기가 없습니다|후기가 없어요|등록된 리뷰가 없습니다|리뷰가 없습니다"))
        section = page.locator(".section_review")
        section.or_(empty).first.wait_for(state="attached", timeout=15000)
        if not section.count() and empty.count():
            return []
        items = page.locator(".section_review .list_review > li")
        reviews = []
        photo_urls = set()
        for i in range(items.count()):
            item = items.nth(i)
            photo_names = []
            if photo_folder is not None and review_photos is not None:
                images = item.locator(".wrap_photo .list_photo img")
                for j in range(images.count()):
                    if len(review_photos) >= 5:
                        break
                    image = images.nth(j)
                    src = next((image.get_attribute(attr) for attr in ("data-original", "data-src", "src")
                                if image.get_attribute(attr)), None)
                    if not src:
                        continue
                    url = urljoin(page.url, src)
                    host = (urlsplit(url).hostname or "").lower()
                    if not (host.endswith(".daumcdn.net") or host.endswith(".kakaocdn.net")) or url in photo_urls:
                        continue
                    photo_urls.add(url)
                    try:
                        response = requests.get(url, headers=HEADERS, timeout=30)
                        response.raise_for_status()
                        mime = response.headers.get("Content-Type", "").split(";")[0].lower()
                        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime)
                        if not ext or not response.content:
                            continue
                        photo_folder.mkdir(parents=True, exist_ok=True)
                        path = photo_folder / f"review_photo_{len(review_photos) + 1}{ext}"
                        path.write_bytes(response.content)
                        review_photos.append(path)
                        photo_names.append(path.name)
                    except requests.RequestException:
                        continue
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
                review = {"내용": content,
                          "평점": score.nth(1).text_content().strip() if score.count() > 1 else None,
                          "작성일": date.first.text_content().strip() if date.count() else None}
                if photo_names:
                    review["사진"] = photo_names
                reviews.append(review)
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
            raise PageHTTPError("사진 페이지", response.status)
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
        for url in urls[::2] if len(urls) >= 20 else urls:
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
    content = [{"type": "input_text", "text": "사진을 한국어로 분석해 JSON 객체만 반환하세요. 가게_전체_특징은 사진 전체에서 확인되는 가게의 특징을 간결히 요약한 문자열 배열입니다. 사진별_정보는 입력 순서대로 모든 사진마다 파일명(문자열), 설명(문자열), 참조_사용_가능(JSON 불리언)을 가진 객체 배열입니다. 메뉴판, 가격표, 전단, 팸플릿, 안내문 등 글자가 주된 내용이거나 글자가 많이 포함된 사진은 참조_사용_가능을 false로 지정하세요. 그 외의 사진은 true로 지정하세요. 작은 간판이나 라벨이 일부 보이는 정도는 글자가 주된 내용이 아니라면 허용합니다. 사진에 보이지 않는 내용은 추측하지 말고 영상 장면, 구도, 연출이나 스토리를 제안하지 마세요."}]
    for index, photo in enumerate(photos, 1):
        content.append({"type": "input_text", "text": f"사진 {index}: {photo.name}"})
        mime = mimetypes.guess_type(photo.name)[0]
        data = base64.b64encode(photo.read_bytes()).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:{mime};base64,{data}", "detail": "auto"})
    return output_text(client.responses.create(model="gpt-6-luna", input=[{"role": "user", "content": content}], text={"format": {"type": "json_object"}}))


class NoUsableReferencePhotos(ValueError):
    """사용할 참조 사진이 없다."""


class PhotoAnalysisError(ValueError):
    """사진 분석 응답의 판별 결과를 확인할 수 없다."""


def reference_photo_candidates(analysis, photos):
    try:
        data = json.loads(analysis)
        details = data["사진별_정보"]
        if not isinstance(details, list):
            raise ValueError
        labels = {}
        for item in details:
            if not isinstance(item, dict) or not isinstance(item.get("파일명"), str) or type(item.get("참조_사용_가능")) is not bool or item["파일명"] in labels:
                raise ValueError
            labels[item["파일명"]] = item["참조_사용_가능"]
        if set(labels) != {photo.name for photo in photos}:
            raise ValueError
    except (TypeError, ValueError, KeyError) as exc:
        raise PhotoAnalysisError("사진별 분석 결과가 없거나 올바르지 않습니다. 사진 분석을 다시 실행하세요") from exc
    eligible = [photo for photo in photos if labels[photo.name]]
    if not eligible:
        raise NoUsableReferencePhotos("글자가 많은 사진을 제외하면 사용할 참조 사진이 없습니다")
    return data, eligible


def plan(client, place, reviews, analysis, photos, prior_concepts=None, revision_feedback=None, requested_format=None):
    video_format = requested_format or select_video_format()
    if os.environ.get("VIDEO_FORMAT", "").strip().lower() == "random":
        print(f"랜덤 선택 포맷: {video_format}", flush=True)
    instruction = build_planning_prompt(place.get("category_group_code"), video_format=video_format)
    photo_analysis, eligible_photos = reference_photo_candidates(analysis, photos)
    features = photo_analysis.get("가게_전체_특징", [])
    details = [item for item in photo_analysis["사진별_정보"] if item["참조_사용_가능"]]
    payload = {"오늘": datetime.now().astimezone().date().isoformat(), "가게": place, "후기": reviews,
               "가게_전체_특징": features}
    if prior_concepts is not None:
        instruction += "\n비교 테스트를 위해 결과 JSON의 중복_비교_요약에 이 기획의 핵심 상황과 전개만 80자 이내로 작성한다. 가게 특징, 포맷명, 기획 의도나 평가를 반복하지 않는다."
        if prior_concepts:
            payload["이전에_기획한_콘셉트"] = prior_concepts
    payload.update({"사진_선택용_상세정보": details,
                    "사진_목록": [{"분석_순번": i, "파일명": photo.name} for i, photo in enumerate(eligible_photos, 1)],
                    "MiniMax_가이드": (ROOT / "marketing" / "prompts" / "minimax_qwen_guide.md" if qwen_tts_enabled() else GUIDE).read_text(encoding="utf-8-sig")})
    base_input = "다음 자료를 바탕으로 JSON 객체를 작성하세요.\n" + json.dumps(payload, ensure_ascii=False)
    if revision_feedback:
        base_input += "\n이전 대본을 Qwen으로 읽은 결과: " + revision_feedback + " 핵심 매력과 흐름을 살리면서 15초 이내에 읽을 수 있도록 대본과 전체 기획을 다시 작성하세요. 짧게 줄이더라도 자연스러운 구어체와 완결된 문장 끝맺음을 유지하세요."
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
            validate_plan(result, eligible_photos)
            break
        except (ValueError, KeyError, TypeError) as exc:
            error = exc
            print(f"기획 검증 실패 ({attempt + 1}/3): {exc}", flush=True)
    else:
        if last_result is None:
            raise ValueError("세 번의 기획 응답 모두 사용할 수 있는 JSON 객체가 아닙니다") from error
        validate_video_caption_instructions(last_result)
        if qwen_tts_enabled():
            validate_silent_video_prompt(last_result)
        else:
            validate_narration_voice(last_result)
            validate_dialogue_format(last_result)
        last_shots = last_result.get("15초_스크립트")
        if isinstance(last_shots, list) and any(reports_reference_material(shot.get("내레이션", "")) for shot in last_shots if isinstance(shot, dict)):
            raise ValueError("대사에 후기나 사진을 분석하는 표현이 남아 있습니다") from error
        print("기획 검증에 세 번 실패하여 마지막으로 받은 JSON 객체를 그대로 사용합니다.", flush=True)
        result = last_result
    shots = result.get("15초_스크립트")
    if os.environ.get("VIDEO_FORMAT", "").strip().lower() == "random":
        result["랜덤_선택_포맷"] = video_format
    for shot in shots if isinstance(shots, list) else []:
        if isinstance(shot, dict):
            shot.pop("자막_스타일", None)
            shot.pop("자막", None)
    result["분석_사진_목록"] = photo_manifest(photos)
    result["참조_가능_사진"] = [photo.name for photo in eligible_photos]
    selected_photos(result, photos)
    return result


def reports_reference_material(text):
    if not isinstance(text, str):
        return False
    return bool(re.search(r"(?:후기|리뷰)(?:에\s*따르면|에서|에는|를\s*(?:보면|살펴보면)|가\s*보여|(?:가|에서|에)\s*[^.!?\n]{0,40}(?:언급|나와|보여|확인))|사진(?:에서|을\s*보면)\s*[^.!?\n]{0,40}(?:확인|보여)", text))


def validate_video_caption_instructions(result):
    prompt = result.get("minimax_prompt")
    if not isinstance(prompt, str):
        return
    without_dialogue = re.sub(r"<d>.*?</d>", "", prompt, flags=re.DOTALL | re.IGNORECASE)
    shots = result.get("15초_스크립트")
    captions = [line.strip() for shot in shots if isinstance(shot, dict)
                for line in (shot.get("내레이션") if isinstance(shot.get("내레이션"), str) else "").splitlines()
                if len(line.strip()) >= 4] if isinstance(shots, list) else []
    for sentence in re.split(r"[.!?;\n]", without_dialogue):
        for caption in captions:
            quoted = re.search(r"[\"'“‘]" + re.escape(caption) + r"[\"'”’]", sentence)
            action = re.search(r"\b(?:add|display|render|overlay|insert|write|put)\b", sentence[:quoted.start()], re.IGNORECASE) if quoted else None
            if action and not re.search(r"\b(?:do\s+not|don't|never|without|no)\s+(?:\w+\s+){0,3}$", sentence[:action.start()], re.IGNORECASE):
                raise ValueError("minimax_prompt에 후처리 자막 문구를 표시하는 지시가 있습니다. 해당 지시를 제거하세요")
        if (re.search(r"\bpost[\s-]*(?:production|produced)\b", sentence, re.IGNORECASE)
                and re.search(r"\b(?:captions?|subtitles?|titles?|text|lettering|overlays?|display|show)\b", sentence, re.IGNORECASE)):
            raise ValueError("minimax_prompt에 후처리 자막 또는 글자에 관한 언급이 있습니다. 해당 언급을 제거하세요")
        if re.search(r"\b(?:leave|reserve|keep|make)\b.{0,100}\b(?:space|area|room)\b.{0,100}\b(?:captions?|subtitles?|on[- ]screen\s+text|text\s+overlays?)\b", sentence, re.IGNORECASE):
            raise ValueError("minimax_prompt에 자막을 위한 공간을 남기는 지시가 있습니다. 해당 지시를 제거하세요")
        pattern = r"\b(?:add|display|show|render|generate|include|overlay|insert|place|write|put)\b.{0,100}?\b(?:captions?|subtitles?|on[- ]screen\s+text|text\s+overlays?)\b|\b(?:captions?|subtitles?)\b.{0,80}?\b(?:appear|displayed|shown|rendered|overlaid)\b"
        for match in re.finditer(pattern, sentence, re.IGNORECASE):
            negative_before = re.search(r"\b(?:do\s+not|don't|never|without|no)\s+(?:\w+\s+){0,3}$", sentence[:match.start()], re.IGNORECASE)
            negative_after_verb = re.match(r"\b(?:add|display|show|render|generate|include|overlay|insert|place|write|put)\s+(?:no|not|zero)\b", match.group(), re.IGNORECASE)
            if not negative_before and not negative_after_verb:
                raise ValueError(f"minimax_prompt에 자막을 생성하거나 표시하는 지시가 있습니다: {match.group()}. 해당 지시를 제거하세요")


def validate_narration_voice(result):
    shots = result.get("15초_스크립트")
    if not isinstance(shots, list):
        return
    narrator_ids = {shot.get("화자_ID") for shot in shots if isinstance(shot, dict) and shot.get("발화_유형") == "나레이션"}
    if not narrator_ids:
        return
    prompt = result.get("minimax_prompt")
    if not isinstance(prompt, str) or "<Audio 1>" not in prompt:
        raise ValueError("나레이션에는 제공된 WAV의 목소리 참조 <Audio 1>이 필요합니다")
    voices = result.get("음성_연출")
    if isinstance(voices, list):
        for voice in voices:
            if not isinstance(voice, dict) or voice.get("화자_ID") not in narrator_ids:
                continue
            description = voice.get("목소리_설명")
            if isinstance(description, str) and re.search(r"남성|여성|남자|여자|\b(?:male|female|masculine|feminine)\b", description, re.IGNORECASE):
                raise ValueError("나레이션의 성별을 따로 지정하지 말고 <Audio 1> 참조 음성의 목소리를 사용하세요")
    without_dialogue = re.sub(r"<d>.*?</d>", "", prompt, flags=re.DOTALL | re.IGNORECASE)
    gender = r"(?:female|male|woman|man|feminine|masculine)"
    narrator = r"(?:narrator|narration|voice[ -]?over|narrative\s+voice)"
    if re.search(rf"\b{gender}\s+(?:\w+\s+){{0,2}}{narrator}\b|\b{narrator}\s+(?:is\s+|with\s+(?:a\s+)?|has\s+(?:a\s+)?|as\s+(?:a\s+)?)?{gender}\b", without_dialogue, re.IGNORECASE):
        raise ValueError("minimax_prompt에 나레이션의 성별을 별도로 지정하지 말고 <Audio 1> 참조 음성을 사용하세요")


def validate_dialogue_format(result):
    shots = result.get("15초_스크립트")
    prompt = result.get("minimax_prompt")
    if not isinstance(shots, list) or not isinstance(prompt, str):
        raise ValueError("대사 형식을 확인할 스크립트 또는 영상 프롬프트가 없습니다")
    spoken_shots = [shot for shot in shots if isinstance(shot, dict) and isinstance(shot.get("내레이션"), str) and shot["내레이션"].strip()]
    matches = list(re.finditer(r"<d>((?:(?!<d>|</d>).)*)</d>", prompt, flags=re.DOTALL))
    if len(matches) != len(spoken_shots):
        raise ValueError("영상 프롬프트의 대사가 스크립트 대사와 순서대로 일치하지 않습니다")
    speaker_order = []
    previous_end = 0
    for shot, match in zip(spoken_shots, matches):
        speaker = shot.get("화자_ID")
        if not isinstance(speaker, str) or not re.fullmatch(r"S[1-9][0-9]*", speaker):
            raise ValueError("화자_ID는 S1, S2 등의 형식이어야 합니다")
        if speaker not in speaker_order:
            speaker_order.append(speaker)
            if speaker != f"S{len(speaker_order)}":
                raise ValueError("화자_ID는 발화 순서대로 S1부터 지정해야 합니다")
        if f"({speaker})" not in prompt[previous_end:match.start()]:
            raise ValueError(f"각 대사 앞에 화자 ID ({speaker})를 표시해야 합니다")
        dialogue = match.group(1)
        if not dialogue.startswith("[Korean] "):
            raise ValueError("각 대사는 <d>[Korean] 대사</d> 형식이어야 합니다")
        if dialogue[len("[Korean] "):].strip() != shot["내레이션"].strip():
            raise ValueError("영상 프롬프트의 대사가 스크립트 대사와 순서대로 일치하지 않습니다")
        previous_end = match.end()


def validate_silent_video_prompt(result):
    prompt = result.get("minimax_prompt")
    if not isinstance(prompt, str) or re.search(r"<\s*/?\s*d\s*>|<Audio\s+\d+>", prompt, re.IGNORECASE):
        raise ValueError("영상 생성 프롬프트에는 음성 참조나 대사를 넣을 수 없습니다")


def validate_plan(result, photos):
    validate_video_caption_instructions(result)
    if qwen_tts_enabled():
        validate_silent_video_prompt(result)
    else:
        validate_narration_voice(result)
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
        narration = shot.get("내레이션")
        if not isinstance(narration, str) or re.search(r"[0-9]", narration):
            raise ValueError("대사는 문자열이어야 하며 숫자는 실제 읽는 한글 발음으로 작성해야 합니다")
        if reports_reference_material(narration):
            raise ValueError("대사에서 후기나 사진을 분석한 과정을 보고하지 말고 가게의 매력을 자연스럽게 소개하세요")
        if shot.get("발화_유형") not in (("나레이션", "없음") if qwen_tts_enabled() else ("나레이션", "화면발화", "없음")):
            raise ValueError("발화_유형이 선택한 음성 방식에 맞지 않습니다")
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
    if not qwen_tts_enabled():
        validate_dialogue_format(result)
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
    eligible = result.get("참조_가능_사진")
    if not isinstance(eligible, list) or not all(isinstance(name, str) for name in eligible):
        raise ValueError("참조 가능한 사진 정보가 없습니다. 4번 사진 분석과 5번 기획을 다시 실행하세요")
    selected = result.get("선택_사진")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 9:
        raise ValueError("기획의 선택 사진은 1~9장이어야 합니다. 5번 테스트를 다시 실행하세요")
    names = [item.get("파일명") if isinstance(item, dict) else None for item in selected]
    if len(set(names)) != len(names) or any(name not in available or name not in eligible for name in names):
        raise ValueError("기획에 지정된 사진이 없거나 중복되었거나 참조할 수 없는 사진입니다")
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


def generate_video(base, photos, prompt, output, reference_audio=None, workflow_name=None):
    if not 1 <= len(photos) <= 9:
        raise ValueError("ComfyUI에 전달할 사진은 1~9장이어야 합니다")
    if "<Audio 1>" in prompt and reference_audio is None:
        raise ValueError("프롬프트의 <Audio 1>에 연결할 참조 WAV가 없습니다")
    base = base.rstrip("/")
    workflow_name = workflow_name or os.environ.get("VIDEO_WORKFLOW", "Deno_MiniMax_H3_R2V_Turbo_8plus2").strip()
    if workflow_name == "Deno_MiniMax_H3_R2V_Turbo_8plus2":
        workflow_path, reference_node, prompt_node, prompt_field, output_node, output_field = WORKFLOW, "235", "186", "text", "144", "gifs"
    elif workflow_name == "Deno_minimax_final_R2V_7plus1_memopt_Sol":
        workflow_path, reference_node, prompt_node, prompt_field, output_node, output_field = ROOT / "workflow" / "Deno_minimax_final_R2V_7plus1_memopt_Sol.json", "214", "186", "text", "144", "gifs"
    elif workflow_name == "video_fasth3_ominiref_r2v":
        workflow_path, reference_node, prompt_node, prompt_field, output_node, output_field = ROOT / "workflow" / "video_fasth3_ominiref_r2v.json", "136", "138", "value", "92", "images"
    else:
        raise ValueError(f"지원하지 않는 워크플로우: {workflow_name}")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8-sig"))
    inputs = workflow[reference_node]["inputs"]
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
    no_speech = "No human speech, narration, singing, voices, vocal sounds or spoken words. Instrumental music and non-vocal sound effects only. " if qwen_tts_enabled() else ""
    workflow[prompt_node]["inputs"][prompt_field] = prompt + "\n" + no_speech + "Do not generate subtitles, captions, titles, or other added on-screen text. Preserve existing lettering in reference images."
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
    visit(output_node)
    payload = {key: workflow[key] for key in needed}
    response = requests.post(base + "/prompt", json={"prompt": payload}, timeout=60)
    response.raise_for_status()
    if reference_audio is not None:
        print(f"나레이션 참조 WAV 전달: {reference_audio.name}", flush=True)
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
        files = history.get("outputs", {}).get(output_node, {}).get(output_field, [])
        if not files:
            raise RuntimeError("ComfyUI 결과에 영상이 없습니다")
        video = files[0]
        response = requests.get(base + "/view", params={"filename": video["filename"], "subfolder": video.get("subfolder", ""), "type": video.get("type", "output")}, timeout=120)
        response.raise_for_status()
        output.write_bytes(response.content)
        return prompt_id
    raise TimeoutError(f"ComfyUI 작업 대기 시간 초과: {prompt_id}")


# 생성 자막 검수는 비활성화한다. 기존 구현은 필요할 때 다시 활성화할 수 있다.
# def check_generated_subtitles(client, raw_video):
#     ffmpeg = ROOT / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
#     if not ffmpeg.is_file():
#         raise FileNotFoundError(f"FFmpeg 실행 파일이 없습니다: {ffmpeg}")
#     with tempfile.TemporaryDirectory() as temp_dir:
#         pattern = str(Path(temp_dir) / "frame_%02d.jpg")
#         subprocess.run([str(ffmpeg), "-loglevel", "error", "-i", str(raw_video.resolve()),
#                         "-vf", "fps=1/3,scale=-2:768", "-frames:v", "5", "-q:v", "3", pattern], check=True)
#         frames = sorted(Path(temp_dir).glob("frame_*.jpg"))
#         if len(frames) != 5:
#             raise ValueError(f"자막 검수 화면이 5장이어야 합니다: {len(frames)}장")
#         content = [{"type": "input_text", "text": (
#             "다음은 자막 합성 전 15초 영상에서 3초 간격으로 추출한 화면입니다. "
#             "영상 위에 생성된 대사 자막, 설명 자막 또는 제목이 한 화면이라도 있는지 확인하세요. "
#             "매장 간판, 메뉴판, 제품 포장이나 화면 속 물체에 원래 속한 글자와 워터마크는 자막이 아닙니다. "
#             "화면에 덧씌운 문구와 실제 사물의 글자를 구분하고, 확실하지 않으면 불확실로 판정하세요. "
#             "JSON 객체만 반환하세요: 판정은 '있음', '없음', '불확실' 중 하나, 이유는 간결한 문자열."
#         )}]
#         for index, frame in enumerate(frames):
#             content.append({"type": "input_text", "text": f"화면 {index + 1} (약 {index * 3 + 1.5:.1f}초)"})
#             data = base64.b64encode(frame.read_bytes()).decode("ascii")
#             content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{data}", "detail": "high"})
#         response = client.responses.create(model="gpt-6-luna", input=[{"role": "user", "content": content}],
#                                            text={"format": {"type": "json_object"}})
#     try:
#         result = json.loads(output_text(response))
#         if not isinstance(result, dict) or result.get("판정") not in ("있음", "없음", "불확실") or not isinstance(result.get("이유"), str):
#             raise ValueError("올바르지 않은 판정")
#     except (ValueError, TypeError) as exc:
#         raise ValueError("영상 자막 검수 응답이 올바르지 않습니다") from exc
#     return result


def generate_video_without_subtitles(client, base, photos, prompt, output, reference_audio=None, workflow_name=None):
    """생성 자막 검수 없이 원본 영상을 생성한다."""
    # GPT 이미지 자막 검수와 검출 시 재생성은 비활성화한다.
    # inspection_client = recorded_client(client, output.parent, "subtitle_check", "06")
    # candidate_number = 0
    # for attempt in range(1, 4):
    #     while True:
    #         candidate_number += 1
    #         candidate = output.with_name(f"{output.stem}_attempt_{candidate_number}{output.suffix}")
    #         if not candidate.exists():
    #             break
    #     prompt_id = generate_video(base, photos, prompt, candidate, reference_audio, workflow_name)
    #     result = check_generated_subtitles(inspection_client, candidate)
    #     print(f"영상 자막 검수 ({attempt}/3): {result['판정']} - {result['이유']}", flush=True)
    #     if result["판정"] == "없음":
    #         shutil.copy2(candidate, output)
    #         return prompt_id
    #     if result["판정"] == "불확실":
    #         raise ValueError(f"영상 자막 검수 결과가 불확실합니다: {result['이유']}")
    # raise ValueError("영상 세 번 모두 생성 자막이 감지되어 자막 합성을 중단합니다")
    return generate_video(base, photos, prompt, output, reference_audio, workflow_name)


def wrap_title(title, font_path):
    """기존 TrueType 폰트의 글자 폭으로 제목 줄을 나눈다."""
    data = font_path.read_bytes()
    u16 = lambda offset: struct.unpack_from(">H", data, offset)[0]
    u32 = lambda offset: struct.unpack_from(">I", data, offset)[0]
    tables = {}
    for i in range(u16(4)):
        offset = 12 + 16 * i
        tables[data[offset:offset + 4]] = u32(offset + 8)
    head, hhea, hmtx, cmap = (tables[name] for name in (b"head", b"hhea", b"hmtx", b"cmap"))
    units = u16(head + 18)
    metrics = u16(hhea + 34)
    subtables = []
    for i in range(u16(cmap + 2)):
        record = cmap + 4 + 8 * i
        platform, encoding = u16(record), u16(record + 2)
        subtable = cmap + u32(record + 4)
        fmt = u16(subtable)
        if fmt in (4, 12) and (platform == 0 or (platform == 3 and encoding in (1, 10))):
            subtables.append((fmt, subtable))
    if not subtables:
        raise ValueError("제목 폰트의 문자 매핑을 읽을 수 없습니다")
    fmt, subtable = max(subtables)

    def glyph(code):
        if fmt == 12:
            for i in range(u32(subtable + 12)):
                group = subtable + 16 + 12 * i
                start, end, first = struct.unpack_from(">III", data, group)
                if start <= code <= end:
                    return first + code - start
            return 0
        segments = u16(subtable + 6) // 2
        ends = subtable + 14
        starts = ends + 2 * segments + 2
        deltas = starts + 2 * segments
        ranges = deltas + 2 * segments
        for i in range(segments):
            if u16(starts + 2 * i) <= code <= u16(ends + 2 * i):
                delta, distance = u16(deltas + 2 * i), u16(ranges + 2 * i)
                if not distance:
                    return (code + delta) & 0xffff
                index = u16(ranges + 2 * i + distance + 2 * (code - u16(starts + 2 * i)))
                return (index + delta) & 0xffff if index else 0
        return 0

    def width(text):
        return sum(u16(hmtx + 4 * min(glyph(ord(char)), metrics - 1)) for char in text) * 110 / units

    # 제목은 화면 양쪽에 45씩 남기고 외곽선과 렌더링 오차 여유를 둔다.
    limit = 1080 - 2 * 45 - 20
    lines = []
    remaining = title
    while remaining:
        end = 0
        while end < len(remaining) and width(remaining[:end + 1]) <= limit:
            end += 1
        if end == len(remaining):
            lines.append(remaining)
            break
        if end == 0:
            raise ValueError("제목 글자가 화면에 표시하기에 너무 넓습니다")
        space = remaining.rfind(" ", 0, end + 1)
        if space > 0 and width(remaining[:space]) >= limit * 0.75:
            lines.append(remaining[:space])
            remaining = remaining[space + 1:].lstrip()
        else:
            lines.append(remaining[:end])
            remaining = remaining[end:].lstrip()
    return r"\N".join(lines)


def add_subtitles(raw_video, output, shots, subtitle_name="subtitles.srt", place_name="", video_title="", voice_clips=None, subtitle_segments=None):
    """기획 자막을 SRT와 ASS로 저장하고 장면별 스타일을 합성한다."""
    if voice_clips:
        ffprobe = ROOT / "bin" / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if not ffprobe.is_file():
            raise FileNotFoundError(f"FFprobe 실행 파일이 없습니다: {ffprobe}")
        duration = float(subprocess.check_output([str(ffprobe), "-v", "error", "-show_entries", "format=duration",
                                                  "-of", "default=noprint_wrappers=1:nokey=1", str(raw_video.resolve())], text=True).strip())
        if any(clip["start"] + clip["duration"] > duration + 0.01 for clip in voice_clips):
            raise ValueError(f"Qwen 음성이 원본 영상 길이 {duration:.2f}초를 초과합니다")
    watermark_setting = os.environ.get("WATERMARK_ENABLED", "true").strip().lower()
    if watermark_setting not in ("true", "false"):
        raise ValueError("WATERMARK_ENABLED는 true 또는 false로 설정하세요")
    watermark_enabled = watermark_setting == "true"
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
    heading = place_name
    if heading.strip():
        title = heading.strip().replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        title = wrap_title(title, ROOT / "font" / "Jua-Regular.ttf")
        events.append(f"Dialogue: 0,{timestamp(0, True)},{timestamp(shots[-1]['종료초'], True)},Default,,0,0,0,,{{\\an8\\q2\\fs110\\c&H0080FF&}}{title}")
    if voice_clips and subtitle_segments is None:
        raise ValueError("Qwen 음성에 맞춘 Whisper 자막이 없습니다")
    if subtitle_segments is not None:
        subtitle_items = subtitle_segments
    else:
        subtitle_items = []
        for shot in shots:
            words = shot["내레이션"].split()
            if not words:
                continue
            start, end = float(shot["시작초"]), float(shot["종료초"])
            weights = [max(1, len(word)) for word in words]
            total, elapsed = sum(weights), 0
            for word, weight in zip(words, weights):
                subtitle_items.append({"text": word, "start": start + (end - start) * elapsed / total,
                                       "end": start + (end - start) * (elapsed + weight) / total})
                elapsed += weight
    for item in subtitle_items:
        text = item["text"].strip()
        if not text:
            continue
        start, end = item["start"], item["end"]
        entries.append(f"{len(entries) + 1}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n")
        text = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r\n", "\n").replace("\n", r"\N")
        tags = r"{\an5\fs80\c&HFFFFFF&}"
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
    if watermark_enabled and not watermark.is_file():
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
        command = [str(ffmpeg), "-y", "-i", str(raw_video.resolve())]
        if watermark_enabled:
            command += ["-i", str(watermark)]
            filters = (video_filter + ";[1:v]scale=120:-1,format=rgba,colorchannelmixer=aa=0.55[mark];"
                       "[base][mark]overlay=W-w-24:H-h-24:format=auto[v]")
            video_map = "[v]"
        else:
            filters = video_filter
            video_map = "[base]"
        if voice_clips:
            offset = 2 if watermark_enabled else 1
            for clip in voice_clips:
                command += ["-i", clip["path"]]
            audio_filters = ["[0:a]volume=0.55[bgm]"]
            for index, clip in enumerate(voice_clips):
                delay = round(clip["start"] * 1000)
                audio_filters.append(f"[{offset + index}:a]adelay={delay}:all=1[voice{index}]")
            inputs = "[bgm]" + "".join(f"[voice{index}]" for index in range(len(voice_clips)))
            audio_filters.append(f"{inputs}amix=inputs={len(voice_clips) + 1}:duration=first:normalize=0[aout]")
            filters += ";" + ";".join(audio_filters)
            audio_map, audio_codec = "[aout]", "aac"
        else:
            audio_map, audio_codec = "0:a?", "copy"
        command += ["-filter_complex", filters, "-map", video_map, "-map", audio_map,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", audio_codec,
                    str(output.resolve())]
        subprocess.run(command, cwd=temp_dir, check=True)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def recorded_client(client, folder, stage, number):
    """인증 정보를 제외하고 GPT 요청 본문과 전체 응답을 저장한다."""
    attempts = 0
    def create(**kwargs):
        nonlocal attempts
        attempts += 1
        suffix = f"_{attempts}" if stage in ("plan", "subtitle_check") and attempts > 1 else ""
        write_json(folder / f"{number}_gpt_{stage}_request{suffix}.json", kwargs)
        response = client.responses.create(**kwargs)
        record_token_usage(folder, stage, response)
        write_json(folder / f"{number}_gpt_{stage}_response{suffix}.json", response.model_dump(mode="json"))
        return response

    return SimpleNamespace(responses=SimpleNamespace(create=create))


def is_chain_place(place):
    category = (place.get("category_name") or "").rsplit(">", 1)[-1].strip()
    name = (place.get("place_name") or "").strip()
    return bool(category and name.startswith(category))


def result_folder(output, place_id, publication, now):
    if publication is None:
        raise ValueError("공개 예정 시간을 배정할 수 없습니다")
    publish_at, _ = publication
    day = publish_at.astimezone(KOREA_TIME)
    slot = day.strftime("%H%M")
    return output / day.strftime("%Y-%m-%d") / slot / place_id


def next_result_publication(schedule, allocated):
    planned = schedule.next_planned_publication(allocated)
    return (planned, False) if planned is not None else None


def voice_silence_intervals(path, duration, minimum):
    """FFmpeg가 감지한 무음 구간을 초 단위로 반환한다."""
    ffmpeg = ROOT / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    result = subprocess.run([str(ffmpeg), "-hide_banner", "-nostats", "-i", str(path), "-af",
                             f"silencedetect=noise=-45dB:d={minimum}", "-f", "null", "-"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    events = [(m.group(1), float(m.group(2))) for m in re.finditer(r"silence_(start|end):\s*([\d.]+)", result.stderr)]
    intervals, start = [], None
    for kind, value in events:
        if kind == "start":
            start = value
        elif start is not None:
            intervals.append((start, value))
            start = None
    if start is not None:
        intervals.append((start, duration))
    return intervals


def shorten_voice_pauses(path, duration):
    """말 사이 0.5초 이상 무음을 0.3초 남기고 줄인다."""
    intervals = voice_silence_intervals(path, duration, 0.5)
    cuts = [(start + 0.15, end - 0.15) for start, end in intervals
            if start > 0.001 and end < duration - 0.05 and end - start > 0.5]
    if not cuts:
        return path, duration
    shortened = path.with_name(path.stem + "_pauses.wav")
    with wave.open(str(path), "rb") as source:
        rate, total = source.getframerate(), source.getnframes()
        boundaries = [(max(0, int(start * rate)), min(total, int(end * rate))) for start, end in cuts]
        with wave.open(str(shortened), "wb") as target:
            target.setparams(source.getparams())
            position = 0
            for start, end in boundaries:
                source.setpos(position)
                target.writeframes(source.readframes(start - position))
                position = end
            source.setpos(position)
            target.writeframes(source.readframes(total - position))
    removed = sum(end - start for start, end in boundaries)
    return shortened, (total - removed) / rate


def trim_voice_boundary_silence(path, duration):
    """15초가 넘는 WAV에서 앞뒤 무음만 일부 줄여 들어갈 때만 새 파일을 만든다."""
    intervals = voice_silence_intervals(path, duration, 0.1)
    padding = 0.05
    first = max(0.0, intervals[0][1] - padding) if intervals and intervals[0][0] <= 0.001 else 0.0
    last = min(duration, intervals[-1][0] + padding) if intervals and intervals[-1][1] >= duration - 0.05 else duration
    if last <= first or last - first > 15 or (first == 0 and last == duration):
        return path, duration
    trimmed = path.with_name(path.stem + "_trimmed.wav")
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        first_frame = max(0, int(first * rate))
        last_frame = min(source.getnframes(), int(last * rate))
        source.setpos(first_frame)
        audio = source.readframes(last_frame - first_frame)
        with wave.open(str(trimmed), "wb") as target:
            target.setparams(source.getparams())
            target.writeframes(audio)
    return trimmed, (last_frame - first_frame) / rate


def generate_qwen_batch(jobs, client=None):
    if not jobs:
        return {}
    voice_python = Path.home() / "anaconda3" / "envs" / "ai_voice" / ("python.exe" if os.name == "nt" else "bin/python")
    python = os.environ.get("QWEN_TTS_PYTHON", "").strip() or (str(voice_python) if voice_python.is_file() else sys.executable)
    ref = Path(os.environ.get("QWEN_TTS_REFERENCE", "").strip() or ROOT / "res" / "kr_male_30_typecast_changsu.wav")
    results, whisper_jobs = {}, []
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    worker = subprocess.Popen([python, "-u", str(ROOT / "marketing" / "qwen_voice_batch.py"), str(ref.resolve())],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", env=child_env)
    try:
        for job in jobs:
            folder = job["folder"].resolve()
            key = str(folder)
            try:
                candidate_number = 0
                for attempt in range(1, 4):
                    shots = job["result"]["15초_스크립트"]
                    text = " ".join(shot["내레이션"].strip() for shot in shots if shot.get("내레이션", "").strip())
                    if not text:
                        raise ValueError("생성할 나레이션이 없습니다")
                    while True:
                        candidate_number += 1
                        candidate = folder / f"06_narration_attempt_{candidate_number}.wav"
                        if not candidate.exists():
                            break
                    worker.stdin.write(json.dumps({"text": text, "path": str(candidate)}, ensure_ascii=False) + "\n")
                    worker.stdin.flush()
                    line = worker.stdout.readline()
                    if not line:
                        raise RuntimeError("Qwen 음성 생성 프로세스가 종료되었습니다")
                    voice = json.loads(line)
                    if voice.get("error"):
                        raise ValueError(voice["error"])
                    duration = float(voice["duration"])
                    candidate, shortened_duration = shorten_voice_pauses(candidate, duration)
                    if shortened_duration < duration:
                        print(f"말 사이 무음을 줄였습니다: {duration:.2f}초 → {shortened_duration:.2f}초", flush=True)
                    duration = shortened_duration
                    if duration > 15:
                        candidate, trimmed_duration = trim_voice_boundary_silence(candidate, duration)
                        if trimmed_duration < duration:
                            print(f"음성 앞뒤 무음을 줄였습니다: {duration:.2f}초 → {trimmed_duration:.2f}초", flush=True)
                        duration = trimmed_duration
                    if duration <= 15:
                        path = folder / "06_narration.wav"
                        shutil.copy2(candidate, path)
                        whisper_jobs.append({"folder": key, "path": str(path), "text": text, "duration": duration,
                                             "srt": str(folder / "06_subtitles.srt")})
                        results[key] = {"folder": key, "clips": [{"path": str(path), "start": 0.0, "duration": duration}]}
                        break
                    if attempt == 3:
                        raise ValueError(f"세 번 기획해도 음성이 15초를 초과합니다: {duration:.2f}초")
                    print(f"음성 {duration:.2f}초: 15초 이내로 대본을 다시 기획합니다 ({attempt}/3)", flush=True)
                    if not all(name in job for name in ("place", "reviews", "analysis", "photos")):
                        raise ValueError("대본 재기획에 필요한 가게 정보, 후기, 사진 분석이 없습니다")
                    replan_client = recorded_client(client or OpenAI(), folder, f"replan_{attempt + 1}", "05")
                    result = plan(replan_client, job["place"], job["reviews"], job["analysis"], job["photos"],
                                  revision_feedback=f"음성 길이가 {duration:.2f}초로 15초를 초과했습니다.", requested_format=job.get("format"))
                    job["result"] = result
                    write_json(folder / f"05_plan_attempt_{attempt + 1}.json", result)
                    write_json(folder / "05_plan.json", result)
                    write_json(folder / "05_marketing.json", {"가게": job["place"], "후기": job["reviews"], "사진_분석": job["analysis"], "기획": result})
            except Exception as exc:
                results[key] = {"folder": key, "error": str(exc)}
    finally:
        try:
            worker.stdin.close()
        except BrokenPipeError:
            pass
        worker.stdout.close()
        worker.wait()
    if whisper_jobs:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_path = Path(temp_dir) / "whisper_jobs.json"
            write_json(jobs_path, whisper_jobs)
            subprocess.run([python, str(ROOT / "marketing" / "wav_to_srt_batch.py"), str(jobs_path), str(ROOT / "bin")], check=True, env=child_env)
            captions = json.loads(jobs_path.with_name("whisper_results.json").read_text(encoding="utf-8"))
        for item in captions:
            if item.get("error"):
                results[item["folder"]] = item
            else:
                results[item["folder"]]["segments"] = item["segments"]
    return results


def main(argv=None, single=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "kakao_places_filtered.db")
    parser.add_argument("--place-id", help="특정 가게만 처리")
    parser.add_argument("--after-id", default="", help="이 ID 다음부터 검색")
    parser.add_argument("--max-photos", type=int, default=10, help="다운로드할 최대 사진 수 (최대 10장)")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--workflow", choices=("Deno_MiniMax_H3_R2V_Turbo_8plus2", "video_fasth3_ominiref_r2v", "Deno_minimax_final_R2V_7plus1_memopt_Sol"),
                        default=os.environ.get("VIDEO_WORKFLOW", "Deno_MiniMax_H3_R2V_Turbo_8plus2").strip(),
                        help="영상 생성 워크플로우 (.env의 VIDEO_WORKFLOW보다 우선)")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    args = parser.parse_args(argv)
    if args.workflow not in ("Deno_MiniMax_H3_R2V_Turbo_8plus2", "video_fasth3_ominiref_r2v", "Deno_minimax_final_R2V_7plus1_memopt_Sol"):
        parser.error("VIDEO_WORKFLOW에 지원하는 워크플로우 이름을 입력하세요")
    build_planning_prompt(None)  # 영상 생성 전에 포맷 설정을 확인한다.
    if args.max_photos is not None and args.max_photos < 1:
        parser.error("--max-photos는 1 이상이어야 합니다")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY 환경 변수가 필요합니다")
    history = PlaceHistory(args.db)
    schedule = UploadSchedule.from_environment(os.environ)
    allocations = folder_allocations(args.output, schedule)
    client = OpenAI()
    with closing(history.db) as db, db, sync_playwright() as playwright:
        if args.place_id:
            groups = (iter(db.execute("SELECT * FROM places WHERE place_id = ? AND generated_at IS NULL AND (upload_status IS NULL OR upload_status != '완료') AND (posible IS NULL OR posible = '')", (args.place_id,))),)
        else:
            groups = iter_region_place_iterators(db, args.after_id)
        browser = playwright.chromium.launch(headless=True)
        completed_paths = []
        prepared = []
        pending_allocations = {channel: list(allocations[channel]) for channel in CHANNEL_ORDER}
        try:
            completed = 0
            for rows in groups:
                if schedule and all(next_result_publication(schedule,
                    pending_allocations[channel]) is None
                    for channel in CHANNEL_ORDER):
                    print("모든 지역의 공개 기간 일정이 완료되었습니다", flush=True)
                    break
                for row in rows:
                    place = dict(row)
                    for key in ("generated_at", "publish_at", "uploaded_at", "channel", "posible", "upload_status", "youtube_video_id"):
                        place.pop(key, None)
                    place_id = str(place["place_id"])
                    stage = "data"
                    try:
                        if place.get("category_group_code") in {"MT1", "PS3", "SC4", "OL7", "PO3", "AG2", "BK9", "CS2", "HP8", "PK6", "PM9", "SW8"}:
                            print(f"제외 업종: {place_id} ({place['category_group_code']}) 건너뜀", flush=True)
                            continue
                        if is_chain_place(place):
                            print(f"체인점: {place_id} {place['place_name']} 건너뜀", flush=True)
                            continue
                        if not place_id.isdigit() or not place.get("place_url"):
                            history.set_failure(place_id, "invalid url")
                            print(f"건너뜀: 유효하지 않은 ID 또는 URL ({place_id})", flush=True)
                            continue
                        parsed = urlsplit(place["place_url"])
                        if parsed.hostname != "place.map.kakao.com" or parsed.path.strip("/") != place_id:
                            history.set_failure(place_id, "invalid url")
                            print(f"건너뜀: 유효하지 않은 카카오 장소 URL ({place_id})", flush=True)
                            continue
                        channel = channel_for_address(place.get("address_name") or place.get("road_address_name"))
                        if schedule:
                            if not channel:
                                history.set_failure(place_id, "invalid region")
                                print(f"지역 채널 없음: {place_id} 건너뜀", flush=True)
                                continue
                            allocated = pending_allocations[channel]
                            if next_result_publication(schedule, allocated) is None:
                                break
                        completed_videos = (args.output / place_id / "video.mp4", ROOT / "test" / "results" / place_id / "video.mp4")
                        if history.contains(place_id) or any(path.is_file() and path.stat().st_size > 0 for path in completed_videos):
                            print(f"이미 영상이 완성된 가게: {place_id} 건너뜀", flush=True)
                            continue
                        print(f"가게 처리: {place_id} {place['place_name']}", flush=True)
                        started = datetime.now()
                        folder = args.output / "작업중" / place_id / started.strftime("%Y%m%d_%H%M%S_%f")
                        stage = "place"
                        availability = place_availability(browser, place["place_url"])
                        with tempfile.TemporaryDirectory() as temp_dir:
                            stage = "reviews"
                            review_photos = []
                            reviews = reviews_for(browser, place["place_url"], availability,
                                                  Path(temp_dir) / "review_photos", review_photos)
                            if not reviews:
                                print("후기 없음: 후기 없이 진행", flush=True)
                            stage = "photos"
                            photos = photos_for(browser, place["place_url"], Path(temp_dir) / "photos", args.max_photos, availability)
                            if len(photos) + len(review_photos) < 2:
                                history.set_failure(place_id, "less than 2 photos")
                                print("사진 2장 미만: 건너뜀", flush=True)
                                continue
                            photo_folder = folder / "03_photos"
                            photo_folder.mkdir(parents=True, exist_ok=True)
                            photos = [Path(shutil.move(str(photo), str(photo_folder / photo.name))) for photo in photos + review_photos]
                        stage = "save"
                        write_json(folder / "01_place.json", place)
                        write_json(folder / "02_reviews.json", reviews)
                        write_json(folder / "04_analysis_photos.json", photo_manifest(photos))
                        stage = "analysis"
                        analysis = analyze(recorded_client(client, folder, "analysis", "04"), photos)
                        (folder / "04_analysis.txt").write_text(analysis, encoding="utf-8")
                        stage = "plan"
                        result = plan(recorded_client(client, folder, "plan", "05"), place, reviews, analysis, photos)
                        write_json(folder / "05_plan.json", result)
                        write_json(folder / "05_marketing.json", {"가게": place, "후기": reviews, "사진_분석": analysis, "기획": result})
                        prepared.append({"place": place, "place_id": place_id, "channel": channel,
                                         "folder": folder, "photos": photos, "result": result, "reviews": reviews, "analysis": analysis})
                        if schedule:
                            pending_allocations[channel].append(next_result_publication(schedule, allocated)[0])
                        print(f"대본 준비 완료: {folder}", flush=True)
                        break
                    except Exception as exc:
                        reason = failure_reason(exc, stage)
                        history.set_failure(place_id, reason)
                        print(f"가게 처리 실패: {place_id} ({reason}: {exc})", file=sys.stderr, flush=True)
                        continue
                if single and prepared:
                    break
            browser.close()
            browser = None
            if qwen_tts_enabled():
                print(f"대본 {len(prepared)}건 완료, Qwen 음성을 일괄 생성합니다", flush=True)
                voices = generate_qwen_batch(prepared, client)
            else:
                voices = {}
            for job in prepared:
                place, place_id, channel = job["place"], job["place_id"], job["channel"]
                folder, result = job["folder"], job["result"]
                stage = "voice"
                try:
                    voice = voices.get(str(folder.resolve())) if qwen_tts_enabled() else None
                    if qwen_tts_enabled() and (not voice or voice.get("error") or not voice.get("clips") or not voice.get("segments")):
                        raise ValueError(voice.get("error", "Qwen 음성 결과가 없습니다") if voice else "Qwen 음성 결과가 없습니다")
                    stage = "video"
                    raw_video = folder / "06_video_raw.mp4"
                    reference = None if qwen_tts_enabled() else narration_reference(result["15초_스크립트"])
                    generate_video_without_subtitles(client, args.comfy_url, selected_photos(result, job["photos"]), result["minimax_prompt"], raw_video, reference, args.workflow)
                    add_subtitles(raw_video, folder / "video.mp4", result["15초_스크립트"], subtitle_name="06_subtitles.srt", place_name=place["place_name"], voice_clips=voice["clips"] if voice else None, subtitle_segments=voice["segments"] if voice else None)
                    stage = "save"
                    publication = next_result_publication(schedule, allocations[channel]) if schedule else None
                    destination = result_folder(args.output, place_id, publication, datetime.now(KOREA_TIME))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        raise FileExistsError(f"결과 폴더가 이미 있습니다. 생성된 결과는 {folder}에 보관했습니다: {destination}")
                    staging_parent = folder.parent
                    folder.rename(destination)
                    for path in (staging_parent, staging_parent.parent):
                        try:
                            path.rmdir()
                        except OSError:
                            pass
                    folder = destination
                    history.add(place_id, channel, publication[0] if publication else None)
                    if publication:
                        allocations[channel].append(publication[0])
                    record_process(place, folder / "video.mp4", publication[0] if publication else None)
                    completed_paths.append(folder)
                    print(f"완료: {folder}", flush=True)
                    completed += 1
                except Exception as exc:
                    reason = failure_reason(exc, stage)
                    history.set_failure(place_id, reason)
                    print(f"가게 처리 실패: {place_id} ({reason}: {exc})", file=sys.stderr, flush=True)
            print(f"처리 종료: {completed}개 가게 완료", flush=True)
        finally:
            if browser is not None:
                browser.close()
            print("완료 경로:", flush=True)
            for path in completed_paths:
                print(f"- {path}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (CollectionError, requests.RequestException, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)
