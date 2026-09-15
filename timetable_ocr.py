"""Recognize colored courses in either orientation of a grid timetable."""

from __future__ import annotations

import re
from statistics import median
from pathlib import Path

import cv2
import numpy as np

from course_data import DAY_NAMES, extract_location, parse_weeks, split_course_code, split_teacher_title

TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*[-~—–]\s*(\d{1,2}:\d{2})")
WEEK_RE = re.compile(r"(?:第)?\s*(\d{1,2})(?:\s*[-~—–]\s*(\d{1,2}))?\s*周")
DAY_RE = re.compile(r"(?:星期|周|礼拜)\s*([一二三四五六日天])")


def _has_weeks(value: str) -> bool:
    return bool(WEEK_RE.search(value) or re.search(r"[（(]\s*\d{1,2}\s*[-~]", value))


def _title_with_visual_space(line: dict) -> str:
    """Restore one strong visual gap using ratios to glyph width, not pixels."""
    text = line["text"]
    if any(char.isspace() for char in text):
        return text
    boxes = line.get("word_boxes", [])
    chars = line.get("word_content", [])
    if boxes is None or chars is None:
        return text
    if len(boxes) != len(chars) or "".join(chars) != text or not all(len(char) == 1 for char in chars):
        return text
    title, _ = split_course_code(text)
    if len(title) < 4:
        return text
    widths = [max(point[0] for point in box) - min(point[0] for point in box)
              for box in boxes[:len(title)]]
    chinese_widths = [width for char, width in zip(chars, widths)
                      if "\u4e00" <= char <= "\u9fff" and width > 0]
    usable_widths = [width for width in widths if width > 0]
    if not usable_widths:
        return text
    reference = median(chinese_widths or usable_widths)
    gaps = [max(0, min(point[0] for point in boxes[i + 1]) -
                max(point[0] for point in boxes[i])) for i in range(len(title) - 1)]
    positive = [gap for gap in gaps if gap > 0]
    if len(positive) < 2:
        return text
    baseline = median(positive)
    ranked = sorted(enumerate(gaps), key=lambda item: item[1], reverse=True)
    index, gap = ranked[0]
    runner_up = ranked[1][1]
    prefix = title[:index + 1]
    # Chinese names occupy a few glyphs; longer all-uppercase OCR names can
    # also precede a Chinese title. Neither check guesses a course-name prefix.
    plausible_prefix = (2 <= len(prefix) <= 4 and all("\u4e00" <= c <= "\u9fff" for c in prefix) or
                        2 <= len(prefix) <= 15 and prefix.isascii() and prefix.isupper() and prefix.isalpha())
    if (plausible_prefix and gap / reference >= 0.5 and
            gap >= baseline * 1.5 and gap >= runner_up * 1.1):
        return title[:index + 1] + " " + text[index + 1:]
    return text


def _axis_step(lines: list[dict], axis: str) -> float:
    positions = sorted(line[axis] for line in lines)
    differences = [b - a for a, b in zip(positions, positions[1:]) if b - a > 5]
    return float(np.median(differences)) if differences else 60.0


def _layout(times: list[dict], days: list[dict]) -> str:
    if len(times) < 2 or not days:
        raise ValueError("未识别出时间和星期标签；请换清晰截图，或打开 JSON 手动填写。")
    x_span = max(line["x"] for line in times) - min(line["x"] for line in times)
    y_span = max(line["y"] for line in times) - min(line["y"] for line in times)
    orientation = "horizontal_time" if x_span >= y_span * 1.5 else "vertical_time" if y_span >= x_span * 1.5 else ""
    if not orientation:
        raise ValueError("时间轴方向不明确；请使用完整网格截图。")
    if len(days) >= 2:
        day_axis = "y" if orientation == "horizontal_time" else "x"
        other_axis = "x" if day_axis == "y" else "y"
        day_span = max(line[day_axis] for line in days) - min(line[day_axis] for line in days)
        other_span = max(line[other_axis] for line in days) - min(line[other_axis] for line in days)
        if day_span < max(10, other_span * 1.5):
            raise ValueError("星期轴与时间轴不成网格；请使用完整课表截图。")
    return orientation


