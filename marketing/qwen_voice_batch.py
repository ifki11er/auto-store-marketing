"""Qwen 모델을 한 번 로드하고 표준 입력으로 대본을 받아 WAV를 생성한다."""
import contextlib
import json
import sys
from pathlib import Path


def main():
    with contextlib.redirect_stdout(sys.stderr):
        import soundfile as sf
        import torch
        from qwen_tts import Qwen3TTSModel

        reference = Path(sys.argv[1])
        if not reference.is_file():
            raise FileNotFoundError(f"Qwen 참조 음성 파일이 없습니다: {reference}")
        model = Qwen3TTSModel.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-1.7B-Base", device_map="auto",
            dtype=torch.bfloat16, attn_implementation="sdpa",
        )
        voice = model.create_voice_clone_prompt(
            ref_audio=str(reference), ref_text="여기 작고 얇은 지갑이 있습니다. 카드가 딱 한장 들어있네요.",
            x_vector_only_mode=False,
        )
    for line in sys.stdin:
        try:
            job = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                wavs, sr = model.generate_voice_clone(text=job["text"], language="Korean", voice_clone_prompt=voice)
                sf.write(job["path"], wavs[0], sr)
            result = {"path": job["path"], "duration": len(wavs[0]) / sr}
        except Exception as exc:
            result = {"error": str(exc)}
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
