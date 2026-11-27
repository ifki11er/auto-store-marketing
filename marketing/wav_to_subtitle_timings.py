"""Whisper로 생성된 WAV의 단어별 자막 시간을 구한다."""
import json
import contextlib
import os
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from transformers import pipeline


def normalized(text):
    return "".join(c.lower() for c in unicodedata.normalize("NFC", text) if c.isalnum())


def check_last_sentence(script, recognized):
    """발화 끝부분이 대본의 마지막 문장과 대체로 일치하는지 확인한다."""
    sentences = [part.strip() for part in re.split(r"[.!?。？！\n]+", script) if part.strip()]
    expected = normalized(sentences[-1]) if sentences else ""
    heard = normalized(recognized)
    if not expected or not heard:
        return {"complete": False, "expected": sentences[-1] if sentences else "", "recognized": recognized}
    tail = heard[-max(len(expected) * 2, len(expected) + 8):]
    matches = sum(block.size for block in SequenceMatcher(None, expected, tail, autojunk=False).get_matching_blocks())
    ending = expected[-min(4, len(expected)):]
    tail_end = heard[-max(len(ending) * 2, 8):]
    ending_matches = sum(block.size for block in SequenceMatcher(None, ending, tail_end, autojunk=False).get_matching_blocks())
    complete = matches / len(expected) >= 0.75 and ending_matches / len(ending) >= 0.75
    return {"complete": complete, "expected": sentences[-1], "recognized": recognized}


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
    index = 0
    while index < len(entries):
        if entries[index]["end"] > entries[index]["start"]:
            index += 1
            continue
        first = index
        while index < len(entries) and entries[index]["end"] <= entries[index]["start"]:
            index += 1
        left, right = max(0, first - 1), min(len(entries), index + 1)
        start, stop = entries[left]["start"], entries[right - 1]["end"]
        if stop <= start:
            raise ValueError("Whisper 음성 시간으로 자막을 정렬할 수 없습니다")
        weights = [len(normalized(item["text"])) for item in entries[left:right]]
        total, elapsed = sum(weights), 0
        for item, count in zip(entries[left:right], weights):
            item["start"] = start + (stop - start) * elapsed / total
            elapsed += count
            item["end"] = start + (stop - start) * elapsed / total
        index = right
    return entries


def main():
    os.environ["PATH"] = str(Path(sys.argv[1])) + os.pathsep + os.environ.get("PATH", "")
    with contextlib.redirect_stdout(sys.stderr):
        transcriber = pipeline("automatic-speech-recognition", model="openai/whisper-small", device=0)
    for line in sys.stdin:
        try:
            job = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                transcription = transcriber(job["path"], return_timestamps="word", generate_kwargs={"language": "ko"})
            chunks = transcription.get("chunks") or []
            recognized = "".join(chunk.get("text", "") for chunk in chunks)
            if not normalized(recognized):
                raise ValueError("Whisper가 음성을 인식하지 못해 마지막 문장을 확인할 수 없습니다")
            check = check_last_sentence(job["text"], recognized)
            result = {"check": check}
            if check["complete"]:
                result["segments"] = align_transcript(job["text"], chunks, job["duration"])
        except Exception as exc:
            result = {"error": str(exc)}
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
