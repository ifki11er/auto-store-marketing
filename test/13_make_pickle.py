"""선택한 YouTube 채널의 업로드 인증 파일을 만든다."""
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET = ROOT / "res" / "technam_client_secret.json"
OUTPUT_FILE = ROOT / "res" / "tmp.pickle"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    if OUTPUT_FILE.exists():
        print(f"이미 존재: {OUTPUT_FILE}. 파일 이름을 변경한 뒤 다시 실행하세요.", flush=True)
        return
    if not CLIENT_SECRET.is_file():
        raise FileNotFoundError(f"Google OAuth 클라이언트 JSON이 없습니다: {CLIENT_SECRET}")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("google_auth_oauthlib"):
            raise SystemExit("인증 라이브러리가 없습니다. 현재 Python 환경에서 python -m pip install google-auth-oauthlib 를 실행하세요.") from exc
        raise
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    print("브라우저에서 유튜브 채널을 선택하고 업로드 권한을 허용하세요.", flush=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    credentials = flow.run_local_server(port=0, prompt="select_account consent")
    if not credentials.refresh_token:
        raise RuntimeError("인증에 갱신 토큰이 없습니다. 다시 인증해 주세요.")
    with OUTPUT_FILE.open("xb") as file:
        pickle.dump(credentials, file)
    print(f"저장 완료: {OUTPUT_FILE}. 지역 이름으로 파일명을 변경하세요.", flush=True)


if __name__ == "__main__":
    main()
