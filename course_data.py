"""Course fields and OCR text parsing shared by CLI and GUI."""

from __future__ import annotations

import re

DAY_NAMES = "一二三四五六日"
CODE_RE = re.compile(r"\s*[（(](\d{3,6})[)）]\s*$")


def split_course_code(value: str) -> tuple[str, str]:
    """Keep leading zeroes in the course code."""
    text = str(value).strip()
    match = CODE_RE.search(text)
    return (text[:match.start()].strip(), match.group(1)) if match else (text, "")


def split_teacher_title(raw_title: str) -> tuple[str, str, str]:
    """Split a teacher only when OCR shows a space before the course title."""
    text, code = split_course_code(raw_title)
    parts = text.split(maxsplit=1)
    return (parts[0], parts[1], code) if len(parts) == 2 else ("", text, code)


def parse_weeks(text: str) -> list[int]:
    parenthesis = re.search(r"[（(]\s*([\d\s,，、.~-]+)", text)
    prefix = parenthesis.group(1).strip(" ,，. ") if parenthesis else re.split(r"周", text, maxsplit=1)[0]
    match = re.search(r"(?:第|\()\s*([\d\s,，、.~-]+)$", prefix)
    if not match:
        match = re.search(r"(\d[\d\s,，、.~-]*)$", prefix)
    if not match:
        return []
    numbers = set()
    for token in re.split(r"[,，、.\s]+", match.group(1).strip()):
        if not token:
            continue
        pair = re.fullmatch(r"(\d{1,2})[-~](\d{1,2})", token)
        if pair:
            a, b = map(int, pair.groups())
            if a <= b <= 30:
                numbers.update(range(a, b + 1))
        elif token.isdigit() and 1 <= int(token) <= 30:
            numbers.add(int(token))
    return sorted(numbers)


def extract_location(lines: str | list[str]) -> str:
    """Find a venue anywhere in the course details, including wrapped OCR lines."""
    text = lines if isinstance(lines, str) else "".join(lines)
    text = re.sub(r"\s+", " ", text)
    # A building/teaching marker is more reliable than its position relative
    # to week notation. Keep a parenthesized room description when present.
    candidates = re.findall(
        r"[^,，;；()（）]*[教楼场][^,，;；()（）]*(?:[（(][^()（）]*[)）])?", text
    )
    venues = []
    for candidate in candidates:
        candidate = re.sub(r"(?:第\s*)?\d{1,2}(?:\s*[-~—–]\s*\d{1,2})?\s*周", "", candidate)
        candidate = re.sub(r"^[\s:：.、]*(?:(?:上课|教学)?地点(?:信息)?|教室\s*[:：])\s*[:：]?\s*", "", candidate)
        candidate = re.sub(r"^[\s\d,，、.~-]+", "", candidate).strip(" :：,，、.")
        if (not candidate or candidate in {"教学", "教", "楼", "场"} or
                re.fullmatch(r"教学周(?:次)?\s*[:：]?\s*[\d\s,，、.~-]*", candidate)):
            continue
        score = 2 * bool(re.search(r"[教楼场][A-Za-z]?\d{2,4}", candidate)) + bool("楼" in candidate)
        venues.append((score, candidate))
    if venues:
        return max(venues, key=lambda item: item[0])[1]

    # Outdoor venues lack 教/楼, but an explicit week/venue separator still
    # makes them identifiable (for example, "(1-16,武东田径场)").
    match = re.search(r"[（(]\s*[\d\s,，、.~-]+?[,，.](?=[^\d\s])([^()（）]+)", text)
    return match.group(1).strip(" :：,，、.()（）") if match else ""
