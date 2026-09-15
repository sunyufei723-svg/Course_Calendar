"""Desktop editor for recognized timetable courses."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from course_data import DAY_NAMES, include_code_choice, normalize_course, parse_weeks
from ics_export import export_ics
from timetable_ocr import recognize


def weeks_to_text(weeks: list[int]) -> str:
    return ",".join(map(str, weeks))


def text_to_weeks(value: str) -> list[int]:
    text = value.strip()
    if not text:
        return []
    return parse_weeks("(" + text + ",)")


def previous_monday(today: dt.date | None = None) -> dt.date:
    """Return the closest Monday strictly before today."""
    current = today or dt.date.today()
    return current - dt.timedelta(days=current.weekday() or 7)


def course_review_issue(course: dict) -> str | None:
    """Return the field that still blocks a reviewed course from export."""
    normalize_course(course)
    if not str(course.get("title", "")).strip():
        return "缺少课程名"
    try:
        weeks = [int(week) for week in course.get("weeks", [])]
        day = int(course.get("day", -1))
        start = dt.time.fromisoformat(str(course.get("start", "")))
        end = dt.time.fromisoformat(str(course.get("end", "")))
    except (TypeError, ValueError):
        return "周次、星期或时间格式无效"
    if not weeks or any(not 1 <= week <= 30 for week in weeks):
        return "缺少有效教学周次"
    if not 0 <= day <= 6 or start >= end:
        return "星期或上课时间无效"
    code = str(course.get("course_code", "")).strip()
    if code and not code.isdigit():
        return "课程序号不是阿拉伯数字"
    return None


class CalendarApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("课表转 Apple Calendar")
        root.geometry("1120x760")
        root.minsize(900, 620)
        self.data = {"first_monday": previous_monday().isoformat(), "timezone": "Asia/Shanghai",
                     "include_course_code": True, "courses": []}
        self.image_path: Path | None = None
        self.current_index: int | None = None
        self.result_queue: queue.Queue = queue.Queue()
        self.photo = None
        self.busy = False

        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        top = ttk.Frame(root, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(7, weight=1)
        ttk.Label(top, text="第一周周一").grid(row=0, column=0, padx=(0, 5))
        self.monday = tk.StringVar(value=self.data["first_monday"])
        ttk.Entry(top, textvariable=self.monday, width=13).grid(row=0, column=1, padx=(0, 12))
        ttk.Button(top, text="选择截图", command=self.pick_image).grid(row=0, column=2, padx=3)
        self.scan_button = ttk.Button(top, text="识别截图", command=self.scan_image)
        self.scan_button.grid(row=0, column=3, padx=3)
        ttk.Button(top, text="打开 JSON", command=self.open_json).grid(row=0, column=4, padx=3)
        ttk.Button(top, text="保存 JSON", command=self.save_json).grid(row=0, column=5, padx=3)
        ttk.Button(top, text="导出 ICS", command=self.export).grid(row=0, column=6, padx=3)
        self.include_code = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="标题包含课程序号", variable=self.include_code).grid(row=0, column=7, padx=(12, 0))

        self.status = tk.StringVar(value="已填入今天之前最近的周一；若学期第一周更早，请修改日期。")
        ttk.Label(root, textvariable=self.status, padding=(12, 0)).grid(row=1, column=0, sticky="ew")
        body = ttk.Panedwindow(root, orient="horizontal")
        body.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=2)
        left.rowconfigure(2, weight=1)

        preview_frame = ttk.LabelFrame(left, text="截图预览")
        preview_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview = ttk.Label(preview_frame, text="尚未选择图片", anchor="center")
        self.preview.grid(row=0, column=0, sticky="nsew")
        list_header = ttk.Frame(left)
        list_header.grid(row=1, column=0, sticky="ew")
        list_header.columnconfigure(0, weight=1)
        ttk.Label(list_header, text="课程列表：选中后在右侧核对").grid(row=0, column=0, sticky="w")
        self.review_all_button = ttk.Button(list_header, text="一键核对全部", command=self.review_all)
        self.review_all_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        table_frame = ttk.Frame(left)
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("day", "time", "teacher", "title", "code", "review"), show="headings", selectmode="browse")
        for name, label, width in [("day", "星期", 52), ("time", "时间", 112),
                                   ("teacher", "教师", 90), ("title", "课程", 190),
                                   ("code", "序号", 60), ("review", "状态", 65)]:
            self.table.heading(name, text=label)
            self.table.column(name, width=width, minwidth=45)
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<<TreeviewSelect>>", self.select_course)

        right.columnconfigure(1, weight=1)
        right.rowconfigure(12, weight=1)
        fields = ["teacher", "title", "course_code", "day", "start", "end", "weeks", "location"]
        labels = ["教师", "课程名", "课程序号", "星期", "开始", "结束", "周次", "教室/地点"]
        self.vars = {field: tk.StringVar() for field in fields}
        for row, (field, label) in enumerate(zip(fields, labels)):
            ttk.Label(right, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(8, 10))
            if field == "day":
                widget = ttk.Combobox(right, textvariable=self.vars[field], values=["星期" + d for d in DAY_NAMES], state="readonly")
            else:
                widget = ttk.Entry(right, textvariable=self.vars[field])
            widget.grid(row=row, column=1, sticky="ew", pady=5, padx=(0, 8))
        ttk.Label(right, text="周次可写 1-16 或 1,3,5；异地/隔周课程可复制后分开填写。",
                  wraplength=330).grid(row=8, column=0, columnspan=2, sticky="w", padx=8)
        self.reviewed = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="已核对这门课程", variable=self.reviewed).grid(row=9, column=0, columnspan=2, sticky="w", padx=8, pady=8)
        controls = ttk.Frame(right)
        controls.grid(row=10, column=0, columnspan=2, sticky="ew", padx=8)
        for i, (label, action) in enumerate([("应用修改", self.apply_course), ("新增", self.add_course),
                                              ("复制", self.duplicate_course), ("删除", self.delete_course)]):
            ttk.Button(controls, text=label, command=action).grid(row=0, column=i, padx=2)
        ttk.Label(right, text="截图识别原文").grid(row=11, column=0, columnspan=2, sticky="w", padx=8, pady=(12, 3))
        self.raw = tk.Text(right, height=8, wrap="word", state="disabled")
        self.raw.grid(row=12, column=0, columnspan=2, sticky="nsew", padx=8, pady=(0, 8))

    def set_status(self, text: str) -> None:
        self.status.set(text)

    def pick_image(self) -> None:
        selected = filedialog.askopenfilename(filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp")])
        if selected:
            self.image_path = Path(selected)
            self.show_image()
            self.set_status(f"已选择 {self.image_path.name}")

    def show_image(self) -> None:
        if not self.image_path:
            return
        try:
            image = Image.open(self.image_path)
            image.thumbnail((680, 360))
            self.photo = ImageTk.PhotoImage(image)
            self.preview.configure(image=self.photo, text="")
        except OSError as exc:
            messagebox.showerror("图片错误", str(exc))

    def scan_image(self) -> None:
        if not self.image_path or self.busy:
            if not self.image_path:
                messagebox.showinfo("请选择截图", "先选择一张课表截图。")
            return
        if not self.commit_current():
            return
        try:
            first_monday = dt.date.fromisoformat(self.monday.get().strip())
            if first_monday.weekday() != 0:
                raise ValueError("第一周日期必须是周一")
        except ValueError as exc:
            messagebox.showerror("日期错误", str(exc))
            return
        self.busy = True
        self.scan_button.configure(state="disabled")
        self.review_all_button.configure(state="disabled")
        self.set_status("正在识别截图…")
        image_path, monday = self.image_path, self.monday.get().strip()
        def worker() -> None:
            try:
                self.result_queue.put(("ok", recognize(image_path, monday)))
            except Exception as exc:
                self.result_queue.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self.poll_scan)

    def poll_scan(self) -> None:
        try:
            kind, value = self.result_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll_scan)
            return
        self.busy = False
        self.scan_button.configure(state="normal")
        self.review_all_button.configure(state="normal")
        if kind == "error":
            messagebox.showerror("识别失败", value)
            self.set_status("识别失败；可换清晰截图或打开 JSON 手动编辑。")
        else:
            self.data = value
            self.include_code.set(include_code_choice(value))
            self.current_index = None
            self.refresh_table()
            layout = value.get("recognition", {}).get("layout", "")
            layout_name = "时间纵向" if layout == "vertical_time" else "时间横向" if layout == "horizontal_time" else "布局待核对"
            missing_weeks = sum(not course.get("weeks") for course in value["courses"])
            week_notice = f"；{missing_weeks} 门缺少教学周次，须手动补全" if missing_weeks else ""
            self.set_status(f"识别出 {len(value['courses'])} 门课程（{layout_name}）{week_notice}；逐门核对后导出。")

    def refresh_table(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        for index, course in enumerate(self.data["courses"]):
            normalize_course(course)
            day = int(course.get("day", 0))
            self.table.insert("", "end", iid=str(index), values=("周" + DAY_NAMES[day],
                f"{course.get('start', '')}-{course.get('end', '')}", course.get("teacher", ""),
                course.get("title", ""), course.get("course_code", ""),
                "待核对" if course.get("needs_review", True) else "已核对"))

    def select_course(self, _event=None) -> None:
        selected = self.table.selection()
        if not selected:
            return
        index = int(selected[0])
        if self.current_index == index:
            return
        if not self.commit_current():
            if self.current_index is not None:
                self.table.selection_set(str(self.current_index))
            return
        self.current_index = index
        course = self.data["courses"][index]
        for field in ("teacher", "title", "course_code", "start", "end", "location"):
            self.vars[field].set(str(course.get(field, "")))
        self.vars["day"].set("星期" + DAY_NAMES[int(course.get("day", 0))])
        self.vars["weeks"].set(weeks_to_text(course.get("weeks", [])))
        self.reviewed.set(not course.get("needs_review", True))
        self.raw.configure(state="normal")
        self.raw.delete("1.0", "end")
        self.raw.insert("1.0", "\n".join(course.get("raw_text", [])))
        self.raw.configure(state="disabled")

    def commit_current(self) -> bool:
        if self.current_index is None:
            return True
        try:
            day_value = self.vars["day"].get()
            day = DAY_NAMES.index(day_value[-1])
            start = dt.time.fromisoformat(self.vars["start"].get().strip())
            end = dt.time.fromisoformat(self.vars["end"].get().strip())
            if start >= end:
                raise ValueError("结束时间必须晚于开始时间")
            weeks = text_to_weeks(self.vars["weeks"].get())
            code = self.vars["course_code"].get().strip()
            if code and not code.isdigit():
                raise ValueError("课程序号必须是阿拉伯数字")
            if self.reviewed.get() and (not weeks or not self.vars["title"].get().strip()):
                raise ValueError("确认课程前请填写课程名与有效周次")
        except ValueError as exc:
            messagebox.showerror("课程信息错误", str(exc))
            return False
        course = self.data["courses"][self.current_index]
        for field in ("teacher", "title", "course_code", "start", "end", "location"):
            course[field] = self.vars[field].get().strip()
        course["day"] = day
        course["weeks"] = weeks
        course["needs_review"] = not self.reviewed.get()
        self.table.item(str(self.current_index), values=("周" + DAY_NAMES[day],
            f"{course['start']}-{course['end']}", course["teacher"], course["title"], course["course_code"],
            "待核对" if course["needs_review"] else "已核对"))
        return True

    def apply_course(self) -> None:
        if self.commit_current() and self.current_index is not None:
            self.table.selection_set(str(self.current_index))
            self.set_status("修改已应用。")

    def review_all(self) -> None:
        if self.busy or not self.commit_current():
            return
        if not self.data["courses"]:
            self.set_status("尚无课程可核对。")
            return
        approved, skipped = 0, []
        for index, course in enumerate(self.data["courses"]):
            issue = course_review_issue(course)
            if issue:
                course["needs_review"] = True
                skipped.append(f"{index + 1}. {course.get('title') or '未命名课程'}：{issue}")
            else:
                approved += bool(course.get("needs_review", True))
                course["needs_review"] = False
            self.table.set(str(index), "review", "待核对" if issue else "已核对")
        if self.current_index is not None:
            self.reviewed.set(not self.data["courses"][self.current_index]["needs_review"])
        self.set_status(f"一键核对完成：新增核对 {approved} 门，仍需修改 {len(skipped)} 门。")
        if skipped:
            details = "\n".join(skipped[:8])
            remainder = f"\n另有 {len(skipped) - 8} 门…" if len(skipped) > 8 else ""
            messagebox.showwarning("仍需核对的课程", f"已核对信息完整的课程；以下课程暂未核对：\n{details}{remainder}")

    def add_course(self) -> None:
        if not self.commit_current():
            return
        self.data["courses"].append({"teacher": "", "title": "", "course_code": "", "day": 0, "start": "08:00", "end": "08:45",
                                     "weeks": [], "location": "", "raw_text": [], "needs_review": True})
        self.current_index = None
        self.refresh_table()
        self.table.selection_set(str(len(self.data["courses"]) - 1))
        self.select_course()

    def duplicate_course(self) -> None:
        if self.current_index is None or not self.commit_current():
            return
        new = dict(self.data["courses"][self.current_index])
        new["weeks"] = list(new["weeks"])
        new["needs_review"] = True
        self.data["courses"].append(new)
        self.current_index = None
        self.refresh_table()
        self.table.selection_set(str(len(self.data["courses"]) - 1))
        self.select_course()

    def delete_course(self) -> None:
        if self.current_index is None:
            return
        del self.data["courses"][self.current_index]
        self.current_index = None
        self.refresh_table()
        for value in self.vars.values():
            value.set("")
        self.reviewed.set(False)

    def open_json(self) -> None:
        if not self.commit_current():
            return
        selected = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not selected:
            return
        try:
            data = json.loads(Path(selected).read_text(encoding="utf-8"))
            if not isinstance(data.get("courses"), list):
                raise ValueError("JSON 中缺少 courses 列表")
            self.data = data
            self.include_code.set(include_code_choice(data))
            self.monday.set(data["first_monday"])
            self.current_index = None
            self.refresh_table()
            self.set_status(f"已打开 {Path(selected).name}")
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("打开失败", str(exc))

    def save_json(self) -> None:
        if not self.commit_current():
            return
        self.data["first_monday"] = self.monday.get().strip()
        self.data["include_course_code"] = self.include_code.get()
        selected = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if selected:
            Path(selected).write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.set_status(f"已保存 {Path(selected).name}")

    def export(self) -> None:
        if not self.commit_current():
            return
        self.data["first_monday"] = self.monday.get().strip()
        self.data["include_course_code"] = self.include_code.get()
        selected = filedialog.asksaveasfilename(defaultextension=".ics", filetypes=[("Apple Calendar ICS", "*.ics")])
        if not selected:
            return
        try:
            count = export_ics(self.data, Path(selected))
            self.set_status(f"已导出 {count} 条课程日程：{Path(selected).name}")
            messagebox.showinfo("导出完成", f"已生成 {count} 条课程日程；多周课程按每周重复。\n"
                                "在 iPhone 上请从系统“邮件”App 打开 .ics 附件；"
                                "也可在 Mac“日历”中导入到 iCloud 日历。")
        except (ValueError, OSError, KeyError) as exc:
            messagebox.showerror("导出失败", str(exc))


def run_gui() -> None:
    root = tk.Tk()
    CalendarApp(root)
    root.mainloop()


def run_cli() -> int:
    parser = argparse.ArgumentParser(description="课表截图识别与 Apple Calendar ICS 导出；不带参数时打开 GUI")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="识别图片，保存可编辑 JSON")
    scan.add_argument("image", type=Path)
    scan.add_argument("--first-monday", help="第一周周一，YYYY-MM-DD；省略时使用今天之前最近的周一")
    scan.add_argument("--output", type=Path, default=Path("courses.json"))
    export = sub.add_parser("export", help="从核对后的 JSON 导出 ICS")
    export.add_argument("json_file", type=Path)
    export.add_argument("--output", type=Path, default=Path("courses.ics"))
    choice = export.add_mutually_exclusive_group()
    choice.add_argument("--with-code", action="store_true", help="日历标题包含课程序号")
    choice.add_argument("--without-code", action="store_true", help="日历标题不包含课程序号")
    args = parser.parse_args()
    try:
        if args.command == "scan":
            first_monday = args.first_monday or previous_monday().isoformat()
            first_date = dt.date.fromisoformat(first_monday)
            if first_date.weekday() != 0:
                raise ValueError("第一周日期必须是周一")
            data = recognize(args.image, first_monday)
            args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"识别 {len(data['courses'])} 门课程（{data.get('recognition', {}).get('layout', '布局未知')}），已保存 {args.output}")
            print("请核对教师、课程名、课程序号、周次、地点和时间；needs_review 为 true 的课程必须修正。")
        else:
            data = json.loads(args.json_file.read_text(encoding="utf-8"))
            override = True if args.with_code else False if args.without_code else None
            count = export_ics(data, args.output, include_course_code=override)
            print(f"已生成 {args.output}，共 {count} 条课程日程；多周课程按每周重复")
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(run_cli())
    run_gui()
