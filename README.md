# 가게 영상 마케팅

기본적으로 제외 업종을 제거한 `kakao_places_filtered.db`의 `places`를 읽어 사진이 있는 가게들을 순서대로 찾고, 사진 분석 → 한국어 마케팅 기획 → MiniMax H3 영상 생성을 순서대로 수행합니다. 후기가 있으면 수집해 참고하고 없으면 후기 없이 진행합니다. DB는 읽기 전용으로 열고 변경하지 않습니다. 원본은 `kakao_places.db`에 그대로 남아 있습니다. 결과는 예약 공개 날짜와 시간에 따라 `output/<공개 날짜>/<공개 시간>/<place_id>/`에 저장합니다. 즉시 공개는 시간 대신 `즉시공개`, 업로드하지 않는 영상은 `업로드없음` 폴더에 저장합니다. 영상 생성 중의 중간 결과는 `output/작업중/`에 저장합니다. 후기는 최대 첫 응답에 포함된 텍스트 후기만 사용합니다.

## 준비

작업 폴더: `C:\d\develop\auto_store_marketing`

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

프로젝트 루트 `.env` 파일의 `OPENAI_API_KEY`에 발급받은 키를 입력하세요. `.env`에는 `이름=값` 형식으로 설정을 추가할 수 있으며, 이미 설정된 환경 변수가 우선합니다.

`.env`의 `YOUTUBE_UPLOAD_ENABLED=true`이면 영상 생성 후 유튜브에 업로드하고, `false`이면 업로드 없이 다음 가게로 넘어갑니다. 업로드를 끄면 예약 날짜와 인증 파일은 필요하지 않습니다. 예약 설정은 `.env`의 `YOUTUBE_START_DATE`와 `YOUTUBE_END_DATE`(YYYY-MM-DD, 양쪽 날짜 포함), `YOUTUBE_DAILY_UPLOAD_COUNT`, `YOUTUBE_PUBLISH_TIMES`(한국 시간, 쉼표 구분)에 입력합니다. 공개 시간의 개수는 하루 업로드 개수와 일치해야 합니다. `test/08_upload_schedule.py`는 실제 업로드 없이 일정만 계산합니다. 업로드를 켜면 `res/강원.pickle`처럼 10개 지역별 Google OAuth Credentials 객체를 저장한 pickle 파일이 필요하며, 인증에는 YouTube 업로드 권한이 포함되어야 합니다.

OpenAI 계정에 `gpt-6-luna` API 접근 권한이 필요합니다. 실행 중인 ComfyUI의 API와 제공된 워크플로에서 지정한 모델, LoRA 및 커스텀 노드가 필요합니다. 특히 `DenoPromptText`, `DenoTextEncoderUnload`, `VHS_VideoCombine` 등 워크플로의 모든 활성 노드가 설치되어 있어야 합니다. 워크플로의 기본 해상도와 영상 설정을 사용하며 참조 사진만 교체합니다. 시작 전 원본 사진과 후기의 광고 활용 권한을 확인하세요.

## 실행

```powershell
python main.py
```

지정된 지역을 순서대로 돌아가며 각 지역에서 적합한 가게 한 곳의 영상을 생성합니다. 업로드가 켜져 있으면 생성 직후 해당 지역 채널에 업로드합니다. 사진이 없는 가게나 후기 또는 사진 페이지 수집 시간이 초과된 가게는 같은 지역의 다음 가게로 넘어갑니다. 후기가 없으면 후기 없이 진행합니다. 시간 초과된 가게는 완료 이력에 기록하지 않아 다음 실행에서 다시 시도합니다. 지역별 공개 시간은 이 파일의 성공한 업로드 이력으로 계산합니다. 유튜브의 기존 영상과 예약은 조회하지 않습니다. 그 밖의 수집 또는 API 오류가 발생하면 중단합니다. 업로드 도중 오류가 나면 중복 방지를 위해 이력이 `진행중`으로 남습니다. 유튜브에서 실제 업로드 여부를 확인한 뒤 이력을 수정해야 다시 실행할 수 있습니다. `test/07_one_place.py`는 업로드 없이 영상만 만들며 후기 또는 사진 수집 시간이 초과되면 오류를 보여줍니다.

