import json
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont

from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from groq import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    RateLimitError,
)

from evaluate import Keyword, Rubric
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
        return "서버에 연결하지 못했습니다. 네트워크를 확인하세요."

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
        self.bind_all("<MouseWheel>", self.on_wheel, add="+")
        self.bind_all("<Button-4>", self.on_wheel, add="+")
        self.bind_all("<Button-5>", self.on_wheel, add="+")

    def update_region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def resize_body(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)

    def on_wheel(self, event):
        widget = event.widget

        if isinstance(widget, (tk.Text, ttk.Treeview, ttk.Spinbox)):
            return

        while widget is not None:
            if widget == self:
                break
            widget = getattr(widget, "master", None)
        else:
            return

        if self.body.winfo_height() <= self.canvas.winfo_height():
            return

        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        elif event.delta:
            step = -max(1, abs(event.delta) // 120)
            if event.delta < 0:
                step = abs(step)
        else:
            return

        self.canvas.yview_scroll(step, "units")


class GradingApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("AI 서답형 채점기")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        width = min(980, int(screen_width * 0.90))
        height = min(760, int(screen_height * 0.82))

        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2 - 20)

        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(720, width), min(480, height))
        self.configure(bg=BG)

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
        self.busy = False

        self.keywords = [
            {"text": "광합성", "points": 0},
            {"text": "빛에너지", "points": 2},
            {"text": "화학 에너지", "points": 2},
        ]

        self.name_var = tk.StringVar()
        self.number_var = tk.StringVar()
        self.keyword_var = tk.StringVar()
        self.keyword_points = tk.StringVar(value="2")
        self.context_points = tk.StringVar(value="6")
        self.status = tk.StringVar(value="준비 완료 · 답안을 입력해 주세요.")

        self.setup_styles()
        self.build_header()

        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        self.container.rowconfigure(0, weight=1)
        self.container.columnconfigure(0, weight=1)

        self.window1 = tk.Frame(self.container, bg=BG)
        self.window2 = tk.Frame(self.container, bg=BG)

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
            font=(self.font, 11, "bold"),
            background=GREEN,
            foreground=WHITE,
            borderwidth=0,
            padding=(20, 11),
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
            font=(self.font, 10, "bold"),
            background=MINT,
            foreground=GREEN_DARK,
            borderwidth=0,
            padding=(14, 9),
        )
        style.map(
            "Soft.TButton",
            background=[("active", "#D6EDE2")],
        )

        style.configure(
            "TEntry",
            padding=8,
            fieldbackground=WHITE,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        style.configure("TSpinbox", padding=6, arrowsize=14)

        style.configure(
            "Treeview",
            background=WHITE,
            fieldbackground=WHITE,
            foreground=TEXT,
            rowheight=38,
            borderwidth=0,
            font=(self.font, 10),
        )
        style.configure(
            "Treeview.Heading",
            background=MINT,
            foreground=GREEN_DARK,
            font=(self.font, 10, "bold"),
            padding=10,
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
        style.configure(
            "Primary.TButton",
            font=(self.font, 10, "bold"),
            padding=(14, 8),
        )
        style.configure(
            "Soft.TButton",
            font=(self.font, 9, "bold"),
            padding=(10, 6),
        )
        style.configure("TEntry", padding=5)
        style.configure("TSpinbox", padding=4)
        style.configure(
            "Treeview",
            rowheight=30,
            font=(self.font, 9),
        )
        style.configure(
            "Treeview.Heading",
            font=(self.font, 9, "bold"),
            padding=6,
        )

    def label(self, parent, text="", size=10, bold=False, color=TEXT, **kwargs):
        return tk.Label(
            parent,
            text=text,
            bg=parent.cget("bg"),
            fg=color,
            font=(self.font, size, "bold" if bold else "normal"),
            **kwargs,
        )

    def button(self, parent, text, command, primary=False, lock=True):
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

        row = tk.Frame(inner, bg=WHITE)
        row.pack(fill="x", pady=(0, 10))

        tk.Frame(row, bg=GREEN, width=4, height=20).pack(
            side="left", padx=(0, 9)
        )
        self.label(row, title, size=12, bold=True).pack(side="left")

        if hint:
            self.label(row, hint, size=9, color=MUTED).pack(side="right")

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

        icon = tk.Label(
            content,
            text="AI",
            bg=MINT,
            fg=GREEN,
            font=(self.font, 20, "bold"),
            padx=14,
            pady=10,
        )
        icon.pack(side="left", padx=(0, 16))

        titles = tk.Frame(content, bg=WHITE)
        titles.pack(side="left")

        self.label(
            titles, "AI 서답형 채점기", size=19, bold=True
        ).pack(anchor="w")
        self.label(
            titles,
            "기준은 명확하게, 피드백은 세심하게.",
            color=MUTED,
        ).pack(anchor="w", pady=(4, 0))

        self.label(
            content,
            "PROTOTYPE",
            size=9,
            bold=True,
            color=GREEN,
        ).pack(side="right")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        tk.Frame(self, bg=BG, height=16).pack(fill="x")

    def build_input_page(self):
        footer = tk.Frame(self.window1, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(8, 0))

        self.start_button = self.button(
            footer, "▶  채점 시작", self.start_grading, primary=True
        )
        self.start_button.pack(side="right")

        self.button(
            footer, "결과 보기 →", self.render_results
        ).pack(side="right", padx=10)

        self.label(
            footer,
            textvariable=self.status,
            color=MUTED,
            anchor="w",
            wraplength=420,
        ).pack(side="left", fill="x", expand=True)

        self.progress = ttk.Progressbar(
            self.window1,
            mode="indeterminate",
            style="Mint.Horizontal.TProgressbar",
        )
        self.progress.pack(side="bottom", fill="x", pady=(6, 0))

        page = ScrollPage(self.window1)
        page.pack(fill="both", expand=True)

        keywords = self.card(
            page.body,
            "키워드",
            "0점 키워드는 필수 · 누락하면 전체 0점",
        )

        row = tk.Frame(keywords, bg=WHITE)
        row.pack(fill="x")

        self.keyword_entry = ttk.Entry(
            row, textvariable=self.keyword_var
        )
        self.keyword_entry.pack(side="left", fill="x", expand=True)
        self.keyword_entry.bind("<Return>", self.add_keyword)
        self.controls.append(self.keyword_entry)

        self.label(row, "배점").pack(side="left", padx=(12, 6))

        spin = ttk.Spinbox(
            row,
            from_=0,
            to=1000,
            textvariable=self.keyword_points,
            width=5,
        )
        spin.pack(side="left")
        self.controls.append(spin)

        self.button(row, "+ 추가", self.add_keyword).pack(
            side="left", padx=(10, 0)
        )

        self.chip_frame = tk.Frame(keywords, bg=WHITE)
        self.chip_frame.pack(fill="x", pady=(10, 0))
        self.render_keywords()

        model = self.card(page.body, "모범 답안")
        self.model_text = self.text_input(model, 2)
        self.model_text.insert(
            "1.0",
            "광합성은 빛에너지를 화학 에너지로 전환하여 "
            "유기물에 저장하는 과정이다.",
        )

        row = tk.Frame(model, bg=WHITE)
        row.pack(fill="x", pady=(10, 0))
        self.label(row, "문맥 배점", bold=True).pack(side="left")

        spin = ttk.Spinbox(
            row,
            from_=0,
            to=1000,
            width=5,
            textvariable=self.context_points,
        )
        spin.pack(side="left", padx=10)
        self.controls.append(spin)

        self.label(
            row,
            "완전 일치 100% · 부분 일치 50% (소수점 버림)",
            size=9,
            color=MUTED,
        ).pack(side="left")

        answer = self.card(page.body, "학생 답안", "학생 답안을 직접 입력하세요")
        self.answer_text = self.text_input(answer, 3)

        student = self.card(page.body, "응시자 정보", "선택 사항")

        row = tk.Frame(student, bg=WHITE)
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)
        row.columnconfigure(3, weight=1)

        self.label(row, "이름").grid(row=0, column=0, padx=(0, 10))
        name = ttk.Entry(row, textvariable=self.name_var)
        name.grid(row=0, column=1, sticky="ew")

        self.label(row, "학번").grid(row=0, column=2, padx=(20, 10))
        number = ttk.Entry(row, textvariable=self.number_var)
        number.grid(row=0, column=3, sticky="ew")
        self.controls.extend([name, number])

    def render_keywords(self):
        for widget in self.chip_frame.winfo_children():
            widget.destroy()

        if not self.keywords:
            self.label(
                self.chip_frame,
                "등록된 키워드가 없어요. 문맥만으로도 채점할 수 있어요.",
                color=MUTED,
            ).pack(anchor="w")
            return

        for index, item in enumerate(self.keywords):
            chip = tk.Frame(self.chip_frame, bg=MINT)
            chip.grid(
                row=index // 3,
                column=index % 3,
                sticky="w",
                padx=(0, 8),
                pady=4,
            )

            badge = "필수" if item["points"] == 0 else f'{item["points"]}점'
            self.label(
                chip,
                f'{item["text"]}  ·  {badge}',
                color=GREEN_DARK,
                padx=10,
                pady=6,
            ).pack(side="left")

            tk.Button(
                chip,
                text="×",
                command=lambda i=index: self.remove_keyword(i),
                bg=MINT,
                fg=GREEN_DARK,
                activebackground="#D6EDE2",
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                padx=8,
            ).pack(side="left")

    def add_keyword(self, event=None):
        if self.busy:
            return

        text = self.keyword_var.get().strip()

        try:
            points = int(self.keyword_points.get())
            if not text:
                raise ValueError("키워드를 입력하세요.")
            if points < 0:
                raise ValueError("배점은 0 이상이어야 합니다.")
            if any(item["text"] == text for item in self.keywords):
                raise ValueError("이미 등록된 키워드입니다.")
        except ValueError as error:
            messagebox.showwarning("키워드 확인", str(error))
            return

        self.keywords.append({"text": text, "points": points})
        self.keyword_var.set("")
        self.render_keywords()
        self.keyword_entry.focus_set()

    def remove_keyword(self, index):
        if not self.busy:
            self.keywords.pop(index)
            self.render_keywords()

    def set_busy(self, busy):
        self.busy = busy

        for widget in self.controls:
            widget.configure(state="disabled" if busy else "normal")

        self.start_button.configure(
            text="채점 중…" if busy else "▶  채점 시작"
        )

        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def start_grading(self):
        if self.busy:
            return

        if self.keyword_var.get().strip():
            messagebox.showwarning(
                "키워드 확인",
                "작성 중인 키워드가 있어요. '+ 추가'를 누르거나 입력을 지워주세요.",
            )
            return

        try:
            model_answer = self.model_text.get("1.0", "end-1c").strip()
            context_points = int(self.context_points.get())

            if not model_answer:
                raise ValueError("모범 답안을 입력하세요.")

            if any(not item["text"].strip() for item in self.keywords):
                raise ValueError("키워드는 공백일 수 없습니다.")

            rubric = Rubric(
                keywords=[Keyword(**item) for item in self.keywords],
                model_answer=model_answer,
                context_points=context_points,
            )

        except Exception as error:
            messagebox.showerror("채점 기준 확인", str(error))
            return

        answer = self.answer_text.get("1.0", "end-1c")

        if not answer.strip():
            if not messagebox.askyesno(
                "빈 답안",
                "입력한 답안이 없습니다. 빈 답안으로 채점할까요?",
            ):
                return

        name = self.name_var.get().strip() or "이름 미입력"
        number = self.number_var.get().strip()

        self.set_busy(True)
        self.status.set(f"{name} · 답안의 문맥을 확인하고 있어요.")

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
                raise TypeError("main()은 dict 형태의 결과를 반환해야 합니다.")

            result = dict(raw)
            result.setdefault("status", "채점 완료")
            result.setdefault("max_score", maximum)

            if result["status"] == "채점 완료":
                if not isinstance(result.get("score"), (int, float)):
                    raise ValueError("채점 결과에 숫자형 score가 없습니다.")
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
                self.status.set(f'{result["name"]} · {result["status"]}')
                self.render_results()
        except queue.Empty:
            pass

        self.after(100, self.poll_events)

    def build_result_page(self):
        self.result_page = ScrollPage(self.window2)
        self.result_page.pack(fill="both", expand=True)

        frame = self.result_page.body

        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", pady=(0, 12))

        self.label(top, "채점 결과", size=19, bold=True).pack(side="left")
        self.label(
            top,
            "AI 가채점 결과는 최종 검토 후 활용하세요.",
            color=MUTED,
        ).pack(side="right")

        stats = tk.Frame(frame, bg=BG)
        stats.pack(fill="x", pady=(0, 14))

        self.stat_values = []

        for column, title in enumerate(
            ("누적 채점", "선택한 답안의 점수", "문맥 판정")
        ):
            stats.columnconfigure(column, weight=1)

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
                padx=(0, 10 if column < 2 else 0),
            )

            self.label(box, title, color=MUTED).pack(
                anchor="w", padx=18, pady=(14, 4)
            )
            value = self.label(
                box, "—", size=22, bold=True, color=GREEN
            )
            value.pack(anchor="w", padx=18, pady=(0, 14))
            self.stat_values.append(value)

        table_box = self.card(frame, "응시자별 결과")

        columns = ("name", "number", "status", "score")
        self.table = ttk.Treeview(
            table_box,
            columns=columns,
            show="headings",
            height=4,
            selectmode="browse",
        )

        for key, title in zip(
            columns, ("이름", "학번", "상태", "점수")
        ):
            self.table.heading(key, text=title)
            self.table.column(
                key, anchor="center", width=150, minwidth=90
            )

        scrollbar = ttk.Scrollbar(
            table_box, orient="vertical", command=self.table.yview
        )
        self.table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.table.pack(fill="x", expand=True)

        self.table.tag_configure("hold", foreground="#AB7540")
        self.table.bind("<<TreeviewSelect>>", self.show_selected_result)

        footer = tk.Frame(frame, bg=BG)
        footer.pack(side="bottom", fill="x", pady=(12, 0))

        self.button(
            footer,
            "← 입력 화면",
            self.window1.tkraise,
            lock=False,
        ).pack(side="left")

        self.button(
            footer,
            "다음 학생 입력",
            self.next_student,
            lock=False,
        ).pack(side="left", padx=8)

        self.button(
            footer,
            "결과 저장",
            self.save_results,
            primary=True,
            lock=False,
        ).pack(side="right")

        detail_box = tk.Frame(
            frame,
            bg=WHITE,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        detail_box.pack(fill="both", expand=True)

        self.label(
            detail_box, "답안 및 상세 피드백", size=12, bold=True
        ).pack(anchor="w", padx=18, pady=(14, 8))

        self.detail_text = ScrolledText(
            detail_box,
            height=9,
            wrap="word",
            bg=WHITE,
            fg=TEXT,
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=8,
            font=(self.font, 10),
            state="disabled",
        )
        self.detail_text.pack(fill="both", expand=True)

        self.detail_text.tag_configure(
            "section",
            foreground=GREEN_DARK,
            font=(self.font, 11, "bold"),
            spacing1=10,
            spacing3=6,
        )
        self.detail_text.tag_configure(
            "body", spacing3=6, lmargin1=2, lmargin2=2
        )

    def render_results(self):
        self.table.delete(*self.table.get_children())

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
                    "hold" if result["status"] != "채점 완료" else "done",
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

        labels = {
            "full": "완전 일치",
            "partial": "부분 일치",
            "none": "불일치",
        }
        verdict = labels.get(context.get("verdict"))

        if result["status"] != "채점 완료":
            verdict = "채점 보류"
        elif not verdict:
            verdict = "판정 미실행"

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
                f'{result["name"]}  ·  '
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
            chunks.append(("키워드 검사 결과가 없습니다.", "body"))

        for item in checks:
            included = item["present"]
            points = item["points"]
            mark = "✓" if included else "×"

            if points == 0:
                description = (
                    "필수 키워드 포함"
                    if included
                    else "필수 키워드 누락 → 전체 0점"
                )
            else:
                description = (
                    f"포함 · 항목 배점 {points}점"
                    if included
                    else f"누락 · {points}점 감점"
                )

            chunks.append((
                f'{mark}  {item["keyword"]} — {description}',
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
                "빈 답안, 필수 키워드 누락, 문맥 배점 0점 또는 "
                "오류로 문맥 판정이 수행되지 않았습니다.",
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
            self.detail_text.insert("end", str(text) + "\n", tag)

        self.detail_text.configure(state="disabled")
        self.detail_text.yview_moveto(0)

    def next_student(self):
        if self.busy:
            return

        self.name_var.set("")
        self.number_var.set("")
        self.answer_text.delete("1.0", "end")
        self.status.set("다음 학생의 이름과 답안을 입력하세요.")
        self.window1.tkraise()
        self.answer_text.focus_set()

    def save_results(self):
        if not self.results:
            messagebox.showinfo("저장 안내", "저장할 채점 결과가 없습니다.")
            return

        path = filedialog.asksaveasfilename(
            title="채점 결과 저장",
            defaultextension=".json",
            initialfile=f"채점결과_{datetime.now():%Y%m%d_%H%M%S}.json",
            filetypes=[("JSON 파일", "*.json")],
        )
        if not path:
            return

        payload = {
            "saved_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "students": self.results,
        }

        try:
            Path(path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as error:
            messagebox.showerror("저장 실패", str(error))
            return

        messagebox.showinfo("저장 완료", f"결과를 저장했습니다.\n\n{path}")

    def close_app(self):
        if self.busy:
            if not messagebox.askyesno(
                "종료 확인",
                "채점 중입니다. 종료하면 진행 중인 결과는 저장되지 않습니다. "
                "종료할까요?",
            ):
                return

        self.destroy()


if __name__ == "__main__":
    app = GradingApp()
    app.mainloop()