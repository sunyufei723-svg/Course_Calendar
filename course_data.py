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


def normalize_course(course: dict) -> dict:
    """Upgrade older JSON where the code was attached to title."""
    title, old_code = split_course_code(course.get("title", ""))
    if not old_code:
        for raw_line in course.get("raw_text", []):
            _, old_code = split_course_code(raw_line)
            if old_code:
                break
    course["title"] = title
    course["course_code"] = str(course["course_code"]).strip() if "course_code" in course else old_code
    course.setdefault("teacher", "")
    return course


def include_code_choice(data: dict) -> bool:
    """Preserve the title style of JSON created before this option existed."""
    if "include_course_code" in data:
        return bool(data["include_course_code"])
    return any(split_course_code(course.get("title", ""))[1] for course in data.get("courses", []))


def split_teacher_title(raw_title: str) -> tuple[str, str, str]:
    text, code = split_course_code(raw_title)
    latin = re.match(r"^([A-Z][A-Z\s]+)(?=[\u4e00-\u9fff])", text)
    if latin:
        teacher, title = latin.group(1).strip(), text[latin.end():].strip()
    else:
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[1] and re.fullmatch(r"[\u4e00-\u9fff]{2,4}|[A-Z]{2,}", parts[0]):
            teacher, title = parts
        else:
            # OCR can remove a visible gap in detailed timetables. Without a
            # course code, a Chinese prefix is just as likely to be the title.
            common_starts = ("管理", "机器", "组织", "体育", "数据", "统计", "计量", "属性", "文本", "线性", "消费")
            teacher_length = 2 if text[2:].startswith(common_starts) else 3
            if code and re.match(r"^[\u4e00-\u9fff]{5,}", text):
                teacher, title = text[:teacher_length], text[teacher_length:]
            else:
                teacher, title = "", text
    return teacher, title, code


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


def extract_location(text: str) -> str:
    if "周" in text:
        tail = text.split("周", 1)[1]
    else:
        match = re.search(r"[（(]\s*[\d\s,，、.~-]+?[,，.](?=[^\d\s])(.+)", text)
        if not match:
            return ""
        tail = match.group(1)
    return tail.strip(" ,，()（）").rstrip("()（）")
