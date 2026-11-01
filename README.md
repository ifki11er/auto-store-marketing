# 가게 영상 마케팅

SQLite의 `places`를 읽어 후기와 사진이 있는 가게들을 순서대로 찾고, 사진 분석 → 한국어 마케팅 기획 → MiniMax H3 영상 생성을 순서대로 수행합니다. 원본 DB는 읽기 전용으로 열고 변경하지 않습니다. 결과는 `output/<place_id>/05_marketing.json`, `03_photos/`, `video.mp4`에 저장합니다. 후기는 최대 첫 응답에 포함된 텍스트 후기만 사용합니다.

## 준비

작업 폴더: `C:\d\develop\auto_store_marketing`

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

프로젝트 루트 `.env` 파일의 `OPENAI_API_KEY`에 발급받은 키를 입력하세요. `.env`에는 `이름=값` 형식으로 설정을 추가할 수 있으며, 이미 설정된 환경 변수가 우선합니다.

OpenAI 계정에 `gpt-6-luna`, `gpt-6.1-terra` API 접근 권한이 필요합니다. 실행 중인 ComfyUI의 API와 제공된 워크플로에서 지정한 모델, LoRA 및 커스텀 노드가 필요합니다. 특히 `DenoPromptText`, `DenoTextEncoderUnload`, `VHS_VideoCombine` 등 워크플로의 모든 활성 노드가 설치되어 있어야 합니다. 워크플로의 기본 해상도와 영상 설정을 사용하며 참조 사진만 교체합니다. 시작 전 원본 사진과 후기의 광고 활용 권한을 확인하세요.

## 실행

```powershell
python main.py
```

적합한 가게의 영상과 기획안을 생성한 뒤 다음 가게로 넘어가며 DB의 마지막 가게까지 계속 처리합니다. 후기나 사진이 없으면 다음 가게로 넘어갑니다. 수집 또는 API 오류가 발생하면 오류를 출력하고 중단합니다. 카카오 페이지의 구조가 변경되어 사진 존재 여부를 판단할 수 없는 경우에도 중단합니다.

```powershell
python main.py --place-id 12263892
python main.py --after-id 12263892 --max-photos 3 --comfy-url http://127.0.0.1:8188
```

`--after-id`는 문자열 기준으로 해당 ID 다음 가게부터 검색합니다. `output/<place_id>/video.mp4` 또는 `test/results/<place_id>/video.mp4`에 크기가 0보다 큰 영상이 있으면 이미 생성된 가게로 보고 건너뜁니다. 생성이 실패해 영상이 없으면 다음 실행에서 다시 처리합니다. 기본적으로 사진 수를 제한하지 않으며 `--max-photos`로 다운로드 최대 수를 지정할 수 있습니다. `--output`과 `--db`로 경로를 바꿀 수 있습니다. 원격 ComfyUI를 사용할 경우 사진과 프롬프트가 해당 서버로 전송됩니다.

카카오 사진 수집은 페이지의 사진 영역과 카카오 이미지 주소 형식에 의존하며, 후기 수집은 카카오 장소의 `#review` 페이지 구조에 의존합니다. 페이지가 바뀌거나 접근이 제한되면 수집 코드 수정이 필요할 수 있습니다. MiniMax 프롬프트 요약은 `docs/MINIMAX_H3_SUMMARY.md`에 있으며 원문은 `docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`입니다. 15초 스크립트 시간은 검증하지만 영상의 실제 길이는 워크플로와 ComfyUI 노드의 출력에 따라 확인해야 합니다.
