# 가게 영상 마케팅

기본적으로 제외 업종을 제거한 `kakao_places_filtered.db`의 `places`에서 처리할 가게를 찾아 사진 분석 → 한국어 마케팅 기획 → MiniMax H3 영상 생성을 수행합니다. 후기가 있으면 수집해 참고하고 없으면 후기 없이 진행합니다. 필터링 DB에는 생성·업로드·사진 상태를 기록하며 원본 `kakao_places.db`는 변경하지 않습니다. 결과는 업로드 여부와 관계없이 `output/YYYY-MM-DD/HHMM/<place_id>/`에 저장합니다. 즉시 공개 영상은 실제 공개 날짜와 시간을 사용합니다. 영상 생성 중의 중간 결과는 `output/작업중/`에 저장합니다. 후기는 최대 첫 응답에 포함된 텍스트 후기만 사용합니다.

## 준비

작업 폴더: `C:\d\develop\auto_store_marketing`

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

프로젝트 루트 `.env` 파일의 `OPENAI_API_KEY`에 발급받은 키를 입력하세요. `.env`에는 `이름=값` 형식으로 설정을 추가할 수 있으며, 이미 설정된 환경 변수가 우선합니다.

`main.py`는 영상만 생성합니다. `.env`의 `YOUTUBE_START_DATE`와 `YOUTUBE_END_DATE`(YYYY-MM-DD, 양쪽 날짜 포함), `YOUTUBE_DAILY_UPLOAD_COUNT`, `YOUTUBE_PUBLISH_TIMES`(한국 시간, 쉼표 구분)를 설정하면 `output`의 날짜·시간 폴더를 확인해 지역별로 빈 시간에 영상을 배정합니다. 공개 시간의 개수는 하루 업로드 개수와 일치해야 합니다. 검수 후 `test/14_upload_reviewed.py`를 실행해 폴더 시간에 맞춰 업로드합니다. `test/08_upload_schedule.py`는 실제 업로드 없이 일정만 계산합니다. 업로드에는 `res/강원.pickle`처럼 지역별 Google OAuth Credentials 객체를 저장한 pickle 파일이 필요하며, 광주는 전라 채널에 포함되고 대전은 별도 채널입니다. 인증에는 YouTube 업로드 권한이 포함되어야 합니다.

OpenAI 계정에 `gpt-6-luna` API 접근 권한이 필요합니다. 실행 중인 ComfyUI의 API와 제공된 워크플로에서 지정한 모델, LoRA 및 커스텀 노드가 필요합니다. 특히 `DenoPromptText`, `DenoTextEncoderUnload`, `VHS_VideoCombine` 등 워크플로의 모든 활성 노드가 설치되어 있어야 합니다. 워크플로의 기본 해상도와 영상 설정을 사용하며 참조 사진만 교체합니다. 시작 전 원본 사진과 후기의 광고 활용 권한을 확인하세요.

## 실행

```powershell
python main.py
```

지정된 지역을 순서대로 돌아가며 각 지역에서 적합한 가게 한 곳의 영상을 생성합니다. 사진이 없는 가게나 후기 또는 사진 페이지 수집 시간이 초과된 가게는 같은 지역의 다음 가게로 넘어갑니다. 후기가 없으면 후기 없이 진행합니다. 시간 초과된 가게는 완료 이력에 기록하지 않아 다음 실행에서 다시 시도합니다. 지역별 공개 시간은 설정된 기간의 완성 영상 폴더를 기준으로 계산합니다. 유튜브의 기존 영상과 예약은 조회하지 않습니다. 그 밖의 수집 또는 API 오류가 발생하면 중단합니다. 업로드 도중 오류가 나면 중복 방지를 위해 이력이 `진행중`으로 남습니다. 유튜브에서 실제 업로드 여부를 확인한 뒤 이력을 수정해야 다시 실행할 수 있습니다. `test/07_one_place.py`는 업로드 없이 영상만 만들며 후기 또는 사진 수집 시간이 초과되면 오류를 보여줍니다.