def _background_candidates(rgb: np.ndarray, x0: int, y0: int) -> list[tuple[np.ndarray, float]]:
    # If a dense timetable is mostly one course color, try other common colors too.
    pixels = rgb[y0::3, x0::3].reshape(-1, 3)
    bins = ((pixels[:, 0].astype(np.uint16) >> 4) << 8) | ((pixels[:, 1].astype(np.uint16) >> 4) << 4) | (pixels[:, 2].astype(np.uint16) >> 4)
    counts = np.bincount(bins, minlength=4096)
    candidates = []
    for color_bin in np.argsort(counts)[-5:][::-1]:
        share = float(counts[color_bin] / len(pixels))
        if share < 0.01:
            continue
        color = np.median(pixels[bins == color_bin], axis=0).astype(np.uint8)
        candidates.append((color, share))
    return candidates


def _boxes(rgb: np.ndarray, background: np.ndarray, x0: int, y0: int,
           min_width: float, min_height: float) -> list[tuple[int, int, int, int]]:
    region = rgb[y0:, x0:]
    lab = cv2.cvtColor(region, cv2.COLOR_RGB2LAB).astype(np.int32)
    bg_lab = cv2.cvtColor(background.reshape(1, 1, 3), cv2.COLOR_RGB2LAB).astype(np.int32)[0, 0]
    distance = np.sqrt(np.sum((lab - bg_lab) ** 2, axis=2))
    r, g, b = [region[:, :, i].astype(np.uint16) >> 4 for i in range(3)]
    color_bins = ((r << 8) | (g << 4) | b).astype(np.uint16)
    foreground = distance > 12
    counts = np.bincount(color_bins[foreground].ravel(), minlength=4096)
    candidates = []
    minimum_area = max(350, min_width * min_height * 0.3)
    for color_bin in np.flatnonzero(counts >= minimum_area):
        mask = ((color_bins == color_bin) & foreground).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        for x, y, w, h, area in stats[1:count]:
            if area < minimum_area or w < min_width or h < min_height:
                continue
            if area / (w * h) < 0.38:  # thin table lines and glyphs
                continue
            candidates.append((int(x + x0), int(y + y0), int(w), int(h), int(area)))
    candidates.sort(key=lambda item: item[4], reverse=True)
    chosen = []
    for x, y, w, h, area in candidates:
        if any(_overlap((x, y, w, h), previous) > 0.75 for previous in chosen):
            continue
        chosen.append((x, y, w, h))
    return chosen


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    shared = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))
    return shared / min(aw * ah, bw * bh)


def _merge_adjacent(courses: list[dict]) -> list[dict]:
    """Join neighboring timetable cells for one continuous class session."""
    def name_key(value: str) -> str:
        return re.sub(r"[^\w]+", "", value).casefold()

    merged = []
    for course in sorted(courses, key=lambda item: (item["day"], item["_slot_first"])):
        previous = merged[-1] if merged else None
        current_name = name_key(course["title"])
        previous_name = name_key(previous["title"]) if previous else ""
        same_name = (current_name == previous_name or
                     min(len(current_name), len(previous_name)) >= 3 and
                     (current_name.startswith(previous_name) or previous_name.startswith(current_name)))
        if (previous and previous["day"] == course["day"] and
                previous["_slot_last"] + 1 == course["_slot_first"] and
                same_name and previous["teacher"] == course["teacher"] and
                previous["course_code"] == course["course_code"] and
                previous["weeks"] == course["weeks"] and
                previous["location"] == course["location"]):
            previous["end"] = course["end"]
            previous["_slot_last"] = course["_slot_last"]
            if len(current_name) > len(previous_name):
                previous["title"] = course["title"]
            for line in course["raw_text"]:
                if line not in previous["raw_text"]:
                    previous["raw_text"].append(line)
            ax, ay, aw, ah = previous["source_box"]
            bx, by, bw, bh = course["source_box"]
            previous["source_box"] = [min(ax, bx), min(ay, by),
                                      max(ax + aw, bx + bw) - min(ax, bx),
                                      max(ay + ah, by + bh) - min(ay, by)]
        else:
            merged.append(course)
    for course in merged:
        course.pop("_slot_first")
        course.pop("_slot_last")
    return merged


