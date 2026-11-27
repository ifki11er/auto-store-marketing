"""가게 영상 마케팅 프로그램 진입점."""
import runpy
import os
from pathlib import Path


if __name__ == "__main__":
    env = Path(__file__).resolve().parent / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() == "MODE":
                os.environ.setdefault("MODE", value.strip().strip("\"'"))
                break
    mode = os.environ.get("MODE", "marketting").strip().lower()
    if mode not in ("marketting", "fasion"):
        raise ValueError("MODE는 marketting 또는 fasion이어야 합니다")
    runpy.run_module("fasion.main" if mode == "fasion" else "marketing.main", run_name="__main__")
