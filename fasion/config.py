"""패션 영상 설정과 경로."""
import json
import os
from pathlib import Path

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
