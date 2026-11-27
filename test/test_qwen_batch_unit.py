import json
import math
import runpy
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from marketing import main


class QwenBatchTests(unittest.TestCase):
    def test_voice_mode_selects_matching_prompt(self):
        with patch.dict("os.environ", {"use_tts": "qwen3"}):
            self.assertTrue(main.qwen_tts_enabled())
            self.assertIn("Qwen이 별도로 생성", main.build_planning_prompt(None, video_format="자동"))
        with patch.dict("os.environ", {"use_tts": "minimax"}):
            self.assertFalse(main.qwen_tts_enabled())
            self.assertIn("나레이션과 화면발화는 MiniMax가 생성한다", main.build_planning_prompt(None, video_format="자동"))
        with patch.dict("os.environ", {"use_tts": "invalid"}):
            with self.assertRaises(ValueError):
                main.qwen_tts_enabled()

    def test_model_is_loaded_once_for_all_jobs(self):
        loads = []
        class Model:
            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                loads.append(args[0])
                return cls()

            def create_voice_clone_prompt(self, **kwargs):
                return object()

            def generate_voice_clone(self, **kwargs):
                return [[0] * 100], 100

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = root / "ref.wav"
            ref.write_bytes(b"ref")
            folders = [root / "a", root / "b"]
            for folder in folders:
                folder.mkdir()
            shots = [{"내레이션": "안녕하세요", "시작초": 0, "종료초": 2}]
            jobs = root / "jobs.json"
            jobs.write_text(json.dumps([{"folder": str(f), "shots": shots} for f in folders], ensure_ascii=False), encoding="utf-8")
            modules = {"torch": types.SimpleNamespace(bfloat16="bf16"),
                       "soundfile": types.SimpleNamespace(write=lambda path, data, sr: Path(path).write_bytes(b"wav")),
                       "qwen_tts": types.SimpleNamespace(Qwen3TTSModel=Model)}
            with patch.dict(sys.modules, modules), patch.object(sys, "argv", ["qwen_voice_batch.py", str(jobs), str(ref)]):
                runpy.run_path(str(main.ROOT / "marketing" / "qwen_voice_batch.py"), run_name="__main__")
            results = json.loads((root / "qwen_voice_results.json").read_text(encoding="utf-8"))
            self.assertEqual(len(loads), 1)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.get("clips") for item in results))

    def test_video_prompt_rejects_dialogue_and_reference_audio(self):
        for prompt in ("<d>[Korean] 안녕</d>", "Use <Audio 1>"):
            with self.assertRaises(ValueError):
                main.validate_silent_video_prompt({"minimax_prompt": prompt})
        main.validate_silent_video_prompt({"minimax_prompt": "Instrumental music, no speech."})

    def test_voice_mixes_with_video_audio(self):
        ffmpeg = main.ROOT / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if not ffmpeg.is_file():
            self.skipTest("FFmpeg가 없습니다")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, voice, output = root / "raw.mp4", root / "voice.wav", root / "video.mp4"
            subprocess.run([str(ffmpeg), "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=64x64:r=10:d=1",
                            "-f", "lavfi", "-i", "sine=frequency=220:duration=1", "-c:v", "libx264", "-c:a", "aac", str(raw)], check=True)
            with wave.open(str(voice), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"".join(struct.pack("<h", round(10000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(8000)))
            shots = [{"시작초": 0.2, "종료초": 1, "자막": "다른 내용", "내레이션": "실제 나레이션"}]
            with patch.dict("os.environ", {"WATERMARK_ENABLED": "false"}):
                main.add_subtitles(raw, output, shots, voice_clips=[{"path": str(voice), "start": 0.2, "duration": 0.5}])
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            subtitle = (root / "subtitles.srt").read_text(encoding="utf-8-sig")
            self.assertIn("00:00:00,200 --> 00:00:00,700", subtitle)
            self.assertIn("실제 나레이션", subtitle)
            self.assertNotIn("다른 내용", subtitle)


if __name__ == "__main__":
    unittest.main()
