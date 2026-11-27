"""ComfyUI에 요청하지 않고 참조 음성 연결을 검증한다."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from marketing import main


class Response:
    def __init__(self, data=None, content=b""):
        self.data = data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class ReferenceVoiceTests(unittest.TestCase):
    def test_narration_rejects_independent_gender_and_missing_reference(self):
        result = {"15초_스크립트": [{"발화_유형": "나레이션", "화자_ID": "S1"}],
                  "음성_연출": [{"화자_ID": "S1", "목소리_설명": "<Audio 1> 참조"}],
                  "minimax_prompt": "Use <Audio 1> for narrator S1. A woman enters the shop."}
        main.validate_narration_voice(result)
        result["minimax_prompt"] = "A female narrator uses <Audio 1>."
        with self.assertRaisesRegex(ValueError, "성별"):
            main.validate_narration_voice(result)
        result["minimax_prompt"] = "Use <Audio 1> for narrator S1."
        result["음성_연출"][0]["목소리_설명"] = "여성 나레이터"
        with self.assertRaisesRegex(ValueError, "성별"):
            main.validate_narration_voice(result)
        result["음성_연출"][0]["목소리_설명"] = "참조 음성"
        result["minimax_prompt"] = "Narrator S1 speaks."
        with self.assertRaisesRegex(ValueError, "<Audio 1>"):
            main.validate_narration_voice(result)

    def test_prompt_audio_reference_requires_wav(self):
        with self.assertRaisesRegex(ValueError, "참조 WAV"):
            main.generate_video("http://unused", [Path("photo.jpg")], "Use <Audio 1>", Path("video.mp4"))

    def test_narration_duration_uses_korean_character_count(self):
        text = "가" * 25
        result = {key: "내용" for key in ("영상_형태", "영상_컨셉", "스토리_요약", "기획_차별점", "핵심_메시지", "시청자_반응", "결말_의미")}
        result.update({"15초_스크립트": [
            {"시작초": 0, "종료초": 4, "내레이션": text, "발화_유형": "나레이션", "화자_ID": "S1"},
            {"시작초": 4, "종료초": 15, "내레이션": "", "발화_유형": "없음", "화자_ID": None},
        ], "음성_연출": [{"화자_ID": "S1", "목소리_설명": "목소리", "연출_이유": "이유"}],
            "minimax_prompt": f"<Audio 1> <Picture 1> (S1) <d>[Korean] {text}</d>",
            "선택_사진": [{"파일명": "photo.jpg", "선택_이유": "이유"}]})
        main.validate_plan(result, [Path("photo.jpg")])
        result["15초_스크립트"][0]["종료초"] = 3.9
        with self.assertRaisesRegex(ValueError, "25자에는 약 4.00초"):
            main.validate_plan(result, [Path("photo.jpg")])
        result["15초_스크립트"][0]["발화_유형"] = "화면발화"
        result["15초_스크립트"][1]["시작초"] = 3.9
        main.validate_plan(result, [Path("photo.jpg")])

    def test_dialogue_format_checks_language_speaker_and_order(self):
        result = {
            "15초_스크립트": [
                {"내레이션": "안녕하세요!", "화자_ID": "S1"},
                {"내레이션": "반갑습니다.", "화자_ID": "S2"},
            ],
            "minimax_prompt": "(S1) <d>[Korean] 안녕하세요!</d> (S2) <d>[Korean] 반갑습니다.</d>",
        }
        main.validate_dialogue_format(result)
        for invalid in (
            result["minimax_prompt"].replace("[Korean] ", "", 1),
            result["minimax_prompt"].replace("(S2)", "(S1)"),
            result["minimax_prompt"].replace("반갑습니다.", "다른 대사."),
        ):
            with self.subTest(prompt=invalid), self.assertRaises(ValueError):
                main.validate_dialogue_format({**result, "minimax_prompt": invalid})

    def test_workflow_attaches_only_requested_reference_audio(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            image, voice = root / "photo.jpg", root / "voice.wav"
            image.write_bytes(b"image")
            voice.write_bytes(b"audio")
            submitted = []

            def post(url, **kwargs):
                if url.endswith("/upload/image"):
                    return Response({"name": kwargs["files"]["image"][0], "subfolder": ""})
                submitted.append(kwargs["json"]["prompt"])
                return Response({"prompt_id": "abc"})

            def get(url, **kwargs):
                if "/history/" in url:
                    return Response({"abc": {"status": {"status_str": "success"}, "outputs": {"144": {"gifs": [{"filename": "video.mp4"}]}}}})
                return Response(content=b"video")

            with patch.object(main.requests, "post", side_effect=post), patch.object(main.requests, "get", side_effect=get), patch.object(main.time, "sleep"):
                main.generate_video("http://unused", [image], "prompt", root / "with.mp4", voice)
                main.generate_video("http://unused", [image], "prompt", root / "without.mp4")
            inputs = submitted[0]["235"]["inputs"]
            self.assertEqual(inputs["ref_audios.ref_audio_0"], ["9001", 0])
            self.assertEqual(submitted[0]["9001"]["class_type"], "LoadAudio")
            self.assertEqual(submitted[0]["9001"]["inputs"]["audio"], "voice.wav")
            self.assertNotIn("ref_audios.ref_audio_0", submitted[1]["235"]["inputs"])

    def test_fasth3_workflow_uses_uploaded_references_and_save_video_output(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            image, voice = root / "photo.jpg", root / "voice.wav"
            image.write_bytes(b"image")
            voice.write_bytes(b"audio")
            submitted = []

            def post(url, **kwargs):
                if url.endswith("/upload/image"):
                    return Response({"name": kwargs["files"]["image"][0]})
                submitted.append(kwargs["json"]["prompt"])
                return Response({"prompt_id": "abc"})

            def get(url, **kwargs):
                if "/history/" in url:
                    return Response({"abc": {"status": {"status_str": "success"}, "outputs": {"92": {"images": [{"filename": "video.mp4", "subfolder": "video", "type": "output"}]}}}})
                self.assertEqual(kwargs["params"], {"filename": "video.mp4", "subfolder": "video", "type": "output"})
                return Response(content=b"video")

            with patch.object(main.requests, "post", side_effect=post), patch.object(main.requests, "get", side_effect=get), patch.object(main.time, "sleep"):
                main.generate_video("http://unused", [image], "prompt", root / "video.mp4", voice, "video_fasth3_ominiref_r2v")
            payload = submitted[0]
            inputs = payload["136"]["inputs"]
            self.assertEqual(inputs["ref_images.ref_image_0"], ["9000", 0])
            self.assertEqual(inputs["ref_audios.ref_audio_0"], ["9001", 0])
            self.assertFalse(any(key.startswith(("ref_videos.", "ref_video_audios.")) for key in inputs))
            self.assertNotIn("ref_images.ref_image_1", inputs)
            self.assertNotIn("ref_audios.ref_audio_1", inputs)
            self.assertEqual(payload["138"]["inputs"]["value"], "prompt")
            self.assertEqual(payload["132"]["inputs"]["value"], 15)
            self.assertNotIn("161", payload)
            self.assertEqual((root / "video.mp4").read_bytes(), b"video")

    def test_rejects_mixed_modes_and_requires_known_mode(self):
        with self.assertRaises(ValueError):
            main.narration_reference([{"발화_유형": "나레이션"}, {"발화_유형": "화면발화"}])
        with self.assertRaises(ValueError):
            main.narration_reference([{}])
        self.assertIsNone(main.narration_reference([{"발화_유형": "화면발화"}]))


if __name__ == "__main__":
    unittest.main()
