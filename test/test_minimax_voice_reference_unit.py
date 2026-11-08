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
    def test_narration_duration_uses_korean_character_count(self):
        text = "가" * 25
        result = {key: "내용" for key in ("영상_형태", "영상_컨셉", "스토리_요약", "기획_차별점", "핵심_메시지", "시청자_반응", "결말_의미")}
        result.update({"15초_스크립트": [
            {"시작초": 0, "종료초": 4, "자막": "", "내레이션": text, "발화_유형": "나레이션", "화자_ID": "S1"},
            {"시작초": 4, "종료초": 15, "자막": "", "내레이션": "", "발화_유형": "없음", "화자_ID": None},
        ], "음성_연출": [{"화자_ID": "S1", "목소리_설명": "목소리", "연출_이유": "이유"}],
            "minimax_prompt": f"<Audio 1> <Picture 1> <d>{text}</d>",
            "선택_사진": [{"파일명": "photo.jpg", "선택_이유": "이유"}]})
        main.validate_plan(result, [Path("photo.jpg")])
        result["15초_스크립트"][0]["종료초"] = 3.9
        with self.assertRaisesRegex(ValueError, "25자에는 약 4.00초"):
            main.validate_plan(result, [Path("photo.jpg")])
        result["15초_스크립트"][0]["발화_유형"] = "화면발화"
        result["15초_스크립트"][1]["시작초"] = 3.9
        main.validate_plan(result, [Path("photo.jpg")])

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

    def test_rejects_mixed_modes_and_requires_known_mode(self):
        with self.assertRaises(ValueError):
            main.narration_reference([{"발화_유형": "나레이션"}, {"발화_유형": "화면발화"}])
        with self.assertRaises(ValueError):
            main.narration_reference([{}])
        self.assertIsNone(main.narration_reference([{"발화_유형": "화면발화"}]))


if __name__ == "__main__":
    unittest.main()
