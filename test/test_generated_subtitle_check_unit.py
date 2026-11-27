import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from marketing import main


class GeneratedSubtitleCheckTests(unittest.TestCase):
    def test_retry_and_accept_only_clean_video(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.mp4"

            def generate(base, photos, prompt, candidate, reference, workflow):
                candidate.write_text(candidate.stem, encoding="utf-8")
                return candidate.stem

            with patch.object(main, "recorded_client", return_value=object()), \
                 patch.object(main, "generate_video", side_effect=generate) as generate_mock, \
                 patch.object(main, "check_generated_subtitles", side_effect=[
                     {"판정": "있음", "이유": "자막 발견"}, {"판정": "없음", "이유": "자막 없음"}]):
                result = main.generate_video_without_subtitles(object(), "base", [], "prompt", output)

            self.assertEqual(generate_mock.call_count, 2)
            self.assertEqual(result, "raw_attempt_2")
            self.assertEqual(output.read_text(encoding="utf-8"), "raw_attempt_2")
            self.assertEqual((Path(directory) / "raw_attempt_1.mp4").read_text(encoding="utf-8"), "raw_attempt_1")

    def test_uncertain_result_does_not_accept_video(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.mp4"
            with patch.object(main, "recorded_client", return_value=object()), \
                 patch.object(main, "generate_video", return_value="id"), \
                 patch.object(main, "check_generated_subtitles", return_value={"판정": "불확실", "이유": "판독 불가"}):
                with self.assertRaisesRegex(ValueError, "불확실"):
                    main.generate_video_without_subtitles(object(), "base", [], "prompt", output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
