"""패션 영상을 인스타그램 업로드용으로 정리한다."""
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fasion.main import load_env
from fasion.upload_files import scheduled_videos

RESULT = ROOT / "fasion" / "test" / "results" / "instagram"


def main():
    load_env()
    run = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    captions = {}
    for job, video, data, slot in scheduled_videos():
        brand = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(data.get("brand", ""))).strip(" .")
        if not brand:
            raise ValueError(f"브랜드 정보가 없습니다: {job}")
        country = data["country"]
        target = RESULT / country / run / country
        target.mkdir(parents=True, exist_ok=True)
        name = f"{slot:%Y-%m-%d_%H%M}_{brand}_{job.name}.mp4"
        shutil.copy2(video, target / name)
        hashtags = []
        for tag in data["tags"]:
            clean = "".join(c for c in tag.strip().lstrip("#") if c.isalnum() or c == "_")
            if clean and "#" + clean not in hashtags:
                hashtags.append("#" + clean)
        if not hashtags:
            raise ValueError(f"인스타그램 해시태그가 없습니다: {job}")
        captions.setdefault(target, []).append(f"[영상] {name}\n[제목] {data['youtube_title'].strip()}\n{data['description'].strip()}\n\n{' '.join(hashtags)}\n")
        print(f"인스타그램 파일 준비: {target / name}", flush=True)
    for folder, sections in captions.items():
        (folder / "전체_설명.txt").write_text("\n".join(sections), encoding="utf-8-sig")
    print(f"결과 폴더: {RESULT} (작업 {run})", flush=True)


if __name__ == "__main__":
    main()
