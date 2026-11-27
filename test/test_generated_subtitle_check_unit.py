import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from marketing import main


class GeneratedSubtitleCheckTests(unittest.TestCase):
    def test_video_generation_skips_gpt_subtitle_check(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.mp4"
            with patch.object(main, "generate_video", return_value="job-id") as generate, \
                 patch.object(main, "recorded_client") as recorded:
                result = main.generate_video_without_subtitles(object(), "base", [], "prompt", output)
            self.assertEqual(result, "job-id")
            generate.assert_called_once_with("base", [], "prompt", output, None, None)
            recorded.assert_not_called()


if __name__ == "__main__":
    unittest.main()