def recognize(image_path: Path, first_monday: str) -> dict:
    # OCR runs several native inference sessions; avoid multiplying OpenCV workers.
    cv2.setNumThreads(1)
    from rapidocr_onnxruntime import RapidOCR

    try:
        image = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except (OSError, cv2.error) as exc:
        raise ValueError(f"图片读取失败：{image_path}") from exc
    if image is None:
        raise ValueError(f"无法打开图片：{image_path}")
    try:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    except cv2.error as exc:
        raise ValueError("图片解码失败或图像处理资源不足；请稍后重试。") from exc
    result, _ = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)(rgb, return_word_box=True)
    lines = []
    for row in result or []:
        box, value, confidence = row[:3]
        points = np.asarray(box)
        lines.append({"x": float(points[:, 0].mean()), "y": float(points[:, 1].mean()),
                      "box": [int(points[:, 0].min()), int(points[:, 1].min()),
                              int(np.ptp(points[:, 0])), int(np.ptp(points[:, 1]))],
                      "text": value.strip(), "confidence": round(float(confidence), 3),
                      "word_boxes": row[3] if len(row) > 3 else [],
                      "word_content": row[4] if len(row) > 4 else []})
    times = [line for line in lines if TIME_RE.search(line["text"])]
    days = [line for line in lines if DAY_RE.search(line["text"])]
    orientation = _layout(times, days)
    time_axis, day_axis = ("x", "y") if orientation == "horizontal_time" else ("y", "x")
    times.sort(key=lambda line: line[time_axis])
    days.sort(key=lambda line: line[day_axis])
    time_step = _axis_step(times, time_axis)
    day_step = _axis_step(days, day_axis)
    if orientation == "horizontal_time":
        x0 = max(0, int(times[0]["x"] - time_step * 0.55))
        y0 = max(0, int(days[0]["y"] - day_step * 0.75))
        min_width, min_height = time_step * 0.55, day_step * 0.3
    else:
        x0 = max(0, int(days[0]["x"] - day_step * 0.75))
        y0 = max(0, int(times[0]["y"] - time_step * 0.55))
        min_width, min_height = day_step * 0.3, time_step * 0.55
    def read_boxes(boxes: list[tuple[int, int, int, int]]) -> list[dict]:
        courses = []
        for x, y, w, h in boxes:
            axis_middle = y + h / 2 if day_axis == "y" else x + w / 2
            day_line = min(days, key=lambda line: abs(line[day_axis] - axis_middle))
            if abs(day_line[day_axis] - axis_middle) > day_step * 0.9:
                continue
            day_match = DAY_RE.search(day_line["text"])
            day_symbol = "日" if day_match.group(1) == "天" else day_match.group(1)
            day = DAY_NAMES.index(day_symbol)
            axis_start, axis_end = (x, x + w) if time_axis == "x" else (y, y + h)
            covered = [i for i, line in enumerate(times) if axis_start - 3 <= line[time_axis] <= axis_end + 3]
            if not covered:
                continue
            block_lines = sorted((line for line in lines if x - 6 <= line["x"] <= x + w + 6
                                  and y - 6 <= line["y"] <= y + h + 6
                                  and not TIME_RE.search(line["text"]) and not DAY_RE.search(line["text"])),
                                 key=lambda line: line["y"])
            texts = [line["text"] for line in block_lines]
            if not texts:
                continue
            title_lines = [line for line in block_lines if not _has_weeks(line["text"])]
            titles = [line["text"] for line in title_lines]
            if not titles or sum(bool(re.search(r"[（(]\d{3,6}[)）]", text)) for text in titles) > 1:
                continue
            week_text = " ".join(text for text in texts if _has_weeks(text))
            teacher, title, course_code = split_teacher_title(_title_with_visual_space(title_lines[0]))
            details = list(texts)
            details.remove(titles[0])
            first, last = min(covered), max(covered)
            start = TIME_RE.search(times[first]["text"]).group(1)
            end = TIME_RE.search(times[last]["text"]).group(2)
            courses.append({"teacher": teacher, "title": title, "course_code": course_code,
                            "day": day, "start": start, "end": end,
                            "weeks": parse_weeks(week_text), "location": extract_location(details),
                            "raw_text": texts, "source_box": [x, y, w, h], "needs_review": True,
                            "_slot_first": first, "_slot_last": last})
        return courses

    expected_titles = sum(bool(re.search(r"[（(]\d{3,6}[)）]\s*$", line["text"])) for line in lines)
    background, background_share, boxes, courses = None, 0.0, [], []
    for candidate, share in _background_candidates(rgb, x0, y0):
        try:
            candidate_boxes = _boxes(rgb, candidate, x0, y0, min_width, min_height)
        except cv2.error as exc:
            raise ValueError("课程色块分割失败；请稍后重试或换清晰截图。") from exc
        candidate_courses = read_boxes(candidate_boxes)
        if len(candidate_courses) > len(courses):
            background, background_share, boxes, courses = candidate, share, candidate_boxes, candidate_courses
        if expected_titles and len(courses) >= expected_titles:
            break
    if not courses:
        raise ValueError("未识别出课程色块；请检查星期/时间标签与课表清晰度。")
    if expected_titles and len(courses) < expected_titles:
        raise ValueError(f"OCR 看到了约 {expected_titles} 个课程标题，但只定位到 {len(courses)} 个色块；请换清晰截图或手动整理 JSON。")
    # Simple selection grids may print the same title in each period without
    # course codes or week details. Their pale cells can evade color splitting.
    if not expected_titles and not any(_has_weeks(line["text"]) for line in lines):
        occupied = {(course["day"], slot) for course in courses
                    for slot in range(course["_slot_first"], course["_slot_last"] + 1)}
        for line in lines:
            if (TIME_RE.search(line["text"]) or DAY_RE.search(line["text"]) or
                    line["x"] < x0 or line["y"] < y0):
                continue
            day_line = min(days, key=lambda item: abs(item[day_axis] - line[day_axis]))
            slot = min(range(len(times)), key=lambda i: abs(times[i][time_axis] - line[time_axis]))
            if (abs(day_line[day_axis] - line[day_axis]) > day_step * 0.45 or
                    abs(times[slot][time_axis] - line[time_axis]) > time_step * 0.45):
                continue
            day_symbol = DAY_RE.search(day_line["text"]).group(1).replace("天", "日")
            day = DAY_NAMES.index(day_symbol)
            if (day, slot) in occupied:
                continue
            teacher, title, code = split_teacher_title(_title_with_visual_space(line))
            if not title:
                continue
            period = TIME_RE.search(times[slot]["text"])
            courses.append({"teacher": teacher, "title": title, "course_code": code,
                            "day": day, "start": period.group(1), "end": period.group(2),
                            "weeks": [], "location": "", "raw_text": [line["text"]],
                            "source_box": line["box"], "needs_review": True,
                            "_slot_first": slot, "_slot_last": slot})
            occupied.add((day, slot))
    courses = _merge_adjacent(courses)
    return {"first_monday": first_monday, "timezone": "Asia/Shanghai", "include_course_code": True,
            "recognition": {"layout": orientation, "background_rgb": background.tolist(),
                            "background_share": round(background_share, 3), "candidate_boxes": len(boxes)},
            "courses": sorted(courses, key=lambda course: (course["day"], course["start"]))}
