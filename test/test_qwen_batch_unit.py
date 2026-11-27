import json
import io
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
    def test_plan_without_subtitle_field_and_legacy_field_is_removed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"use_tts": "qwen3", "VIDEO_FORMAT": "자동"}):
            photo = Path(directory) / "photo.jpg"
            photo.write_bytes(b"photo")
            analysis = json.dumps({"가게_전체_특징": [], "사진별_정보": [{"파일명": photo.name, "참조_사용_가능": True}]}, ensure_ascii=False)
            result = {key: "내용" for key in ("영상_형태", "영상_컨셉", "스토리_요약", "기획_차별점", "핵심_메시지", "시청자_반응", "결말_의미")}
            result.update({"15초_스크립트": [{"시작초": 0, "종료초": 15, "내레이션": "안녕하세요", "발화_유형": "나레이션", "화자_ID": "S1"}],
                           "음성_연출": [{"화자_ID": "S1", "목소리_설명": "목소리", "연출_이유": "이유"}],
                           "minimax_prompt": "<Picture 1> No speech.",
                           "선택_사진": [{"파일명": photo.name, "선택_이유": "이유"}]})
            for legacy in (False, True):
                if legacy:
                    result["15초_스크립트"][0]["자막"] = "예전 자막"
                response = types.SimpleNamespace(output_text=json.dumps(result, ensure_ascii=False))
                client = types.SimpleNamespace(responses=types.SimpleNamespace(create=lambda **kwargs: response))
                planned = main.plan(client, {}, [], analysis, [photo])
                self.assertNotIn("자막", planned["15초_스크립트"][0])
                self.assertEqual(planned["15초_스크립트"][0]["내레이션"], "안녕하세요")

    def test_caption_instruction_check_uses_narration(self):
        with self.assertRaises(ValueError):
            main.validate_video_caption_instructions({"15초_스크립트": [{"내레이션": "동해물과 백두산이"}],
                                                       "minimax_prompt": 'Display "동해물과 백두산이".'})

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
            jobs = [{"text": "안녕하세요", "path": str(f / "out.wav")} for f in folders]
            inputs = io.StringIO("".join(json.dumps(job, ensure_ascii=False) + "\n" for job in jobs))
            outputs = io.StringIO()
            modules = {"torch": types.SimpleNamespace(bfloat16="bf16"),
                       "soundfile": types.SimpleNamespace(write=lambda path, data, sr: Path(path).write_bytes(b"wav")),
                       "qwen_tts": types.SimpleNamespace(Qwen3TTSModel=Model)}
            with patch.dict(sys.modules, modules), patch.object(sys, "argv", ["qwen_voice_batch.py", str(ref)]), \
                 patch.object(sys, "stdin", inputs), patch.object(sys, "stdout", outputs):
                runpy.run_path(str(main.ROOT / "marketing" / "qwen_voice_batch.py"), run_name="__main__")
            results = [json.loads(line) for line in outputs.getvalue().splitlines()]
            self.assertEqual(len(loads), 1)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.get("duration") == 1 for item in results))

    def test_video_prompt_rejects_dialogue_and_reference_audio(self):
        for prompt in ("<d>[Korean] 안녕</d>", "Use <Audio 1>"):
            with self.assertRaises(ValueError):
                main.validate_silent_video_prompt({"minimax_prompt": prompt})
        main.validate_silent_video_prompt({"minimax_prompt": "Instrumental music, no speech."})

    def test_overlong_wav_replans_without_reloading_qwen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class Worker:
                def __init__(self):
                    self.pending = []
                    self.stdin = self.stdout = self
                    self.calls = 0

                def write(self, line):
                    job = json.loads(line)
                    Path(job["path"]).write_bytes(b"audio")
                    self.calls += 1
                    self.pending.append(json.dumps({"duration": 16.0 if self.calls == 1 else 10.0}))

                def flush(self):
                    pass

                def readline(self):
                    return self.pending.pop(0)

                def close(self):
                    pass

                def wait(self):
                    return 0

            def whisper(command, check, env):
                jobs = json.loads(Path(command[2]).read_text(encoding="utf-8"))
                Path(command[2]).with_name("whisper_results.json").write_text(
                    json.dumps([{"folder": jobs[0]["folder"], "segments": [{"text": "짧은 대본", "start": 0, "end": 1}]}]), encoding="utf-8")

            first = {"15초_스크립트": [{"내레이션": "긴 대본"}]}
            shorter = {"15초_스크립트": [{"내레이션": "짧은 대본"}]}
            job = {"folder": root, "result": first, "place": {}, "reviews": [], "analysis": "{}", "photos": []}
            with patch.object(main.subprocess, "Popen", return_value=Worker()) as popen, \
                 patch.object(main.subprocess, "run", side_effect=whisper), \
                 patch.object(main, "shorten_voice_pauses", side_effect=lambda path, duration: (path, duration)), \
                 patch.object(main, "speed_up_voice", side_effect=lambda path: (path, 16.0 if "attempt_1" in path.name else 10.0)), \
                 patch.object(main, "trim_voice_boundary_silence", side_effect=lambda path, duration: (path, duration)), \
                 patch.object(main, "recorded_client", return_value=object()), \
                 patch.object(main, "plan", return_value=shorter) as replan:
                result = main.generate_qwen_batch([job], object())
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(replan.call_count, 1)
            self.assertEqual(job["result"], shorter)
            self.assertEqual(result[str(root.resolve())]["clips"][0]["duration"], 10.0)

    def test_trim_only_boundary_silence_when_it_fits_15_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "voice.wav"
            rate = 8000
            samples = ([0] * int(rate * 0.7) +
                       [round(12000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(rate * 14.8))] +
                       [0] * int(rate * 0.3))
            with wave.open(str(source), "wb") as wav:
                wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
            trimmed, duration = main.trim_voice_boundary_silence(source, len(samples) / rate)
            self.assertNotEqual(trimmed, source)
            self.assertLessEqual(duration, 15)
            with wave.open(str(trimmed), "rb") as wav:
                self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), duration)
            self.assertTrue(source.is_file())

    def test_shorten_internal_pause_keeps_speech_and_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "voice.wav"
            rate = 8000
            tone = [round(12000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate)]
            samples = tone + [0] * int(rate * 0.8) + tone
            with wave.open(str(source), "wb") as wav:
                wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
            shortened, duration = main.shorten_voice_pauses(source, len(samples) / rate)
            self.assertNotEqual(shortened, source)
            self.assertAlmostEqual(duration, 2.3, delta=0.03)
            with wave.open(str(shortened), "rb") as wav:
                output = wav.readframes(wav.getnframes())
            self.assertEqual(output[:rate * 2], struct.pack(f"<{rate}h", *tone))
            self.assertEqual(output[-rate * 2:], struct.pack(f"<{rate}h", *tone))
            self.assertTrue(source.is_file())

    def test_voice_speed_increases_and_duration_is_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "voice.wav"
            rate = 8000
            samples = [round(12000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate * 2)]
            with wave.open(str(source), "wb") as wav:
                wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
            output, duration = main.speed_up_voice(source)
            self.assertAlmostEqual(duration, 2 / 1.1, delta=0.05)
            with wave.open(str(output), "rb") as wav:
                self.assertAlmostEqual(duration, wav.getnframes() / wav.getframerate())
            self.assertTrue(source.is_file())

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
            shots = [{"시작초": 0.2, "종료초": 1, "내레이션": "실제 나레이션"}]
            with patch.dict("os.environ", {"WATERMARK_ENABLED": "false"}):
                main.add_subtitles(raw, output, shots, voice_clips=[{"path": str(voice), "start": 0.2, "duration": 0.5}],
                                   subtitle_segments=[{"start": 0.2, "end": 0.4, "text": "실제"},
                                                      {"start": 0.4, "end": 0.7, "text": "나레이션"}])
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            subtitle = (root / "subtitles.srt").read_text(encoding="utf-8-sig")
            self.assertIn("00:00:00,200 --> 00:00:00,400", subtitle)
            self.assertIn("실제\n", subtitle)
            self.assertIn("나레이션\n", subtitle)

    def test_transcript_timing_preserves_original_text_with_mismatch(self):
        modules = {"torch": types.SimpleNamespace(), "transformers": types.SimpleNamespace(pipeline=None)}
        with patch.dict(sys.modules, modules):
            module = runpy.run_path(str(main.ROOT / "marketing" / "wav_to_srt_batch.py"))
        align = module["align_transcript"]
        entries = align("저장해요, 좋아요!", [{"text": "저장해요", "timestamp": (0.2, 0.7)},
                                           {"text": "좋아요", "timestamp": (0.8, 1.2)}], 1.5)
        self.assertEqual([entry["text"] for entry in entries], ["저장해요,", "좋아요!"])
        entries = align("저장해요", [{"text": "자장해요", "timestamp": (0.1, 0.5)}], 1.0)
        self.assertEqual(entries[0]["text"], "저장해요")
        self.assertLess(entries[0]["start"], entries[0]["end"])

    def test_transcript_timing_preserves_omitted_script_words(self):
        modules = {"torch": types.SimpleNamespace(), "transformers": types.SimpleNamespace(pipeline=None)}
        with patch.dict(sys.modules, modules):
            module = runpy.run_path(str(main.ROOT / "marketing" / "wav_to_srt_batch.py"))
        entries = module["align_transcript"]("맛있는 국밥 먹으러 가요", [
            {"text": "맛있는", "timestamp": (0.0, 0.6)},
            {"text": "먹으러 가요", "timestamp": (0.6, 1.6)}], 2.0)
        self.assertEqual([item["text"] for item in entries], ["맛있는", "국밥", "먹으러", "가요"])
        self.assertTrue(all(item["start"] < item["end"] for item in entries))

    def test_whisper_chunks_split_on_original_spaces(self):
        modules = {"torch": types.SimpleNamespace(), "transformers": types.SimpleNamespace(pipeline=None)}
        with patch.dict(sys.modules, modules):
            module = runpy.run_path(str(main.ROOT / "marketing" / "wav_to_srt_batch.py"))
        chunks = [{"text": "동해물과 백두산이", "timestamp": (0.0, 1.0)},
                  {"text": "마르", "timestamp": (1.0, 1.3)},
                  {"text": "고 닳도록.", "timestamp": (1.3, 2.0)}]
        entries = module["align_transcript"]("동해물과 백두산이 마르고 닳도록.", chunks, 2.1)
        self.assertEqual([entry["text"] for entry in entries], ["동해물과", "백두산이", "마르고", "닳도록."])
        self.assertTrue(all(entry["start"] < entry["end"] for entry in entries))

    def test_minimax_subtitles_change_by_space_delimited_words(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shots = [{"시작초": 0, "종료초": 4, "내레이션": "동해물과 백두산이 마르고 닳도록."}]
            with patch.dict("os.environ", {"WATERMARK_ENABLED": "false"}), patch.object(main.subprocess, "run"):
                main.add_subtitles(root / "raw.mp4", root / "video.mp4", shots)
            srt = (root / "subtitles.srt").read_text(encoding="utf-8-sig")
            for word in ("동해물과", "백두산이", "마르고", "닳도록."):
                self.assertIn("\n" + word + "\n", srt)


if __name__ == "__main__":
    unittest.main()
