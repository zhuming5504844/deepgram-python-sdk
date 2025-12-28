import json
import os
import threading
import textwrap
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, scrolledtext

from dotenv import load_dotenv

load_dotenv()

from deepgram import DeepgramClient


@dataclass
class TranscriptionOptions:
    api_key: str
    model: str
    language: str
    detect_language: bool
    punctuate: bool
    smart_format: bool
    utterances: bool
    diarize: bool
    numerals: bool
    filler_words: bool
    profanity_filter: bool
    paragraphs: bool
    keywords: str
    search: str
    replace: str
    tag: str
    redact: str
    summarize: str
    extra_json: str
    line_width: int


class DeepgramSubtitleGUI:
    def __init__(self, root: tk.Tk, dnd_available: bool = False) -> None:
        self.root = root
        self.dnd_available = dnd_available
        self.root.title("Deepgram 字幕转录 (拖放音频)")
        self.root.geometry("900x720")

        self.audio_path = tk.StringVar()
        self.status_text = tk.StringVar(value="准备就绪")

        self._build_ui()
        if self.dnd_available:
            self._setup_drag_and_drop()
        else:
            self.log("拖放功能需要 tkinterdnd2 (可选依赖)，当前将使用选择文件按钮。")

    def _build_ui(self) -> None:
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=12, pady=8)

        tk.Label(top_frame, text="音频文件").pack(anchor="w")
        file_row = tk.Frame(top_frame)
        file_row.pack(fill=tk.X, pady=4)

        self.file_entry = tk.Entry(file_row, textvariable=self.audio_path)
        self.file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Button(file_row, text="选择文件", command=self.select_file).pack(side=tk.LEFT, padx=6)
        tk.Button(file_row, text="开始转录", command=self.start_transcription).pack(side=tk.LEFT)

        options_frame = tk.LabelFrame(self.root, text="转录参数 (全参数)")
        options_frame.pack(fill=tk.BOTH, padx=12, pady=8, expand=True)

        self.api_key_var = tk.StringVar(value=os.getenv("DEEPGRAM_API_KEY", ""))
        self.model_var = tk.StringVar(value="nova-2")
        self.language_var = tk.StringVar(value="")
        self.detect_language_var = tk.BooleanVar(value=True)
        self.punctuate_var = tk.BooleanVar(value=True)
        self.smart_format_var = tk.BooleanVar(value=True)
        self.utterances_var = tk.BooleanVar(value=True)
        self.diarize_var = tk.BooleanVar(value=False)
        self.numerals_var = tk.BooleanVar(value=True)
        self.filler_words_var = tk.BooleanVar(value=False)
        self.profanity_filter_var = tk.BooleanVar(value=False)
        self.paragraphs_var = tk.BooleanVar(value=False)
        self.keywords_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")
        self.replace_var = tk.StringVar(value="")
        self.tag_var = tk.StringVar(value="")
        self.redact_var = tk.StringVar(value="")
        self.summarize_var = tk.StringVar(value="")
        self.extra_json_var = tk.StringVar(value="{}")
        self.line_width_var = tk.IntVar(value=42)

        row = 0
        row = self._add_labeled_entry(options_frame, row, "API Key", self.api_key_var)
        row = self._add_labeled_entry(options_frame, row, "模型(model)", self.model_var)
        row = self._add_labeled_entry(options_frame, row, "语言(language, 可留空)", self.language_var)

        checkbox_frame = tk.Frame(options_frame)
        checkbox_frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        row += 1

        self._add_checkbox(checkbox_frame, "自动识别语言", self.detect_language_var)
        self._add_checkbox(checkbox_frame, "标点", self.punctuate_var)
        self._add_checkbox(checkbox_frame, "智能格式化", self.smart_format_var)
        self._add_checkbox(checkbox_frame, "按话语分段(utterances)", self.utterances_var)
        self._add_checkbox(checkbox_frame, "说话人区分", self.diarize_var)
        self._add_checkbox(checkbox_frame, "数字转写", self.numerals_var)
        self._add_checkbox(checkbox_frame, "填充词(filler)", self.filler_words_var)
        self._add_checkbox(checkbox_frame, "敏感词过滤", self.profanity_filter_var)
        self._add_checkbox(checkbox_frame, "段落(paragraphs)", self.paragraphs_var)

        row = self._add_labeled_entry(options_frame, row, "关键词(keywords, 逗号分隔)", self.keywords_var)
        row = self._add_labeled_entry(options_frame, row, "搜索(search, 逗号分隔)", self.search_var)
        row = self._add_labeled_entry(options_frame, row, "替换(replace, 逗号分隔)", self.replace_var)
        row = self._add_labeled_entry(options_frame, row, "标签(tag)", self.tag_var)
        row = self._add_labeled_entry(options_frame, row, "敏感替换(redact)", self.redact_var)
        row = self._add_labeled_entry(options_frame, row, "摘要(summarize)", self.summarize_var)

        tk.Label(options_frame, text="额外参数(JSON, 覆盖上方设置)").grid(
            row=row, column=0, sticky="w", padx=4, pady=(6, 2)
        )
        row += 1
        extra_entry = tk.Entry(options_frame, textvariable=self.extra_json_var)
        extra_entry.grid(row=row, column=0, columnspan=2, sticky="ew", padx=4)
        row += 1

        tk.Label(options_frame, text="字幕行宽(字符数)").grid(
            row=row, column=0, sticky="w", padx=4, pady=(6, 2)
        )
        line_width_entry = tk.Entry(options_frame, textvariable=self.line_width_var)
        line_width_entry.grid(row=row, column=1, sticky="ew", padx=4)
        row += 1

        options_frame.columnconfigure(1, weight=1)

        output_frame = tk.LabelFrame(self.root, text="日志")
        output_frame.pack(fill=tk.BOTH, padx=12, pady=8, expand=True)

        self.log_output = scrolledtext.ScrolledText(output_frame, height=10)
        self.log_output.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        status_bar = tk.Label(self.root, textvariable=self.status_text, anchor="w")
        status_bar.pack(fill=tk.X, padx=12, pady=(0, 8))

    def _add_labeled_entry(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar) -> int:
        tk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        entry = tk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=4, pady=2)
        return row + 1

    def _add_checkbox(self, parent: tk.Widget, label: str, variable: tk.BooleanVar) -> None:
        tk.Checkbutton(parent, text=label, variable=variable).pack(side=tk.LEFT, padx=6)

    def _setup_drag_and_drop(self) -> None:
        from tkinterdnd2 import DND_FILES

        self.file_entry.drop_target_register(DND_FILES)
        self.file_entry.dnd_bind("<<Drop>>", self.handle_drop)

    def select_file(self) -> None:
        filetypes = (
            ("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.mp4"),
            ("All files", "*.*"),
        )
        filename = filedialog.askopenfilename(title="选择音频文件", filetypes=filetypes)
        if filename:
            self.audio_path.set(filename)
            self.log(f"已选择文件: {filename}")

    def handle_drop(self, event: tk.Event) -> None:
        path = event.data.strip("{}")
        if path:
            self.audio_path.set(path)
            self.log(f"已拖放文件: {path}")

    def start_transcription(self) -> None:
        path = self.audio_path.get().strip()
        if not path:
            messagebox.showwarning("提示", "请先选择或拖放音频文件。")
            return
        if not os.path.exists(path):
            messagebox.showerror("错误", f"文件不存在: {path}")
            return

        options = TranscriptionOptions(
            api_key=self.api_key_var.get().strip(),
            model=self.model_var.get().strip(),
            language=self.language_var.get().strip(),
            detect_language=self.detect_language_var.get(),
            punctuate=self.punctuate_var.get(),
            smart_format=self.smart_format_var.get(),
            utterances=self.utterances_var.get(),
            diarize=self.diarize_var.get(),
            numerals=self.numerals_var.get(),
            filler_words=self.filler_words_var.get(),
            profanity_filter=self.profanity_filter_var.get(),
            paragraphs=self.paragraphs_var.get(),
            keywords=self.keywords_var.get().strip(),
            search=self.search_var.get().strip(),
            replace=self.replace_var.get().strip(),
            tag=self.tag_var.get().strip(),
            redact=self.redact_var.get().strip(),
            summarize=self.summarize_var.get().strip(),
            extra_json=self.extra_json_var.get().strip() or "{}",
            line_width=int(self.line_width_var.get()),
        )

        self.status_text.set("转录中，请稍候...")
        self.log("开始转录，请等待...")

        threading.Thread(
            target=self.transcribe_file,
            args=(path, options),
            daemon=True,
        ).start()

    def transcribe_file(self, path: str, options: TranscriptionOptions) -> None:
        try:
            client = DeepgramClient(options.api_key or None)
            with open(path, "rb") as audio_file:
                audio_data = audio_file.read()

            params = self._build_params(options)
            self.log(f"请求参数: {json.dumps(params, ensure_ascii=False)}")

            response = client.listen.v1.media.transcribe_file(request=audio_data, **params)
            srt_text = self._build_srt(response, options.line_width)

            output_path = os.path.splitext(path)[0] + ".srt"
            with open(output_path, "w", encoding="utf-8") as srt_file:
                srt_file.write(srt_text)

            self.status_text.set("完成")
            self.log(f"转录完成，已保存字幕: {output_path}")
        except Exception as exc:
            self.status_text.set("转录失败")
            self.log(f"转录失败: {exc}")

    def _build_params(self, options: TranscriptionOptions) -> dict:
        params = {
            "model": options.model or None,
            "language": options.language or None,
            "detect_language": options.detect_language,
            "punctuate": options.punctuate,
            "smart_format": options.smart_format,
            "utterances": options.utterances,
            "diarize": options.diarize,
            "numerals": options.numerals,
            "filler_words": options.filler_words,
            "profanity_filter": options.profanity_filter,
            "paragraphs": options.paragraphs,
        }

        if options.keywords:
            params["keywords"] = [item.strip() for item in options.keywords.split(",") if item.strip()]
        if options.search:
            params["search"] = [item.strip() for item in options.search.split(",") if item.strip()]
        if options.replace:
            params["replace"] = [item.strip() for item in options.replace.split(",") if item.strip()]
        if options.tag:
            params["tag"] = [item.strip() for item in options.tag.split(",") if item.strip()]
        if options.redact:
            params["redact"] = options.redact
        if options.summarize:
            params["summarize"] = options.summarize

        params = {key: value for key, value in params.items() if value is not None}

        extra_params = self._parse_extra_json(options.extra_json)
        params.update(extra_params)
        return params

    def _parse_extra_json(self, raw: str) -> dict:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("额外参数必须是 JSON 对象")
            return data
        except json.JSONDecodeError as exc:
            raise ValueError(f"额外参数 JSON 解析失败: {exc}") from exc

    def _build_srt(self, response: dict, line_width: int) -> str:
        results = response.get("results", {})
        utterances = results.get("utterances")
        if utterances:
            return self._srt_from_utterances(utterances, line_width)

        channels = results.get("channels", [])
        if channels:
            alternatives = channels[0].get("alternatives", [])
            if alternatives:
                words = alternatives[0].get("words", [])
                return self._srt_from_words(words, line_width)

        transcript = results.get("transcripts") or []
        if transcript:
            return self._srt_from_text(transcript[0].get("transcript", ""), line_width)

        return ""

    def _srt_from_utterances(self, utterances: list, line_width: int) -> str:
        lines = []
        for index, utterance in enumerate(utterances, start=1):
            start = utterance.get("start", 0)
            end = utterance.get("end", 0)
            transcript = utterance.get("transcript", "").strip()
            transcript = textwrap.fill(transcript, width=line_width)
            lines.append(
                f"{index}\n{self._format_timestamp(start)} --> {self._format_timestamp(end)}\n{transcript}\n"
            )
        return "\n".join(lines)

    def _srt_from_words(self, words: list, line_width: int) -> str:
        if not words:
            return ""
        segments = []
        current = {"start": words[0]["start"], "end": words[0]["end"], "text": words[0]["word"]}
        for word in words[1:]:
            text = current["text"]
            if word["start"] - current["end"] > 1.2 or len(text) > line_width:
                segments.append(current)
                current = {"start": word["start"], "end": word["end"], "text": word["word"]}
            else:
                current["text"] = f"{current['text']} {word['word']}"
                current["end"] = word["end"]
        segments.append(current)

        lines = []
        for index, seg in enumerate(segments, start=1):
            transcript = textwrap.fill(seg["text"].strip(), width=line_width)
            lines.append(
                f"{index}\n{self._format_timestamp(seg['start'])} --> {self._format_timestamp(seg['end'])}\n{transcript}\n"
            )
        return "\n".join(lines)

    def _srt_from_text(self, text: str, line_width: int) -> str:
        transcript = textwrap.fill(text.strip(), width=line_width)
        return f"1\n00:00:00,000 --> 00:00:10,000\n{transcript}\n"

    def _format_timestamp(self, seconds: float) -> str:
        millis = int(round(seconds * 1000))
        hours, remainder = divmod(millis, 3600 * 1000)
        minutes, remainder = divmod(remainder, 60 * 1000)
        secs, ms = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def log(self, message: str) -> None:
        self.log_output.insert(tk.END, message + "\n")
        self.log_output.see(tk.END)


if __name__ == "__main__":
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()
        dnd_available = True
    except Exception:
        root = tk.Tk()
        dnd_available = False

    app = DeepgramSubtitleGUI(root, dnd_available=dnd_available)
    root.mainloop()