```powershell
python main.py --place-id 12263892
python main.py --after-id 12263892 --max-photos 3 --comfy-url http://127.0.0.1:8188
```

`--after-id`는 문자열 기준으로 해당 ID 다음 가게부터 검색합니다. 완성된 가게 ID와 생성·공개 시간은 프로젝트 루트의 `completed_places.json`에 기록하므로 `output`을 삭제해도 다시 생성하지 않습니다. `업로드_시간`은 즉시 공개 시각 또는 예약 공개 시각이며, 실제 완료 여부는 `업로드_상태`로 확인합니다. 기존 문자열 형식 이력과 `completed_places.db`의 시간은 당시 이력에 기록된 시각을 생성 시간으로 가져옵니다. 실행 시 `output/<날짜>/<시간>/<place_id>/video.mp4`, 기존 `output/<place_id>/video.mp4` 또는 `test/results/<place_id>/video.mp4`에 크기가 0보다 큰 기존 영상도 파일 수정 시각을 생성 시간으로 등록합니다. 기존 결과를 보존하려면 `output`을 삭제하기 전에 프로그램을 한 번 실행해야 합니다. 생성이 실패한 가게는 이력에 기록하지 않습니다. 기본적으로 사진 수를 제한하지 않으며 `--max-photos`로 다운로드 최대 수를 지정할 수 있습니다. `--output`과 `--db`로 경로를 바꿀 수 있습니다. 원격 ComfyUI를 사용할 경우 사진과 프롬프트가 해당 서버로 전송됩니다.

사진이 없는 것으로 확인된 가게는 `no_photo_places.json`에 ID와 가게명, URL, 확인 시간을 기록하고 다음 실행부터 웹 접속 전에 건너뜁니다. 사진이 추가된 가게를 다시 확인하려면 해당 ID를 JSON에서 삭제하세요. 페이지 시간 초과나 다운로드 오류는 사진 없음으로 기록하지 않습니다.

카카오 원본 장소 페이지의 기본 탭을 확인해 사진 탭이 없으면 해당 가게를 건너뜁니다. 사진 탭이 있으면 후기가 있을 때만 `#review`에서 수집하고, `#photoview`에서 사진을 수집한 뒤 GPT를 호출합니다. 이 판단은 카카오 페이지 구조와 이미지 주소 형식에 의존하므로 페이지가 바뀌거나 접근이 제한되면 수정이 필요할 수 있습니다. MiniMax 프롬프트 요약은 `docs/MINIMAX_H3_SUMMARY.md`에 있으며 원문은 `docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`입니다. 15초 스크립트 시간은 검증하지만 영상의 실제 길이는 워크플로와 ComfyUI 노드의 출력에 따라 확인해야 합니다.

영상 기획의 공통 지시는 `marketing/prompts/common.md`에 있고, 업종별 지시는 같은 폴더의 코드별 파일에 있습니다. `marketing/prompts.py`에서 업종 코드와 지시 파일을 연결하며, 등록되지 않은 코드는 공통 지시만 사용합니다.

나레이션 영상은 `.env`의 `MINIMAX_REFERENCE_VOICE`에 지정한 `res` 폴더의 WAV를 MiniMax 음색 참조로 전달합니다. 나레이션·음악·효과음은 MiniMax가 생성하며, 별도 TTS나 FFmpeg 음성 합성은 사용하지 않습니다. 화면 속 발화 영상은 참조 WAV 없이 MiniMax가 발화합니다. 한 영상에는 나레이션과 화면 속 발화를 섞지 않습니다. `test/11_minimax_voice_reference.py`로 참조 음성을 개별 테스트할 수 있습니다.
