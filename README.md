# 课表截图转 Apple Calendar

输入带星期和时间标签的网格课表截图，先识别为可编辑 JSON，核对后导出 `.ics`。识别器会判断“时间横向/星期纵向”或“时间纵向/星期横向”，从数据区域估计背景颜色，再定位不同颜色的课程块。无网格、课程与背景没有明显色差、时间标签缺失的课表仍需手动填写 JSON。

## 安装与运行

```powershell
python -m pip install -r requirements.txt
python main.py
```

GUI 中选择截图并填写第一周周一，点击“识别截图”。逐门核对右侧的教师、课程名、课程序号、周次、地点及时间，勾选“已核对这门课程”后点击“应用修改”；若全部课程都无需修改，可在课程列表上方点击“一键核对全部”。批量核对只会通过信息完整的课程，缺少课程名、教学周次或有效时间的课程会保持待核对并列出原因。顶部的“标题包含课程序号”可自由勾选，课程序号会单独保存，`0408` 这类前导零不会丢。同一门课若不同周在不同地点，先“复制”，再为两条记录分别填写周次和地点。核对完后可保存 JSON 并导出 ICS。

也可以继续使用命令行：

```powershell
python main.py scan my_timetable.png --first-monday 2026-08-31 --output courses.json
```

新建课表时，GUI 会自动填入今天之前最近的周一；命令行省略 `--first-monday` 时也使用这个默认值。若真实学期第一教学周更早，请改成学期起点，例如上面的 `2026-08-31`。已有 JSON 的日期仍从文件读取。

打开 JSON 核对每门课的 `teacher`、`title`、`course_code`、`location`、`weeks`、`day`（0=周一，6=周日）、`start` 和 `end`。OCR 原文在 `raw_text` 中；`recognition` 记录识别的布局、背景颜色及候选色块数，`source_box` 记录课程块像素位置。尤其检查隔周课程和换行教室名；确认一门课程后将它的 `needs_review` 改为 `false`。若同一课程不同周在不同地点上课，拆成两条课程，给各条不同的 `weeks` 和 `location`。日历事件标题由 `title` 加可选的 `course_code` 构成，教师写在事件备注里。

```powershell
python main.py export courses.json --output courses.ics
```

命令行可加 `--with-code` 或 `--without-code` 覆盖 JSON 中的 `include_course_code` 选择。旧 JSON 若没有这个选项，会沿用原有标题样式；若课程序号只留在 `raw_text` 中，程序会自动提取到独立字段。

在 Mac 上双击 `.ics`，或在 Apple Calendar 中使用“文件 → 导入”。多周课程以每周重复的单条日程导出，重复截止时间按最后一个教学周计算；缺课周使用 `EXDATE` 排除，单周课程则不设置重复。使用 `Asia/Shanghai` 时区。再次导入同一份文件前，建议先检查目标日历，避免日历应用把重复导入显示成重复事件。

在 iPhone 上，Apple 官方说明可从“邮件”App 导入收到的 `.ics` 附件；从“文件”App 把本地文件拖入日历并非该说明中的导入步骤。如果有 Mac，也可以在 Mac“日历”中将 `.ics` 导入 **iCloud 日历**，随后在使用同一 Apple 账户的 iPhone 上查看。导入后请在日历列表确认目标日历已勾选，并跳到第一教学周的上课日期检查。新的导出文件包含与 `TZID` 对应的 `VTIMEZONE` 定义。

对于按单节格子显示课程的课表，识别器会合并同一天相邻、同名且周次/地点一致的格子。若截图没有教学周次或标题被截断，必须在 GUI 或 JSON 中补全并核对后才能导出可靠的课表。

`first_monday` 必须是第一教学周的周一，而不是开学周或截图日期。如果时间、周次或未核对课程不完整，导出会报错，不会静默生成缺课日历。

代码按职责分为 `course_data.py`（课程字段与文本解析）、`timetable_ocr.py`（截图识别）、`ics_export.py`（日历生成）；`main.py` 同时是桌面界面和命令行入口。
