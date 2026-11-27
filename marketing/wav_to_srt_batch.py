"""Whisper로 생성된 WAV의 단어 시간을 구하고 원래 대본과 비교해 자막을 만든다."""
import json
import os
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import torch
from transformers import pipeline


def normalized(text):
    return "".join(c.lower() for c in unicodedata.normalize("NFC", text) if c.isalnum())


def timestamp(value):
    ms = round(value * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def align_transcript(script, chunks, duration):
    script = unicodedata.normalize("NFC", script)
    recognized = normalized("".join(c.get("text", "") for c in chunks))
    target = normalized(script)
    if not chunks or not recognized or not target:
        raise ValueError("음성 시간 정보 또는 나레이션이 없어 자막을 만들 수 없습니다")
    char_times = []
    previous = 0.0
    for chunk in chunks:
        amount = len(normalized(chunk.get("text", "")))
        start, stop = chunk.get("timestamp", (None, None))
        if start is None or stop is None or float(start) < previous or float(stop) <= float(start) or float(stop) > duration + 0.05:
            raise ValueError("Whisper가 반환한 자막 시간이 올바르지 않습니다")
        if not amount:
            raise ValueError("내용이 없는 Whisper 자막 구간입니다")
        span = float(stop) - float(start)
        char_times.extend((float(start) + span * i / amount, float(start) + span * (i + 1) / amount)
                          for i in range(amount))
        previous = float(start)
    # 인식이 다른 부분은 주변에서 일치한 글자의 시간을 기준으로 추정한다.
    script_times = [None] * len(target)
    for block in SequenceMatcher(None, recognized, target, autojunk=False).get_matching_blocks():
        for index in range(block.size):
            script_times[block.b + index] = char_times[block.a + index]
    index = 0
    while index < len(script_times):
        if script_times[index] is not None:
            index += 1
            continue
        start_index = index
        while index < len(script_times) and script_times[index] is None:
            index += 1
        left = script_times[start_index - 1][1] if start_index else char_times[0][0]
        right = script_times[index][0] if index < len(script_times) else char_times[-1][1]
        right = max(left, right)
        count = index - start_index
        for offset in range(count):
            script_times[start_index + offset] = (left + (right - left) * offset / count,
                                                   left + (right - left) * (offset + 1) / count)
    entries, cursor = [], 0
    for match in re.finditer(r"\S+", script):
        word = match.group()
        count = len(normalized(word))
        if not count:
            if entries:
                entries[-1]["text"] += " " + word
            continue
        if cursor + count > len(script_times):
            raise ValueError("대본과 음성 구간을 일치시킬 수 없습니다")
        entries.append({"start": script_times[cursor][0], "end": script_times[cursor + count - 1][1], "text": word})
        cursor += count
    if cursor != len(script_times):
        raise ValueError("대본의 일부가 음성 자막에서 누락되었습니다")
    for current, following in zip(entries, entries[1:]):
        if current["end"] > following["start"]:
            current["end"] = following["start"]
    if any(item["end"] <= item["start"] for item in entries):
        # 인식에서 단어가 통째로 빠지는 등 시간 추정이 불가능한 경우 전체 발화 시간에 배분한다.
        start, stop = char_times[0][0], char_times[-1][1]
        cursor = 0
        for item in entries:
            count = len(normalized(item["text"]))
            item["start"] = start + (stop - start) * cursor / len(target)
            cursor += count
            item["end"] = start + (stop - start) * cursor / len(target)
    return entries


def main():
    jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    os.environ["PATH"] = str(Path(sys.argv[2])) + os.pathsep + os.environ.get("PATH", "")
    device = 0 if torch.cuda.is_available() else -1
    transcriber = pipeline("automatic-speech-recognition", model="openai/whisper-small", device=device)
    results = []
    for job in jobs:
        try:
            result = transcriber(job["path"], return_timestamps="word", generate_kwargs={"language": "ko"})
            entries = align_transcript(job["text"], result["chunks"], job["duration"])
            srt = "\n".join(f"{i}\n{timestamp(e['start'])} --> {timestamp(e['end'])}\n{e['text']}\n" for i, e in enumerate(entries, 1))
            Path(job["srt"]).write_text(srt, encoding="utf-8-sig")
            results.append({"folder": job["folder"], "segments": entries})
        except Exception as exc:
            results.append({"folder": job["folder"], "error": str(exc)})
    Path(sys.argv[1]).with_name("whisper_results.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
