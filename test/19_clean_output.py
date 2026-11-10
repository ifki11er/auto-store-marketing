"""설정 기간의 영상을 백업하고 해당 output 날짜 폴더를 정리한다."""
import filecmp
import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from marketing.main import load_env

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
BACKUP_DIR = OUTPUT_DIR / "backup"


def is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def main():
    load_env(ROOT / ".env")
    try:
        start = date.fromisoformat(os.environ.get("YOUTUBE_START_DATE", "").strip())
        end = date.fromisoformat(os.environ.get("YOUTUBE_END_DATE", "").strip())
    except ValueError as exc:
        raise ValueError(".env의 YOUTUBE_START_DATE와 YOUTUBE_END_DATE를 YYYY-MM-DD 형식으로 입력하세요") from exc
    if end < start:
        raise ValueError("종료 날짜는 시작 날짜보다 빠를 수 없습니다")
    if is_link(OUTPUT_DIR) or not OUTPUT_DIR.is_dir() or is_link(BACKUP_DIR):
        raise ValueError("output 폴더가 없거나 output 또는 backup이 링크입니다")

    files = []
    directories = []
    videos = {}

    def walk_error(error):
        raise error

    day = start
    while day <= end:
        day_folder = OUTPUT_DIR / day.isoformat()
        if is_link(day_folder):
            raise ValueError(f"날짜 폴더가 링크입니다: {day_folder}")
        if day_folder.is_dir():
            day_root = day_folder.resolve()
            for current, subdirs, names in os.walk(day_folder, followlinks=False, onerror=walk_error):
                current_path = Path(current)
                directories.append(current_path)
                for name in subdirs:
                    path = current_path / name
                    if is_link(path) or not path.resolve().is_relative_to(day_root):
                        raise ValueError(f"링크 또는 날짜 폴더 밖의 폴더입니다: {path}")
                for name in names:
                    path = current_path / name
                    if is_link(path) or not path.resolve(strict=True).is_relative_to(day_root):
                        raise ValueError(f"링크 또는 날짜 폴더 밖의 파일입니다: {path}")
                    files.append(path)
                    if name.casefold() == "video.mp4":
                        place_id = current_path.name
                        if not place_id.isdigit():
                            raise ValueError(f"영상의 가게 ID 폴더를 확인할 수 없습니다: {path}")
                        if place_id in videos:
                            raise ValueError(f"같은 ID의 영상이 여러 개입니다: {videos[place_id]}, {path}")
                        videos[place_id] = path
        if day == end:
            break
        day += timedelta(days=1)

    BACKUP_DIR.mkdir(exist_ok=True)
    for place_id, video in videos.items():
        target = BACKUP_DIR / f"{place_id}.mp4"
        if is_link(target):
            raise ValueError(f"백업 파일이 링크입니다: {target}")
        if target.exists():
            if not target.is_file() or not filecmp.cmp(video, target, shallow=False):
                raise FileExistsError(f"내용이 다른 백업 파일이 이미 있습니다: {target}")
            print(f"동일한 백업 확인: {target}", flush=True)
            continue
        with video.open("rb") as source:
            with target.open("xb") as destination:
                try:
                    shutil.copyfileobj(source, destination)
                except Exception:
                    destination.close()
                    target.unlink()
                    raise
        if not filecmp.cmp(video, target, shallow=False):
            target.unlink()
            raise OSError(f"백업 파일 검증 실패: {target}")
        print(f"백업 완료: {target}", flush=True)

    failures = []
    deleted = 0
    for path in files:
        try:
            path.unlink()
            deleted += 1
            print(f"삭제: {path}", flush=True)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
            print(f"삭제 실패: {path}: {exc}", flush=True)
    for path in reversed(directories):
        try:
            path.rmdir()
        except OSError as exc:
            failures.append(f"{path}: {exc}")
            print(f"폴더 삭제 실패: {path}: {exc}", flush=True)
    print(f"백업: {len(videos)}개, 파일 삭제 성공: {deleted}개, 실패: {len(failures)}개", flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
