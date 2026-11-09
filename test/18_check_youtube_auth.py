"""지역별 YouTube 인증 파일의 갱신 토큰을 확인한다."""
import pickle
import sys
from pathlib import Path

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from marketing.channel_regions import CHANNEL_ORDER


AUTH_DIR = Path(__file__).resolve().parent.parent / "res"


def main():
    results = {"유효": [], "재인증 필요": [], "확인 불가": []}
    for channel in CHANNEL_ORDER:
        path = AUTH_DIR / f"{channel}.pickle"
        if not path.is_file():
            results["확인 불가"].append(f"{channel}: 인증 파일 없음")
            continue
        try:
            with path.open("rb") as file:
                credentials = pickle.load(file)
            if not getattr(credentials, "refresh_token", None):
                results["재인증 필요"].append(f"{channel}: 갱신 토큰 없음")
                continue
            # 액세스 토큰이 유효해도 갱신 토큰을 실제로 확인한다.
            credentials.refresh(Request())
            results["유효"].append(channel)
        except RefreshError as exc:
            if "invalid_grant" in str(exc):
                results["재인증 필요"].append(f"{channel}: 갱신 토큰 만료 또는 무효")
            else:
                results["확인 불가"].append(f"{channel}: 인증 서버가 갱신을 거부함 ({type(exc).__name__})")
        except TransportError:
            results["확인 불가"].append(f"{channel}: 네트워크 또는 인증 서버 연결 오류")
        except Exception as exc:
            results["확인 불가"].append(f"{channel}: 인증 파일 또는 응답 확인 실패 ({type(exc).__name__})")

    for status, channels in results.items():
        print(f"{status}: {len(channels)}개")
        for channel in channels:
            print(f"- {channel}")


if __name__ == "__main__":
    main()
