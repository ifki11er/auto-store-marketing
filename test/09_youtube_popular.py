"""유튜브에서 조회수가 높은 음식점 관련 짧은 영상을 검색한다."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests


# VS Code에서 실행하기 전에 필요에 따라 아래 값을 수정하세요.
SEARCH_QUERY = "맛집 추천 쇼츠"
SEARCH_RESULTS = 50  # 검색 후보 수: 1~50
MAX_DURATION_SECONDS = 180  # 짧은 영상 후보의 최대 길이
PUBLISHED_AFTER = ""  # 선택 사항: YYYY-MM-DD. 비우면 게시일 제한 없음
OUTPUT_FILE = Path(__file__).resolve().parent / "results" / "09_youtube_popular.json"
ROOT = Path(__file__).resolve().parent.parent


def api_key():
    value = os.environ.get("YOUTUBE_API_KEY")
    if value is None:
        env_path = ROOT / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                key, separator, candidate = line.strip().partition("=")
                if separator and key.strip() == "YOUTUBE_API_KEY":
                    value = candidate.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                        value = value[1:-1]
                    break
    if not value or not value.strip():
        raise ValueError(".env의 YOUTUBE_API_KEY에 YouTube Data API 키를 입력하세요.")
    return value.strip()


def duration_seconds(value):
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not match:
        raise ValueError(f"알 수 없는 영상 길이 형식: {value}")
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def youtube_get(resource, params, key):
    response = requests.get(f"https://www.googleapis.com/youtube/v3/{resource}", params=params,
                            headers={"X-Goog-Api-Key": key}, timeout=30)
    if not response.ok:
        try:
            error = response.json().get("error", {})
            reason = error.get("errors", [{}])[0].get("reason", "")
        except (ValueError, IndexError, AttributeError):
            reason = ""
        raise RuntimeError(f"YouTube API 요청 실패: HTTP {response.status_code} {reason}")
    return response.json()


def main():
    if not 1 <= SEARCH_RESULTS <= 50 or MAX_DURATION_SECONDS < 1:
        raise ValueError("검색 후보 수는 1~50, 최대 영상 길이는 1초 이상이어야 합니다.")
    key = api_key()
    params = {"part": "snippet", "q": SEARCH_QUERY, "type": "video", "order": "viewCount",
              "videoDuration": "short", "maxResults": SEARCH_RESULTS, "regionCode": "KR"}
    if PUBLISHED_AFTER:
        params["publishedAfter"] = datetime.strptime(PUBLISHED_AFTER, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    search = youtube_get("search", params, key)
    video_ids = [item["id"]["videoId"] for item in search.get("items", []) if item.get("id", {}).get("videoId")]
    details = youtube_get("videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(video_ids)}, key) if video_ids else {"items": []}
    videos = []
    for item in details.get("items", []):
        duration = duration_seconds(item["contentDetails"]["duration"])
        if duration > MAX_DURATION_SECONDS:
            continue
        snippet, stats = item["snippet"], item.get("statistics", {})
        videos.append({"영상_ID": item["id"], "제목": snippet["title"], "채널": snippet["channelTitle"],
                       "게시일": snippet["publishedAt"], "조회수": int(stats["viewCount"]) if "viewCount" in stats else None,
                       "좋아요": int(stats["likeCount"]) if "likeCount" in stats else None,
                       "길이_초": duration, "설명": snippet.get("description", ""),
                       "URL": f"https://www.youtube.com/watch?v={item['id']}"})
    videos.sort(key=lambda video: video["조회수"] if video["조회수"] is not None else -1, reverse=True)
    result = {"검색어": SEARCH_QUERY, "검색_시각": datetime.now(timezone.utc).isoformat(),
              "최대_영상_길이_초": MAX_DURATION_SECONDS, "검색_후보_수": len(video_ids), "영상": videos}
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for video in videos:
        print(f"{video['조회수']}회 | {video['길이_초']}초 | {video['제목']} | {video['URL']}")
    print(f"검색 후보 {len(video_ids)}개 중 길이 조건에 맞는 영상 {len(videos)}개 저장: {OUTPUT_FILE}")
    print("짧은 영상 검색 결과이며 실제 쇼츠 여부는 별도로 확인해야 합니다.")


if __name__ == "__main__":
    main()
