"""준비된 패션 영상을 지정한 Meta 계정에 예약한다."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fasion.main import load_env
from fasion.locale_settings import selected_locales
from fasion import instagram_upload as module


def main():
    load_env()
    result_root = ROOT / "fasion" / "test" / "results" / "instagram"
    accounts = []
    for country, _ in selected_locales():
        result_dir = result_root / country
        if not result_dir.is_dir() or not any(result_dir.glob(f"*/{country}/*.mp4")):
            continue
        asset_key = f"FASION_META_ASSET_ID_{country.upper()}"
        business_key = f"FASION_META_BUSINESS_ID_{country.upper()}"
        asset_id = os.environ.get(asset_key, "").strip()
        business_id = os.environ.get(business_key, "").strip()
        if not asset_id or not business_id:
            raise ValueError(f".env에 {asset_key}와 {business_key}를 설정하세요")
        accounts.append((country, result_dir, asset_id, business_id))
    if not accounts:
        print("선택한 언어의 인스타그램 업로드 파일이 없습니다.", flush=True)
        return
    for country, result_dir, asset_id, business_id in accounts:
        module.RESULT_DIR = result_dir
        module.PROFILE_DIR = ROOT / "fasion" / "test" / f".instagram_browser_profile_{country}"
        module.LOG_FILE = result_dir / "업로드_결과.json"
        module.BUSINESS_ID = business_id
        module.ASSETS = {country: asset_id}
        module.main()


if __name__ == "__main__":
    main()
