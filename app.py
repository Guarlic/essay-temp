import json
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont

from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from fpdf import FPDF

from groq import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    RateLimitError,
)

from evaluate import Keyword, Rubric, normalize, validate_rubric
from main import main as run_grading


BG = "#F3F7F6"
WHITE = "#FFFFFF"
GREEN = "#159875"
GREEN_DARK = "#107657"
MINT = "#E8F6EF"
TEXT = "#1C2C35"
MUTED = "#71817F"
BORDER = "#DCE7E2"


def error_message(error):
    if isinstance(error, AuthenticationError):
        return "Groq API 키를 확인하세요."

    if isinstance(error, RateLimitError):
        return "API 사용 한도를 초과했습니다. 잠시 후 다시 시도하세요."

    if isinstance(error, APIConnectionError):
        return "서버 연결에 실패했습니다. 네트워크를 확인하세요."

    if isinstance(error, APIStatusError):
        return f"Groq API 오류: HTTP {error.status_code}"

    return str(error)


class ScrollPage(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)

        self.canvas = tk.Canvas(
            self,
            bg=BG,
            highlightthickness=0,
            yscrollincrement=24,
        )

        scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=BG)
        self.item = self.canvas.create_window(
            (0, 0),
            window=self.body,
            anchor="nw",
        )

        self.body.bind("<Configure>", self.update_region)
        self.canvas.bind("<Configure>", self.resize_body)

        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(event, self.on_wheel, add="+")

    def update_region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def resize_body(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)

    def on_wheel(self, event):
        widget = event.widget

        # 스크롤 가능한 텍스트·표는 자체 스크롤을 우선합니다.
        if isinstance(widget, (tk.Text, ttk.Treeview)):
            first, last = widget.yview()
            if last - first < 0.999:
                return

        if isinstance(widget, ttk.Spinbox):
            return

        current = widget

        while current is not None:
            if current == self:
                break
            current = getattr(current, "master", None)
        else:
            return

        if self.body.winfo_height() <= self.canvas.winfo_height():
            return

        number = getattr(event, "num", None)
        delta = getattr(event, "delta", 0)

        if number == 4:
            step = -1
        elif number == 5:
            step = 1
        elif delta:
            amount = max(1, abs(int(delta)) // 120)
            step = -amount if delta > 0 else amount
        else:
            return

        self.canvas.yview_scroll(step, "units")


class GradingApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("AI 서답형 채점기")
        self.configure(bg=BG)

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        width = min(980, int(screen_width * 0.90))
        height = min(760, int(screen_height * 0.82))

        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2 - 20)

        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(680, width), min(440, height))

        families = tkfont.families()
        self.font = next(
            (
                name
                for name in (
                    "맑은 고딕",
                    "Malgun Gothic",
                    "Apple SD Gothic Neo",
                    "Noto Sans CJK KR",
                )
                if name in families
            ),
            "TkDefaultFont",
        )

        self.option_add("*Font", (self.font, 10))

        self.results = []
        self.events = queue.Queue()
        self.controls = []
        self.synonym_vars = []
        self.busy = False

        self.keywords = [
            {
                "text": "광합성",
                "points": 0,
                "allow_synonyms": False,
            },
            {
                "text": "빛에너지",
                "points": 2,
                "allow_synonyms": False,
            },
            {
                "text": "화학 에너지",
                "points": 2,
                "allow_synonyms": False,
            },
        ]

        self.name_var = tk.StringVar()
        self.number_var = tk.StringVar()
        self.keyword_var = tk.StringVar()
        self.keyword_points = tk.StringVar(value="2")
        self.context_points = tk.StringVar(value="6")
        self.status = tk.StringVar(value="준비 완료 · 답안을 입력해 주세요.")

        self.setup_styles()
        self.build_header()

        container = tk.Frame(self, bg=BG)
        container.pack(
            fill="both",
            expand=True,
            padx=16,
            pady=(12, 12),
        )
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.window1 = tk.Frame(container, bg=BG)
        self.window2 = tk.Frame(container, bg=BG)

        for frame in (self.window1, self.window2):
            frame.grid(row=0, column=0, sticky="nsew")

        self.build_input_page()
        self.build_result_page()

        self.window1.tkraise()
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.after(100, self.poll_events)

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(
            "Primary.TButton",
            font=(self.font, 10, "bold"),
            background=GREEN,
            foreground=WHITE,
            borderwidth=0,
            padding=(14, 8),
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#A8CDBE"),
                ("active", GREEN_DARK),
            ],
            foreground=[("disabled", WHITE)],
        )

        style.configure(
            "Soft.TButton",
            font=(self.font, 9, "bold"),
            background=MINT,
            foreground=GREEN_DARK,
            borderwidth=0,
            padding=(10, 7),
        )
        style.map(
            "Soft.TButton",
            background=[("active", "#D6EDE2")],
        )

        style.configure(
            "TEntry",
            padding=5,
            fieldbackground=WHITE,
            bordercolor=BORDER,
        )
        style.configure("TSpinbox", padding=4, arrowsize=12)

        style.configure(
            "Treeview",
            background=WHITE,
            fieldbackground=WHITE,
            foreground=TEXT,
            rowheight=30,
            borderwidth=0,
            font=(self.font, 9),
        )
        style.configure(
            "Treeview.Heading",
            background=MINT,
            foreground=GREEN_DARK,
            font=(self.font, 9, "bold"),
            padding=6,
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", "#D8EEE3")],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "Mint.Horizontal.TProgressbar",
            background=GREEN,
            troughcolor=MINT,
            borderwidth=0,
        )

    def label(
        self,
        parent,
        text="",
        size=10,
        bold=False,
        color=TEXT,
        **kwargs,
    ):
        return tk.Label(
            parent,
            text=text,
            bg=parent.cget("bg"),
            fg=color,
            font=(self.font, size, "bold" if bold else "normal"),
            **kwargs,
        )

    def button(
        self,
        parent,
        text,
        command,
        primary=False,
        lock=True,
    ):
        widget = ttk.Button(
            parent,
            text=text,
            command=command,
            style="Primary.TButton" if primary else "Soft.TButton",
        )

        if lock:
            self.controls.append(widget)

        return widget

    def card(self, parent, title, hint=None):
        outer = tk.Frame(
            parent,
            bg=WHITE,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        outer.pack(fill="x", pady=(0, 8))

        inner = tk.Frame(outer, bg=WHITE)
        inner.pack(fill="both", expand=True, padx=14, pady=10)

        heading = tk.Frame(inner, bg=WHITE)
        heading.pack(fill="x", pady=(0, 8))

        tk.Frame(
            heading,
            bg=GREEN,
            width=4,
            height=18,
        ).pack(side="left", padx=(0, 8))

        self.label(
            heading,
            title,
            size=11,
            bold=True,
        ).pack(side="left")

        if hint:
            self.label(
                inner,
                hint,
                size=9,
                color=MUTED,
                anchor="w",
                justify="left",
                wraplength=580,
            ).pack(fill="x", pady=(0, 8))

        return inner

    def text_input(self, parent, height):
        widget = ScrolledText(
            parent,
            height=height,
            wrap="word",
            bg="#FBFDFC",
            fg=TEXT,
            insertbackground=GREEN,
            selectbackground="#D6EDE2",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=GREEN,
            padx=9,
            pady=7,
            font=(self.font, 10),
            undo=True,
        )
        widget.pack(fill="both", expand=True)
        self.controls.append(widget)
        return widget

    def build_header(self):
        header = tk.Frame(self, bg=WHITE)
        header.pack(fill="x")

        content = tk.Frame(header, bg=WHITE)
        content.pack(fill="x", padx=22, pady=12)

        tk.Label(
            content,
            text="AI",
            bg=MINT,
            fg=GREEN,
            font=(self.font, 17, "bold"),
            padx=12,
            pady=8,
        ).pack(side="left", padx=(0, 12))

        titles = tk.Frame(content, bg=WHITE)
        titles.pack(side="left")

        self.label(
            titles,
            "AI 서답형 채점기",
            size=19,
            bold=True,
        ).pack(anchor="w")

        self.label(
            titles,
            "기준은 명확하게, 피드백은 세심하게.",
            size=9,
            color=MUTED,
        ).pack(anchor="w", pady=(3, 0))

        self.label(
            content,
            "PROTOTYPE",
            size=9,
            bold=True,
            color=GREEN,
        ).pack(side="right")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

    def build_input_page(self):
        footer = tk.Frame(self.window1, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(8, 0))

        self.start_button = self.button(
            footer,
            "▶ 채점 시작",
            self.start_grading,
            primary=True,
        )
        self.start_button.pack(side="right")

        self.button(
            footer,
            "결과 보기 →",
            self.render_results,
        ).pack(side="right", padx=8)

        self.label(
            footer,
            textvariable=self.status,
            color=MUTED,
            size=9,
            anchor="w",
            justify="left",
            wraplength=300,
        ).pack(side="left", fill="x", expand=True)

        self.progress = ttk.Progressbar(
            self.window1,
            mode="indeterminate",
            style="Mint.Horizontal.TProgressbar",
        )
        self.progress.pack(side="bottom", fill="x", pady=(5, 0))

        page = ScrollPage(self.window1)
        page.pack(fill="both", expand=True)
        self.input_page = page

        keywords = self.card(
            page.body,
            "키워드",
            "0점은 필수 키워드입니다. 각 키워드의 동의어 허용 여부를 선택하세요.",
        )

        row = tk.Frame(keywords, bg=WHITE)
        row.pack(fill="x")

        self.keyword_entry = ttk.Entry(
            row,
            textvariable=self.keyword_var,
        )
        self.keyword_entry.pack(
            side="left",
            fill="x",
            expand=True,
        )
        self.keyword_entry.bind("<Return>", self.add_keyword)
        self.controls.append(self.keyword_entry)

        self.label(row, "배점").pack(
            side="left",
            padx=(10, 6),
        )

        points = ttk.Spinbox(
            row,
            from_=0,
            to=1000,
            textvariable=self.keyword_points,
            width=5,
        )
        points.pack(side="left")
        self.controls.append(points)

        self.button(
            row,
            "+ 추가",
            self.add_keyword,
        ).pack(side="left", padx=(8, 0))

        self.chip_frame = tk.Frame(keywords, bg=WHITE)
        self.chip_frame.pack(fill="x", pady=(8, 0))
        self.render_keywords()

        model = self.card(page.body, "모범 답안")
        self.model_text = self.text_input(model, 3)
        self.model_text.insert(
            "1.0",
            "광합성은 빛에너지를 화학 에너지로 전환하여 "
            "유기물에 저장하는 과정이다.",
        )

        row = tk.Frame(model, bg=WHITE)
        row.pack(fill="x", pady=(8, 0))

        self.label(
            row,
            "문맥 배점",
            bold=True,
        ).pack(side="left")

        points = ttk.Spinbox(
            row,
            from_=0,
            to=1000,
            textvariable=self.context_points,
            width=5,
        )
        points.pack(side="left", padx=8)
        self.controls.append(points)

        self.label(
            row,
            "완전 일치 100% · 부분 일치 50% (소수점 버림)",
            size=9,
            color=MUTED,
        ).pack(side="left")

        answer = self.card(page.body, "학생 답안")
        self.answer_text = self.text_input(answer, 4)

        student = self.card(
            page.body,
            "응시자 정보",
            "선택 사항 · 이름과 학번은 AI에 전송하지 않습니다.",
        )

        row = tk.Frame(student, bg=WHITE)
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)
        row.columnconfigure(3, weight=1)

        self.label(row, "이름").grid(
            row=0,
            column=0,
            padx=(0, 8),
        )

        name = ttk.Entry(
            row,
            textvariable=self.name_var,
            width=16,
        )
        name.grid(row=0, column=1, sticky="ew")

        self.label(row, "학번").grid(
            row=0,
            column=2,
            padx=(16, 8),
        )

        number = ttk.Entry(
            row,
            textvariable=self.number_var,
            width=16,
        )
        number.grid(row=0, column=3, sticky="ew")

        self.controls.extend([name, number])

    def render_keywords(self):
        for widget in self.chip_frame.winfo_children():
            widget.destroy()

        self.synonym_vars = []

        if not self.keywords:
            self.label(
                self.chip_frame,
                "등록된 키워드가 없습니다. 문맥만으로 채점합니다.",
                size=9,
                color=MUTED,
            ).pack(anchor="w")
            return

        for index, item in enumerate(self.keywords):
            row = tk.Frame(self.chip_frame, bg=MINT)
            row.pack(fill="x", pady=3)
            row.columnconfigure(0, weight=1)

            badge = (
                "필수"
                if item["points"] == 0
                else f'{item["points"]}점'
            )

            self.label(
                row,
                f'{item["text"]} · {badge}',
                color=GREEN_DARK,
                anchor="w",
                justify="left",
                wraplength=320,
                padx=9,
                pady=7,
            ).grid(row=0, column=0, sticky="ew")

            variable = tk.BooleanVar(
                value=item.get("allow_synonyms", False)
            )
            self.synonym_vars.append(variable)

            checkbox = tk.Checkbutton(
                row,
                text="문맥상 동의어 허용",
                variable=variable,
                command=lambda i=index, v=variable: (
                    self.change_synonym_option(i, v)
                ),
                bg=MINT,
                fg=GREEN_DARK,
                activebackground=MINT,
                activeforeground=GREEN_DARK,
                selectcolor=WHITE,
                highlightthickness=0,
                borderwidth=0,
                font=(self.font, 9),
                cursor="hand2",
                state="disabled" if self.busy else "normal",
            )
            checkbox.grid(row=0, column=1, padx=6)

            delete = tk.Button(
                row,
                text="×",
                command=lambda i=index: self.remove_keyword(i),
                bg=MINT,
                fg=GREEN_DARK,
                activebackground="#D6EDE2",
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                padx=9,
                state="disabled" if self.busy else "normal",
            )
            delete.grid(row=0, column=2)

    def change_synonym_option(self, index, variable):
        if self.busy:
            variable.set(
                self.keywords[index].get("allow_synonyms", False)
            )
            return

        self.keywords[index]["allow_synonyms"] = variable.get()

    def add_keyword(self, event=None):
        if self.busy:
            return

        text = self.keyword_var.get().strip()

        try:
            if not text:
                raise ValueError("키워드를 입력하세요.")

            points = int(self.keyword_points.get())

            if points < 0:
                raise ValueError("배점은 0 이상이어야 합니다.")

            if any(
                normalize(item["text"]) == normalize(text)
                for item in self.keywords
            ):
                raise ValueError("이미 등록된 키워드입니다.")

        except ValueError as error:
            messagebox.showwarning("키워드 확인", str(error))
            return

        self.keywords.append({
            "text": text,
            "points": points,
            "allow_synonyms": False,
        })

        self.keyword_var.set("")
        self.render_keywords()
        self.keyword_entry.focus_set()

    def remove_keyword(self, index):
        if self.busy:
            return

        self.keywords.pop(index)
        self.render_keywords()

    def set_busy(self, busy):
        self.busy = busy

        for widget in self.controls:
            widget.configure(
                state="disabled" if busy else "normal"
            )

        self.start_button.configure(
            text="채점 중…" if busy else "▶ 채점 시작"
        )

        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

        self.render_keywords()

    def start_grading(self):
        if self.busy:
            return

        if self.keyword_var.get().strip():
            messagebox.showwarning(
                "키워드 확인",
                "작성 중인 키워드가 있습니다. "
                "'+ 추가'를 누르거나 입력 내용을 지워주세요.",
            )
            return

        try:
            rubric = Rubric(
                keywords=[
                    Keyword(**item)
                    for item in self.keywords
                ],
                model_answer=self.model_text.get(
                    "1.0", "end-1c"
                ).strip(),
                context_points=int(self.context_points.get()),
            )
            validate_rubric(rubric)

        except Exception as error:
            messagebox.showerror(
                "채점 기준 확인",
                str(error),
            )
            return

        answer = self.answer_text.get("1.0", "end-1c")

        if not answer.strip():
            if not messagebox.askyesno(
                "빈 답안",
                "빈 답안으로 채점할까요?",
            ):
                return

        name = self.name_var.get().strip() or "이름 미입력"
        number = self.number_var.get().strip()

        self.set_busy(True)
        self.status.set(f"{name} · 답안을 확인하고 있어요.")

        threading.Thread(
            target=self.grade_worker,
            args=(name, number, answer, rubric),
            daemon=True,
        ).start()

    def grade_worker(self, name, number, answer, rubric):
        maximum = (
            sum(item.points for item in rubric.keywords)
            + rubric.context_points
        )

        try:
            raw = run_grading(answer, rubric)

            if not isinstance(raw, dict):
                raise TypeError(
                    "main()은 dict 형태의 결과를 반환해야 합니다."
                )

            result = dict(raw)
            result.setdefault("status", "채점 완료")
            result.setdefault("max_score", maximum)

            if result["status"] == "채점 완료":
                if not isinstance(result.get("score"), (int, float)):
                    raise ValueError(
                        "채점 결과에 숫자형 score가 없습니다."
                    )
            else:
                result["score"] = None

        except Exception as error:
            result = {
                "status": "채점 보류",
                "score": None,
                "max_score": maximum,
                "error": error_message(error),
                "reason": error_message(error),
            }

        result.update({
            "name": name,
            "student_number": number,
            "answer": answer,
            "rubric": rubric.model_dump(mode="json"),
            "graded_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        })

        self.events.put(result)

    def poll_events(self):
        try:
            while True:
                result = self.events.get_nowait()
                self.results.append(result)

                self.set_busy(False)
                self.status.set(
                    f'{result["name"]} · {result["status"]}'
                )
                self.render_results()

        except queue.Empty:
            pass

        self.after(100, self.poll_events)

    def build_result_page(self):
        footer = tk.Frame(self.window2, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(8, 0))

        self.button(
            footer,
            "← 입력 화면",
            self.window1.tkraise,
        ).pack(side="left")

        self.button(
            footer,
            "다음 학생",
            self.next_student,
        ).pack(side="left", padx=8)

        self.button(
            footer,
            "결과 저장",
            self.save_results,
            primary=True,
            lock=False,
        ).pack(side="right")

        self.result_page = ScrollPage(self.window2)
        self.result_page.pack(fill="both", expand=True)
        frame = self.result_page.body

        self.label(
            frame,
            "채점 결과",
            size=17,
            bold=True,
        ).pack(anchor="w", pady=(0, 4))

        self.label(
            frame,
            "AI 가채점 결과는 최종 검토 후 활용하세요.",
            size=9,
            color=MUTED,
        ).pack(anchor="w", pady=(0, 12))

        stats = tk.Frame(frame, bg=BG)
        stats.pack(fill="x", pady=(0, 10))
        self.stat_values = []

        for column, title in enumerate(
            ("누적 채점", "선택 답안 점수", "문맥 판정")
        ):
            stats.columnconfigure(column, weight=1, uniform="stats")

            box = tk.Frame(
                stats,
                bg=WHITE,
                highlightthickness=1,
                highlightbackground=BORDER,
            )
            box.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0, 8 if column < 2 else 0),
            )

            self.label(
                box,
                title,
                size=9,
                color=MUTED,
            ).pack(anchor="w", padx=12, pady=(10, 4))

            value = self.label(
                box,
                "—",
                size=18,
                bold=True,
                color=GREEN,
            )
            value.pack(anchor="w", padx=12, pady=(0, 10))
            self.stat_values.append(value)

        table_box = self.card(frame, "응시자별 결과")

        columns = ("name", "number", "status", "score")
        self.table = ttk.Treeview(
            table_box,
            columns=columns,
            show="headings",
            height=5,
            selectmode="browse",
        )

        for key, title in zip(
            columns,
            ("이름", "학번", "상태", "점수"),
        ):
            self.table.heading(key, text=title)
            self.table.column(
                key,
                anchor="center",
                width=130,
                minwidth=70,
            )

        scrollbar = ttk.Scrollbar(
            table_box,
            orient="vertical",
            command=self.table.yview,
        )
        self.table.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.table.pack(fill="x", expand=True)

        self.table.tag_configure(
            "hold",
            foreground="#AB7540",
        )
        self.table.bind(
            "<<TreeviewSelect>>",
            self.show_selected_result,
        )

        detail_box = self.card(frame, "답안 및 상세 피드백")

        self.detail_text = ScrolledText(
            detail_box,
            height=14,
            wrap="word",
            bg=WHITE,
            fg=TEXT,
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=6,
            font=(self.font, 10),
            state="disabled",
        )
        self.detail_text.pack(fill="both", expand=True)

        self.detail_text.tag_configure(
            "section",
            foreground=GREEN_DARK,
            font=(self.font, 10, "bold"),
            spacing1=10,
            spacing3=5,
        )
        self.detail_text.tag_configure(
            "body",
            spacing3=6,
            lmargin1=2,
            lmargin2=2,
        )

    def render_results(self):
        children = self.table.get_children()
        if children:
            self.table.delete(*children)

        completed = sum(
            item["status"] == "채점 완료"
            for item in self.results
        )

        self.stat_values[0].configure(
            text=f"{completed} / {len(self.results)}건"
        )

        for index, result in enumerate(self.results):
            score = result.get("score")
            score_text = (
                f'{score} / {result["max_score"]}'
                if score is not None
                else "미확정"
            )

            self.table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    result["name"],
                    result["student_number"] or "—",
                    result["status"],
                    score_text,
                ),
                tags=(
                    "hold"
                    if result["status"] != "채점 완료"
                    else "done",
                ),
            )

        self.window2.tkraise()

        if self.results:
            last = str(len(self.results) - 1)
            self.table.selection_set(last)
            self.table.focus(last)
            self.table.see(last)
            self.show_selected_result()
        else:
            self.stat_values[1].configure(text="—")
            self.stat_values[2].configure(text="—")
            self.write_detail([
                ("안내", "section"),
                ("아직 채점 결과가 없습니다.", "body"),
            ])

    def show_selected_result(self, event=None):
        selection = self.table.selection()
        if not selection:
            return

        result = self.results[int(selection[0])]
        rubric = result["rubric"]
        score = result.get("score")
        context = result.get("context") or {}

        verdict_labels = {
            "full": "완전 일치",
            "partial": "부분 일치",
            "none": "불일치",
        }

        verdict = verdict_labels.get(
            context.get("verdict"),
            "판정 미실행",
        )

        if result["status"] != "채점 완료":
            verdict = "채점 보류"

        self.stat_values[1].configure(
            text=(
                f'{score} / {result["max_score"]}'
                if score is not None
                else "미확정"
            )
        )
        self.stat_values[2].configure(text=verdict)

        chunks = [
            (
                f'{result["name"]} · '
                f'{result["student_number"] or "학번 미입력"}',
                "section",
            ),
            (result["status"], "body"),
            ("학생 답안", "section"),
            (result["answer"] or "(빈 답안)", "body"),
            ("키워드 검사", "section"),
        ]

        checks = result.get("keywords", [])

        if not checks:
            chunks.append((
                "키워드가 없거나 검사 결과를 받지 못했습니다.",
                "body",
            ))

        for item in checks:
            included = item["present"]
            points = item["points"]
            match_type = item.get("match_type", "missing")
            allowed = item.get("allow_synonyms", False)

            if match_type == "not_evaluated":
                description = "동의어 검사 생략"
                mark = "–"
            elif included:
                mark = "✓"
                match_label = (
                    "문맥상 동의어 인정"
                    if match_type == "synonym"
                    else "키워드 포함"
                )
                description = (
                    f"필수 충족 · {match_label}"
                    if points == 0
                    else f"{match_label} · 항목 배점 {points}점"
                )
            else:
                mark = "×"
                description = (
                    "필수 미충족 → 전체 0점"
                    if points == 0
                    else f"미충족 · {points}점 감점"
                )

            chunks.append((
                f'{mark} {item["keyword"]} — {description}',
                "body",
            ))
            chunks.append((
                "    동의어 허용: " + ("켜짐" if allowed else "꺼짐"),
                "body",
            ))

            if match_type == "synonym":
                chunks.append((
                    f'    인정 표현: “{item["evidence"]}”',
                    "body",
                ))

            if allowed and match_type != "exact":
                chunks.append((
                    f'    근거: {item.get("reason", "")}',
                    "body",
                ))

        chunks.append(("문맥 판정", "section"))

        if context:
            chunks.extend([
                (
                    f'{verdict} · '
                    f'{result.get("context_score", 0)} / '
                    f'{rubric["context_points"]}점',
                    "body",
                ),
                (context.get("reason", ""), "body"),
            ])
        else:
            chunks.append((
                "문맥 판정을 수행하지 않았거나, "
                "전체 0점 조건에 따라 문맥 점수를 반영하지 않았습니다.",
                "body",
            ))

        reason = result.get("reason") or result.get("error")
        if reason:
            chunks.extend([
                ("전체 채점 사유", "section"),
                (reason, "body"),
            ])

        chunks.extend([
            ("모범 답안", "section"),
            (rubric["model_answer"], "body"),
        ])

        self.write_detail(chunks)

    def write_detail(self, chunks):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")

        for text, tag in chunks:
            self.detail_text.insert(
                "end",
                str(text) + "\n",
                tag,
            )

        self.detail_text.configure(state="disabled")
        self.detail_text.yview_moveto(0)

    def next_student(self):
        if self.busy:
            return

        self.name_var.set("")
        self.number_var.set("")
        self.answer_text.delete("1.0", "end")
        self.status.set("다음 학생의 답안을 입력하세요.")

        self.window1.tkraise()
        self.answer_text.focus_set()

    def save_results(self):
        if not self.results:
            messagebox.showinfo(
                "저장 안내",
                "저장할 채점 결과가 없습니다.",
            )
            return

        path = filedialog.asksaveasfilename(
            title="채점 결과 PDF 저장",
            defaultextension=".pdf",
            initialfile=(
                f"채점결과_{datetime.now():%Y%m%d_%H%M%S}.pdf"
            ),
            filetypes=[("PDF 파일", "*.pdf")],
        )

        if not path:
            return

        base_dir = Path(__file__).resolve().parent

        font_candidates = [
            base_dir / "fonts" / "NanumGothic.ttf",
            base_dir / "fonts" / "NotoSansKR-Regular.ttf",
            Path("C:/Windows/Fonts/malgun.ttf"),
        ]

        font_path = next(
            (item for item in font_candidates if item.is_file()),
            None,
        )

        if font_path is None:
            selected = filedialog.askopenfilename(
                title="한글을 지원하는 TTF 또는 OTF 폰트 선택",
                filetypes=[
                    ("폰트 파일", "*.ttf *.otf"),
                    ("모든 파일", "*.*"),
                ],
            )

            if not selected:
                messagebox.showwarning(
                    "저장 취소",
                    "한글 PDF를 만들려면 한글 폰트가 필요합니다.",
                )
                return

            font_path = Path(selected)

        try:
            pdf = FPDF(format="A4")
            pdf.set_margins(18, 18, 18)
            pdf.set_auto_page_break(auto=True, margin=18)

            pdf.add_font("Korean", fname=str(font_path))
            pdf.set_title("서답형 답안 채점 결과")

            def write(text, size=10, color=(28, 44, 53)):
                pdf.set_font("Korean", size=size)
                pdf.set_text_color(*color)
                pdf.multi_cell(
                    w=0,
                    h=6,
                    text=str(text).replace("\t", "    "),
                    new_x="LMARGIN",
                    new_y="NEXT",
                    wrapmode="CHAR",
                )

            def section(title):
                pdf.ln(4)
                pdf.set_fill_color(232, 246, 239)
                pdf.set_text_color(16, 118, 87)
                pdf.set_font("Korean", size=11)
                pdf.multi_cell(
                    w=0,
                    h=8,
                    text=title,
                    fill=True,
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                pdf.ln(2)

            verdict_labels = {
                "full": "완전 일치",
                "partial": "부분 일치",
                "none": "불일치",
            }

            saved_at = datetime.now().astimezone().strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )

            for index, result in enumerate(self.results, start=1):
                pdf.add_page()

                rubric = result.get("rubric", {})
                context = result.get("context") or {}
                score = result.get("score")

                write(
                    "AI 서답형 채점 결과",
                    size=20,
                    color=(21, 152, 117),
                )
                write(
                    f"{index} / {len(self.results)}번째 결과",
                    size=9,
                    color=(113, 129, 127),
                )

                section("응시자 및 점수")

                write(f"이름: {result.get('name') or '미입력'}")
                write(
                    f"학번: "
                    f"{result.get('student_number') or '미입력'}"
                )
                write(f"상태: {result.get('status', '확인 필요')}")

                if score is None:
                    write("총점: 채점 보류 — 점수 미확정")
                else:
                    write(
                        f"총점: {score} / "
                        f"{result.get('max_score', '-')}점",
                        size=14,
                        color=(21, 152, 117),
                    )

                write(
                    f"채점 시각: {result.get('graded_at', '-')}",
                    size=9,
                )
                write(f"PDF 저장 시각: {saved_at}", size=9)

                section("학생 답안")
                write(result.get("answer") or "(빈 답안)")

                section("키워드 검사")

                checks = result.get("keywords", [])

                if not checks:
                    write("등록된 키워드 또는 검사 결과가 없습니다.")

                for item in checks:
                    points = item.get("points", 0)
                    present = item.get("present", False)
                    match_type = item.get("match_type", "missing")

                    if match_type == "not_evaluated":
                        outcome = "검사 생략"
                    elif match_type == "synonym":
                        outcome = "문맥상 동의어 인정"
                    elif present:
                        outcome = "키워드 포함"
                    else:
                        outcome = "미충족"

                    allocation = (
                        "필수 키워드"
                        if points == 0
                        else f"배점 {points}점"
                    )

                    write(
                        f"[{outcome}] {item.get('keyword', '')}"
                        f" / {allocation}"
                    )

                    allowed = item.get("allow_synonyms", False)
                    write(
                        "동의어 허용: "
                        + ("켜짐" if allowed else "꺼짐"),
                        size=9,
                    )

                    if match_type == "synonym":
                        write(
                            f"인정 표현: {item.get('evidence', '')}"
                        )

                    if item.get("reason"):
                        write(
                            f"근거: {item['reason']}",
                            size=9,
                        )

                    pdf.ln(2)

                section("문맥 판정")

                if context:
                    verdict = verdict_labels.get(
                        context.get("verdict"),
                        "확인 필요",
                    )

                    write(f"판정: {verdict}")
                    write(
                        f"문맥 점수: "
                        f"{result.get('context_score', 0)} / "
                        f"{rubric.get('context_points', '-')}점"
                    )
                    write(f"근거: {context.get('reason', '')}")
                else:
                    write(
                        "문맥 판정 미실행 또는 "
                        "전체 0점 조건에 따라 문맥 점수 미반영"
                    )

                reason = result.get("reason") or result.get("error")

                if reason:
                    section("전체 채점 사유")
                    write(reason)

                section("모범 답안")
                write(rubric.get("model_answer") or "(없음)")

                pdf.ln(5)
                write(
                    "이 문서는 AI 가채점 결과입니다. "
                    "최종 점수는 채점자가 검토해 주세요.",
                    size=9,
                    color=(113, 129, 127),
                )

            pdf.output(str(path))

        except Exception as error:
            messagebox.showerror(
                "PDF 저장 실패",
                str(error),
            )
            return

        messagebox.showinfo(
            "저장 완료",
            f"총 {len(self.results)}건의 결과를 PDF로 저장했습니다."
            f"\n\n{path}",
        )

    def close_app(self):
        if self.busy:
            if not messagebox.askyesno(
                "종료 확인",
                "채점 중입니다. 진행 중인 결과는 저장되지 않습니다. "
                "종료할까요?",
            ):
                return
        elif self.results:
            if not messagebox.askyesno(
                "종료 확인",
                "저장하지 않은 결과는 사라집니다. 종료할까요?",
            ):
                return

        self.destroy()


if __name__ == "__main__":
    app = GradingApp()
    app.mainloop()