```powershell
python main.py --place-id 12263892
python main.py --after-id 12263892 --max-photos 3 --comfy-url http://127.0.0.1:8188
```

`--after-id`는 문자열 기준으로 해당 ID 다음 가게부터 검색합니다. 생성·공개 시간과 상태는 필터링 DB의 `places` 테이블에 기록하므로 `output`을 삭제해도 다시 생성하지 않습니다. `generated_at`은 생성 완료 시각, `publish_at`은 예정 또는 실제 공개 시각, `channel`은 지역 채널, `upload_status`는 업로드 상태, `youtube_video_id`는 업로드된 영상 ID입니다. 기존 JSON 파일은 이전 이력의 백업이며 실행 중 갱신하지 않습니다. 필터링 DB를 새로 만들 때는 기존 이력을 이전해야 합니다. 생성이 실패한 가게는 완료로 기록하지 않습니다. `--max-photos`로 다운로드 최대 수를 지정할 수 있습니다. `--output`과 `--db`로 경로를 바꿀 수 있습니다. 원격 ComfyUI를 사용할 경우 사진과 프롬프트가 해당 서버로 전송됩니다.

영상 제작이 불가능한 가게는 DB의 `posible`에 간단한 실패 사유를 기록합니다. 사진이 없으면 `no photo`, 글자가 많은 사진만 있으면 `too much text`이며, 값이 있으면 DB 조회에서 제외합니다. 다시 확인하려면 `posible`을 NULL 또는 빈 문자열로 변경하세요. 페이지 시간 초과나 다운로드 오류, 사진 분석 응답 형식 오류는 실패 사유로 기록하지 않아 다음에 다시 시도합니다.

새로 생성한 가게 영상은 프로젝트 루트 `process.json`에 ID, 가게명, 지역 채널, 생성 시각, 공개 예정 시각과 영상 경로를 기록합니다. 6번·7번·12번 테스트 결과도 포함합니다. 업로드 시에는 이 파일을 변경하지 않으며, 검수 후 수동으로 삭제해도 DB 이력에는 영향이 없습니다.

카카오 원본 장소 페이지의 기본 탭을 확인해 사진 탭이 없으면 해당 가게를 건너뜁니다. 사진 탭이 있으면 후기가 있을 때만 `#review`에서 수집하고, `#photoview`에서 사진을 수집한 뒤 GPT를 호출합니다. 이 판단은 카카오 페이지 구조와 이미지 주소 형식에 의존하므로 페이지가 바뀌거나 접근이 제한되면 수정이 필요할 수 있습니다. MiniMax 프롬프트 요약은 `docs/MINIMAX_H3_SUMMARY.md`에 있으며 원문은 `docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`입니다. 15초 스크립트 시간은 검증하지만 영상의 실제 길이는 워크플로와 ComfyUI 노드의 출력에 따라 확인해야 합니다.

영상 기획의 공통 지시는 `marketing/prompts/common.md`에 있고, 업종별 지시는 같은 폴더의 코드별 파일에 있습니다. `marketing/prompts.py`에서 업종 코드와 지시 파일을 연결하며, 등록되지 않은 코드는 공통 지시만 사용합니다.

나레이션 영상은 `.env`의 `MINIMAX_REFERENCE_VOICE`에 지정한 `res` 폴더의 WAV를 MiniMax 음색 참조로 전달합니다. 나레이션·음악·효과음은 MiniMax가 생성하며, 별도 TTS나 FFmpeg 음성 합성은 사용하지 않습니다. 화면 속 발화 영상은 참조 WAV 없이 MiniMax가 발화합니다. 한 영상에는 나레이션과 화면 속 발화를 섞지 않습니다. `test/11_minimax_voice_reference.py`로 참조 음성을 개별 테스트할 수 있습니다.
