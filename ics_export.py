"""Generate RFC 5545 ICS events from reviewed course data."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Timezone

from course_data import include_code_choice, normalize_course


def escape_ics(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    chunks, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            chunks.append(current)
            current = " " + char
        else:
            current += char
    chunks.append(current)
    return "\r\n".join(chunks)


def export_ics(data: dict, output: Path, include_course_code: bool | None = None) -> int:
    monday = dt.date.fromisoformat(data["first_monday"])
    if monday.weekday() != 0:
        raise ValueError("first_monday 必须是星期一")
    include_code = include_code_choice(data) if include_course_code is None else include_course_code
    tz = data.get("timezone", "Asia/Shanghai")
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"未知时区：{tz}") from exc
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Course Calendar//CN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-TIMEZONE:{tz}"]
    timezone_end = monday + dt.timedelta(weeks=30)
    timezone_definition = Timezone.from_tzid(
        tz, first_date=dt.date(monday.year, 1, 1),
        last_date=dt.date(timezone_end.year + 1, 12, 31))
    lines.extend(timezone_definition.to_ical().decode("utf-8").splitlines())
    schedules: dict[tuple, set[int]] = {}
    for course in data["courses"]:
        normalize_course(course)
        title = str(course["title"]).strip()
        code = course["course_code"]
        if code and not code.isdigit():
            raise ValueError(f"课程序号必须是阿拉伯数字：{code}")
        day = int(course["day"])
        weeks = [int(w) for w in course["weeks"]]
        start, end = course["start"], course["end"]
        if course.get("needs_review", False):
            raise ValueError(f"课程尚未核对：{title}；核对并改为 needs_review=false 后导出")
        if not title or not weeks or not 0 <= day <= 6 or any(not 1 <= w <= 30 for w in weeks):
            raise ValueError(f"课程信息缺失或周次无效：{course.get('raw_text', title)}")
        if dt.time.fromisoformat(start) >= dt.time.fromisoformat(end):
            raise ValueError(f"课程结束时间不晚于开始时间：{title}")
        key = (title, code, day, start, end, str(course.get("location", "")), str(course.get("teacher", "")))
        schedules.setdefault(key, set()).update(weeks)

    for key, week_set in schedules.items():
        title, code, day, start, end, location, teacher = key
        weeks = sorted(week_set)
        first_date = monday + dt.timedelta(days=(weeks[0] - 1) * 7 + day)
        last_date = monday + dt.timedelta(days=(weeks[-1] - 1) * 7 + day)
        summary = title + (f" ({code})" if include_code and code else "")
        uid = hashlib.sha256(repr((monday.isoformat(), key)).encode("utf-8")).hexdigest()[:24] + "@course-calendar"
        description = "教学周：" + ",".join(map(str, weeks))
        if teacher:
            description += "\n教师：" + teacher
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                  f"DTSTART;TZID={tz}:{first_date.strftime('%Y%m%d')}T{start.replace(':', '')}00",
                  f"DTEND;TZID={tz}:{first_date.strftime('%Y%m%d')}T{end.replace(':', '')}00",
                  f"SUMMARY:{escape_ics(summary)}", f"LOCATION:{escape_ics(location)}",
                  f"DESCRIPTION:{escape_ics(description)}"]
        if len(weeks) > 1:
            last_start = dt.datetime.combine(last_date, dt.time.fromisoformat(start), zone)
            until = last_start.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            lines.append(f"RRULE:FREQ=WEEKLY;UNTIL={until}")
            omitted = sorted(set(range(weeks[0], weeks[-1] + 1)) - week_set)
            if omitted:
                exception_dates = [monday + dt.timedelta(days=(week - 1) * 7 + day) for week in omitted]
                values = ",".join(date.strftime("%Y%m%d") + "T" + start.replace(":", "") + "00" for date in exception_dates)
                lines.append(f"EXDATE;TZID={tz}:{values}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    output.write_bytes(("\r\n".join(fold(line) for line in lines) + "\r\n").encode("utf-8"))
    return len(schedules)
