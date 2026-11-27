"""Qwen 음성 모델을 한 번 로드해 기획된 모든 대사를 생성한다."""
import json
import sys
from pathlib import Path

import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


def main():
    jobs_path = Path(sys.argv[1])
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    ref_audio = Path(sys.argv[2])
    ref_text = "여기 작고 얇은 지갑이 있습니다. 카드가 딱 한장 들어있네요."
    if not ref_audio.is_file():
        raise FileNotFoundError(f"Qwen 참조 음성 파일이 없습니다: {ref_audio}")
    model = Qwen3TTSModel.from_pretrained(
        "Qwen/Qwen3-TTS-12Hz-1.7B-Base", device_map="auto",
        dtype=torch.bfloat16, attn_implementation="sdpa",
    )
    voice = model.create_voice_clone_prompt(ref_audio=str(ref_audio), ref_text=ref_text, x_vector_only_mode=False)
    results = []
    for job in jobs:
        folder = Path(job["folder"])
        try:
            clips = []
            for index, shot in enumerate(job["shots"]):
                text = shot["내레이션"].strip()
                if not text:
                    continue
                wavs, sr = model.generate_voice_clone(text=text, language="Korean", voice_clone_prompt=voice)
                path = folder / f"06_narration_{index + 1:02d}.wav"
                sf.write(str(path), wavs[0], sr)
                duration = len(wavs[0]) / sr
                available = float(shot["종료초"]) - float(shot["시작초"])
                if duration > available + 0.01:
                    raise ValueError(f"{index + 1}번 대사 {duration:.2f}초가 장면 {available:.2f}초보다 깁니다")
                clips.append({"path": str(path.resolve()), "start": float(shot["시작초"]), "duration": duration})
            if not clips:
                raise ValueError("생성할 나레이션이 없습니다")
            results.append({"folder": str(folder), "clips": clips})
        except Exception as exc:
            results.append({"folder": str(folder), "error": str(exc)})
    jobs_path.with_name("qwen_voice_results.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
