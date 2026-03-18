import inspect
import json
import os
import shlex
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from urllib import error, parse, request
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv
from PySide6.QtCore import QMimeData, QObject, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

load_dotenv()

try:
    import qdarktheme
except ImportError:  # optional dependency at runtime for graceful fallback
    qdarktheme = None

from deepgram import DeepgramClient
from deepgram.core import RequestOptions

POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 300
BATCH_PARALLEL_JOBS = 3
SUPPORTED_AUDIO_EXTENSIONS = (
    ".wav",
    ".mp3",
    ".m4a",
    ".flac",
    ".ogg",
    ".aac",
    ".mp4",
    ".webm",
    ".opus",
    ".wma",
    ".amr",
    ".aiff",
    ".aif",
    ".caf",
    ".m4b",
)
LANGUAGE_OPTIONS = {
    "自动(检测)": "",
    "多语言(multi)": "multi",
    "英语(en)": "en",
    "日语(ja)": "ja",
    "中文(zh)": "zh",
    "韩语(ko)": "ko",
    "法语(fr)": "fr",
    "德语(de)": "de",
    "西班牙语(es)": "es",
    "葡萄牙语(pt)": "pt",
    "意大利语(it)": "it",
}


class FileStatus(str, Enum):
    PENDING = "待处理"
    RUNNING = "转录中"
    SUCCESS = "已完成"
    FAILED = "失败"


@dataclass
class TranscriptionOptions:
    api_key: str
    model: str
    language: str
    detect_language: bool
    word_timestamps: bool
    punctuate: bool
    smart_format: bool
    utterances: bool
    diarize: bool
    numerals: bool
    filler_words: bool
    profanity_filter: bool
    paragraphs: bool
    max_chars: int
    max_duration: float
    max_pause: float
    word_segmentation: bool
    timeout_seconds: int
    max_retries: int
    output_directory: str


class DropListWidget(QListWidget):
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        files = self._extract_paths(event.mimeData())
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _extract_paths(self, mime_data: QMimeData) -> list[str]:
        paths: list[str] = []
        if mime_data.hasUrls():
            for url in mime_data.urls():
                if url.isLocalFile():
                    paths.append(url.toLocalFile())
        elif mime_data.hasText():
            text = mime_data.text().strip()
            if text:
                try:
                    paths.extend(shlex.split(text))
                except ValueError:
                    paths.append(text)
        return paths


class TranscriptionWorker(QObject):
    log_message = Signal(str)
    progress_message = Signal(str)
    progress_updated = Signal(int, int)
    finished = Signal(int, int)
    file_status_changed = Signal(str, str, str)

    def __init__(self, paths: list[str], options: TranscriptionOptions) -> None:
        super().__init__()
        self.paths = paths
        self.options = options

    def run(self) -> None:
        total = len(self.paths)
        completed = 0
        failed = 0

        self.log_message.emit(f"批量模式已启用，并发任务数: {BATCH_PARALLEL_JOBS}")

        with ThreadPoolExecutor(max_workers=BATCH_PARALLEL_JOBS) as executor:
            future_to_path = {
                executor.submit(self._transcribe_single_file, path, self.options): path for path in self.paths
            }
            for future in as_completed(future_to_path):
                completed += 1
                path = future_to_path[future]
                try:
                    future.result()
                    self.file_status_changed.emit(path, FileStatus.SUCCESS.value, "")
                    self.log_message.emit(f"[{completed}/{total}] 完成: {path}")
                except Exception as exc:  # noqa: BLE001 - surfaced to UI log
                    failed += 1
                    self.file_status_changed.emit(path, FileStatus.FAILED.value, str(exc))
                    self.log_message.emit(f"[{completed}/{total}] 转录失败: {exc}")
                self.progress_updated.emit(completed, total)
                self.progress_message.emit(f"转录中 ({completed}/{total})...")

        self.finished.emit(total, failed)

    def _transcribe_single_file(self, path: str, options: TranscriptionOptions) -> None:
        self.file_status_changed.emit(path, FileStatus.RUNNING.value, "")
        try:
            client = DeepgramClient(api_key=options.api_key or None)
            with open(path, "rb") as audio_file:
                audio_data = audio_file.read()

            params = self._build_params(options)
            self.log_message.emit(f"请求参数: {json.dumps(params, ensure_ascii=False)}")

            response = self._transcribe_with_retry(client, audio_data, params, options.max_retries)
            response_dict = self._response_to_dict(response)
            if response_dict.get("results") is None:
                response_dict = self._poll_transcription_result(response_dict, options.api_key)
            srt_text = self._build_srt(response_dict, options)

            output_name = os.path.splitext(os.path.basename(path))[0] + ".srt"
            output_dir = options.output_directory or os.path.dirname(path)
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, output_name)
            with open(output_path, "w", encoding="utf-8") as srt_file:
                srt_file.write(srt_text)

            self.log_message.emit(f"转录完成，已保存字幕: {output_path}")
        except Exception as exc:  # noqa: BLE001 - wrapped with file path context
            raise RuntimeError(f"{path}: {exc}") from exc

    def _transcribe_with_retry(
        self, client: DeepgramClient, audio_data: bytes, params: dict, max_retries: int
    ) -> object:
        retry_count = max(0, max_retries)
        last_error: Exception | None = None

        for attempt in range(retry_count + 1):
            try:
                return self._transcribe_with_sdk_compat(client, audio_data, params)
            except Exception as exc:  # noqa: BLE001 - compatibility + network handling
                if attempt >= retry_count or not self._is_retryable_network_error(exc):
                    raise
                last_error = exc
                wait_seconds = min(8, 2**attempt)
                self.log_message.emit(
                    f"网络异常，准备重试 ({attempt + 1}/{retry_count})，{wait_seconds}s 后再次尝试: {exc}"
                )
                time.sleep(wait_seconds)

        if last_error is not None:
            raise last_error
        raise RuntimeError("未知错误：转录重试流程未返回结果。")

    def _transcribe_with_sdk_compat(self, client: DeepgramClient, audio_data: bytes, params: dict) -> object:
        transcribe_method = client.listen.v1.media.transcribe_file
        call_variants = [
            lambda: transcribe_method(request=audio_data, **params),
            lambda: transcribe_method(audio_data, **params),
            lambda: transcribe_method(request={"buffer": audio_data}, **params),
            lambda: transcribe_method({"buffer": audio_data}, **params),
        ]

        call_errors: list[str] = []
        for call in call_variants:
            try:
                return call()
            except TypeError as exc:
                call_errors.append(str(exc))

        method_signature = inspect.signature(transcribe_method)
        raise TypeError(
            "无法匹配当前 Deepgram SDK 的 transcribe_file 接口。"
            f"签名: {method_signature}; 最近错误: {call_errors[-1] if call_errors else 'unknown'}"
        )

    def _is_retryable_network_error(self, exc: Exception) -> bool:
        retryable_exceptions = (TimeoutError, socket.timeout, OSError)
        if isinstance(exc, retryable_exceptions):
            return True

        text = str(exc).lower()
        retryable_markers = (
            "10060",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "connection refused",
            "network is unreachable",
        )
        return any(marker in text for marker in retryable_markers)

    def _poll_transcription_result(self, response: dict, api_key: str) -> dict:
        request_id = response.get("request_id") or response.get("metadata", {}).get("request_id")
        if not request_id:
            raise ValueError("转录返回结果为空，且未提供 request_id，无法继续轮询。")

        headers = {
            "Authorization": f"Token {api_key}" if api_key else "",
            "Accept": "application/json",
        }
        headers = {k: v for k, v in headers.items() if v}
        query = parse.urlencode({"extra": "true"})
        endpoint = f"https://api.deepgram.com/v1/listen/{request_id}?{query}"
        start_time = time.monotonic()

        self.log_message.emit(
            f"未直接返回结果，进入异步结果轮询（间隔 {POLL_INTERVAL_SECONDS}s，超时 {POLL_TIMEOUT_SECONDS}s）..."
        )

        while True:
            if time.monotonic() - start_time >= POLL_TIMEOUT_SECONDS:
                raise TimeoutError(f"轮询超时（>{POLL_TIMEOUT_SECONDS}s），request_id={request_id}")

            req = request.Request(endpoint, headers=headers, method="GET")
            try:
                with request.urlopen(req, timeout=max(10, POLL_INTERVAL_SECONDS + 2)) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except error.HTTPError as exc:
                if exc.code == 404:
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue
                raise RuntimeError(f"轮询请求失败: HTTP {exc.code}") from exc
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"轮询请求异常: {exc}") from exc

            if payload.get("results"):
                return payload
            time.sleep(POLL_INTERVAL_SECONDS)

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
        request_options: RequestOptions = {
            "timeout_in_seconds": max(1, options.timeout_seconds),
            "max_retries": max(0, options.max_retries),
        }
        params["request_options"] = request_options
        return {key: value for key, value in params.items() if value is not None}

    def _response_to_dict(self, response: object) -> dict:
        if hasattr(response, "model_dump"):
            return response.model_dump()  # type: ignore[no-any-return]
        if hasattr(response, "dict"):
            return response.dict()  # type: ignore[no-any-return]
        if isinstance(response, dict):
            return response
        return {}

    def _build_srt(self, response: dict, options: TranscriptionOptions) -> str:
        max_chars = max(1, options.max_chars)
        max_duration = max(0.1, options.max_duration)
        max_pause = max(0.0, options.max_pause)
        results = response.get("results", {}) or {}
        utterances = results.get("utterances")
        channels = results.get("channels", [])
        if channels:
            alternatives = channels[0].get("alternatives", [])
            if alternatives:
                words = alternatives[0].get("words", [])
                if options.word_segmentation and words:
                    return self._srt_from_words(words, max_chars, max_duration, max_pause)
                if utterances:
                    utterance_end = max((item.get("end", 0) for item in utterances), default=0)
                    words_end = max((item.get("end", 0) for item in words), default=0)
                    if words_end > utterance_end + 0.5 or self._words_outside_utterances(words, utterances):
                        return self._srt_from_words(words, max_chars, max_duration, max_pause)
                    return self._srt_from_utterances(utterances)
                if words:
                    return self._srt_from_words(words, max_chars, max_duration, max_pause)

        if utterances:
            return self._srt_from_utterances(utterances)

        transcript = results.get("transcripts") or []
        if transcript:
            return self._srt_from_text(transcript[0].get("transcript", ""))

        return ""

    def _words_outside_utterances(self, words: list, utterances: list) -> bool:
        if not words or not utterances:
            return False
        sorted_utterances = sorted(utterances, key=lambda item: item.get("start", 0))
        utterance_index = 0
        current = sorted_utterances[utterance_index]
        for word in words:
            word_start = word.get("start", 0)
            while utterance_index < len(sorted_utterances) and word_start > current.get("end", 0):
                utterance_index += 1
                if utterance_index >= len(sorted_utterances):
                    return True
                current = sorted_utterances[utterance_index]
            if word_start < current.get("start", 0):
                return True
        return False

    def _srt_from_utterances(self, utterances: list) -> str:
        lines = []
        for index, utterance in enumerate(utterances, start=1):
            start = utterance.get("start", 0)
            end = utterance.get("end", 0)
            transcript = utterance.get("transcript", "").strip()
            lines.append(
                f"{index}\n{self._format_timestamp(start)} --> {self._format_timestamp(end)}\n{transcript}\n"
            )
        return "\n".join(lines)

    def _srt_from_words(self, words: list, max_chars: int, max_duration: float, max_pause: float) -> str:
        if not words:
            return ""
        segments = []
        current = {"start": words[0]["start"], "end": words[0]["end"], "text": words[0]["word"]}
        for word in words[1:]:
            text = current["text"]
            next_text = f"{text} {word['word']}".strip()
            duration = word["end"] - current["start"]
            pause = word["start"] - current["end"]
            if pause > max_pause or duration > max_duration or len(next_text) > max_chars:
                segments.append(current)
                current = {"start": word["start"], "end": word["end"], "text": word["word"]}
            else:
                current["text"] = next_text
                current["end"] = word["end"]
        segments.append(current)

        lines = []
        for index, seg in enumerate(segments, start=1):
            transcript = seg["text"].strip()
            lines.append(
                f"{index}\n{self._format_timestamp(seg['start'])} --> {self._format_timestamp(seg['end'])}\n{transcript}\n"
            )
        return "\n".join(lines)

    def _srt_from_text(self, text: str) -> str:
        transcript = text.strip()
        return f"1\n00:00:00,000 --> 00:00:10,000\n{transcript}\n"

    def _format_timestamp(self, seconds: float) -> str:
        millis = int(round(seconds * 1000))
        hours, remainder = divmod(millis, 3600 * 1000)
        minutes, remainder = divmod(remainder, 60 * 1000)
        secs, ms = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


class DeepgramSubtitleGUI(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Deepgram 字幕转录 · PySide6")
        self.resize(1120, 860)
        self.audio_queue: list[str] = []
        self.api_key_history: list[str] = []
        self.worker_thread: QThread | None = None
        self.worker: TranscriptionWorker | None = None
        self.settings = QSettings("deepgram-python-sdk", "deepgram-subtitle-gui")

        self._build_ui()
        self._load_settings()
        self._update_queue_placeholder()
        self.statusBar().showMessage("准备就绪")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        header = QFrame()
        header.setObjectName("heroCard")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("Deepgram 字幕转录")
        title.setObjectName("titleLabel")
        subtitle = QLabel("PySide6 + qdarktheme 界面，支持拖拽上传、批量转写、轮询与 SRT 导出。")
        subtitle.setObjectName("subtitleLabel")
        subtitle.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        main_layout.addWidget(header)

        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        main_layout.addWidget(content_splitter, 1)

        left_panel = QWidget()
        left_column = QVBoxLayout(left_panel)
        left_column.setContentsMargins(0, 0, 0, 0)
        left_column.setSpacing(12)
        content_splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_column = QVBoxLayout(right_panel)
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(12)
        content_splitter.addWidget(right_panel)

        content_splitter.setStretchFactor(0, 9)
        content_splitter.setStretchFactor(1, 11)

        queue_group = QGroupBox("音频队列")
        queue_layout = QVBoxLayout(queue_group)

        self.queue_list = DropListWidget()
        self.queue_list.setMinimumHeight(220)
        self.queue_list.files_dropped.connect(self._add_files_to_queue)
        self.queue_list.itemSelectionChanged.connect(self._on_queue_selection)
        queue_layout.addWidget(self.queue_list)

        queue_actions = QHBoxLayout()
        self.add_files_button = QPushButton("添加文件")
        self.remove_files_button = QPushButton("移除选中")
        self.clear_files_button = QPushButton("清空队列")
        queue_actions.addWidget(self.add_files_button)
        queue_actions.addWidget(self.remove_files_button)
        queue_actions.addWidget(self.clear_files_button)
        queue_layout.addLayout(queue_actions)

        left_column.addWidget(queue_group)

        actions_group = QGroupBox("执行")
        actions_layout = QVBoxLayout(actions_group)
        self.start_button = QPushButton("开始转录")
        self.retry_failed_button = QPushButton("重试失败任务")
        self.retry_failed_button.setEnabled(False)
        self.start_button.setMinimumHeight(46)
        actions_layout.addWidget(self.start_button)
        actions_layout.addWidget(self.retry_failed_button)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        actions_layout.addWidget(self.progress_bar)
        left_column.addWidget(actions_group)

        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        log_layout.addWidget(self.log_output)
        left_column.addWidget(log_group, 1)

        options_group = QGroupBox("转录参数")
        options_layout = QVBoxLayout(options_group)

        form_layout = QFormLayout()
        form_layout.setLabelAlignment(Qt.AlignLeft)
        form_layout.setFormAlignment(Qt.AlignTop)

        self.api_key_combo = QComboBox()
        self.api_key_combo.setEditable(True)
        self.api_key_combo.setInsertPolicy(QComboBox.NoInsert)
        api_row = QHBoxLayout()
        api_row.addWidget(self.api_key_combo, 1)
        self.paste_api_button = QPushButton("粘贴")
        self.import_api_button = QPushButton("导入")
        self.clear_api_button = QPushButton("清空")
        self.remove_api_button = QPushButton("删除当前")
        for button in [self.paste_api_button, self.import_api_button, self.clear_api_button, self.remove_api_button]:
            api_row.addWidget(button)
        api_widget = QWidget()
        api_widget.setLayout(api_row)
        form_layout.addRow("API Key", api_widget)

        self.model_edit = QLineEdit("nova-3")
        form_layout.addRow("模型(model)", self.model_edit)

        self.language_combo = QComboBox()
        self.language_combo.addItems(LANGUAGE_OPTIONS.keys())
        form_layout.addRow("语言(language)", self.language_combo)

        self.max_chars_edit = QLineEdit("16")
        self.max_duration_edit = QLineEdit("6.0")
        self.max_pause_edit = QLineEdit("1.0")
        self.timeout_edit = QLineEdit("300")
        self.retry_edit = QLineEdit("2")
        output_dir_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_button = QPushButton("选择目录")
        output_dir_row.addWidget(self.output_dir_edit, 1)
        output_dir_row.addWidget(self.output_dir_button)
        output_dir_widget = QWidget()
        output_dir_widget.setLayout(output_dir_row)
        form_layout.addRow("输出目录", output_dir_widget)
        form_layout.addRow("每段最大字符数", self.max_chars_edit)
        form_layout.addRow("每段最长期限(秒)", self.max_duration_edit)
        form_layout.addRow("每段最大停顿(秒)", self.max_pause_edit)
        form_layout.addRow("请求超时(秒)", self.timeout_edit)
        form_layout.addRow("重试次数", self.retry_edit)
        options_layout.addLayout(form_layout)

        checkbox_grid = QGridLayout()
        checkbox_specs = [
            ("自动识别语言", "detect_language"),
            ("字词级时间戳", "word_timestamps"),
            ("标点", "punctuate"),
            ("智能格式化", "smart_format"),
            ("按话语分段(utterances)", "utterances"),
            ("说话人区分", "diarize"),
            ("数字转写", "numerals"),
            ("填充词(filler)", "filler_words"),
            ("敏感词过滤", "profanity_filter"),
            ("段落(paragraphs)", "paragraphs"),
            ("启用字词级自动分段", "word_segmentation"),
        ]
        self.checkboxes: dict[str, QCheckBox] = {}
        for index, (label, key) in enumerate(checkbox_specs):
            checkbox = QCheckBox(label)
            self.checkboxes[key] = checkbox
            checkbox_grid.addWidget(checkbox, index // 2, index % 2)
        options_layout.addLayout(checkbox_grid)
        right_column.addWidget(options_group)

        self.checkboxes["word_timestamps"].setChecked(True)
        self.checkboxes["punctuate"].setChecked(True)
        self.checkboxes["smart_format"].setChecked(True)
        self.checkboxes["utterances"].setChecked(True)
        self.checkboxes["numerals"].setChecked(True)
        self.checkboxes["word_segmentation"].setChecked(True)

        self.add_files_button.clicked.connect(self.select_files)
        self.remove_files_button.clicked.connect(self.remove_selected_files)
        self.clear_files_button.clicked.connect(self.clear_queue)
        self.start_button.clicked.connect(self.start_transcription)
        self.retry_failed_button.clicked.connect(self.retry_failed_tasks)
        self.output_dir_button.clicked.connect(self.select_output_directory)
        self.paste_api_button.clicked.connect(self.paste_api_key)
        self.import_api_button.clicked.connect(self.import_api_key)
        self.clear_api_button.clicked.connect(self.clear_api_key)
        self.remove_api_button.clicked.connect(self.remove_selected_api_key)
        self.api_key_combo.currentTextChanged.connect(self._on_api_key_selected)
        self.api_key_combo.lineEdit().editingFinished.connect(self._remember_current_api_key)

        clear_logs_action = QAction("清空日志", self)
        clear_logs_action.triggered.connect(self.log_output.clear)
        self.addAction(clear_logs_action)

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

    def select_files(self) -> None:
        filter_text = f"音频文件 ({' '.join(f'*{ext}' for ext in SUPPORTED_AUDIO_EXTENSIONS)});;所有文件 (*.*)"
        filenames, _ = QFileDialog.getOpenFileNames(self, "选择音频文件", "", filter_text)
        self._add_files_to_queue(filenames)

    def remove_selected_files(self) -> None:
        rows = sorted({self.queue_list.row(item) for item in self.queue_list.selectedItems()}, reverse=True)
        if not rows:
            return
        for row in rows:
            del self.audio_queue[row]
        self._refresh_queue()

    def clear_queue(self) -> None:
        self.audio_queue.clear()
        self._refresh_queue()

    def _normalize_input_path(self, raw_path: str) -> str:
        candidate = raw_path.strip().strip("{}").strip().strip("\"'")
        if not candidate:
            return ""

        parsed = urlparse(candidate)
        if parsed.scheme == "file":
            candidate = unquote(parsed.path or "")
            if parsed.netloc:
                candidate = f"//{parsed.netloc}{candidate}"
            if len(candidate) >= 3 and candidate[0] == "/" and candidate[2] == ":":
                candidate = candidate[1:]

        candidate = os.path.expanduser(os.path.expandvars(candidate))
        return os.path.normpath(candidate)

    def _add_files_to_queue(self, files: list[str]) -> None:
        added_count = 0
        for filename in files:
            normalized = self._normalize_input_path(filename)
            if not normalized:
                continue
            ext = os.path.splitext(normalized)[1].lower()
            if ext not in SUPPORTED_AUDIO_EXTENSIONS:
                self.log(f"跳过不支持格式: {normalized}")
                continue
            if not os.path.exists(normalized):
                self.log(f"跳过不存在的文件: {normalized}")
                continue
            if normalized not in self.audio_queue:
                self.audio_queue.append(normalized)
                added_count += 1
        if added_count:
            self.log(f"已添加 {added_count} 个文件到队列")
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        self.queue_list.clear()
        for file_path in self.audio_queue:
            item = QListWidgetItem(self._format_queue_item_text(file_path, FileStatus.PENDING.value), self.queue_list)
            item.setData(Qt.UserRole, file_path)
            item.setData(Qt.UserRole + 1, FileStatus.PENDING.value)
            self._set_item_color(item, FileStatus.PENDING.value)
        self._update_queue_placeholder()

    def _update_queue_placeholder(self) -> None:
        if not self.audio_queue:
            self.queue_list.setStyleSheet("QListWidget { border: 1px dashed palette(mid); }")
            self.queue_list.setToolTip("拖拽音频文件到这里")
        else:
            self.queue_list.setStyleSheet("")
            self.queue_list.setToolTip("")

    def _on_queue_selection(self) -> None:
        if not self.queue_list.selectedItems():
            return

    def select_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir_edit.text().strip() or os.getcwd())
        if directory:
            self.output_dir_edit.setText(directory)

    def _format_queue_item_text(self, file_path: str, status: str, detail: str = "") -> str:
        base = f"[{status}] {file_path}"
        if detail:
            return f"{base}\n    {detail}"
        return base

    def _find_queue_item(self, file_path: str) -> QListWidgetItem | None:
        for index in range(self.queue_list.count()):
            item = self.queue_list.item(index)
            if item.data(Qt.UserRole) == file_path:
                return item
        return None

    def _set_item_color(self, item: QListWidgetItem, status: str) -> None:
        colors = {
            FileStatus.PENDING.value: "#94A3B8",
            FileStatus.RUNNING.value: "#60A5FA",
            FileStatus.SUCCESS.value: "#34D399",
            FileStatus.FAILED.value: "#F87171",
        }
        item.setForeground(QColor(colors.get(status, "#E6EAF2")))

    def _initialize_file_statuses(self, paths: list[str]) -> None:
        for path in paths:
            self._update_file_status(path, FileStatus.PENDING.value, "")

    def _update_file_status(self, file_path: str, status: str, detail: str = "") -> None:
        item = self._find_queue_item(file_path)
        if item is None:
            return
        item.setText(self._format_queue_item_text(file_path, status, detail))
        item.setData(Qt.UserRole + 1, status)
        item.setData(Qt.UserRole + 2, detail)
        self._set_item_color(item, status)

    def _reset_progress(self, total: int) -> None:
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"0/{total}" if total else "0/0")

    def _update_progress(self, completed: int, total: int) -> None:
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(completed)
        self.progress_bar.setFormat(f"{completed}/{total}")

    def _failed_paths(self) -> list[str]:
        failed = []
        for index in range(self.queue_list.count()):
            item = self.queue_list.item(index)
            if item.data(Qt.UserRole + 1) == FileStatus.FAILED.value:
                failed.append(item.data(Qt.UserRole))
        return failed

    def retry_failed_tasks(self) -> None:
        failed_paths = self._failed_paths()
        if not failed_paths:
            QMessageBox.information(self, "提示", "当前没有失败任务可重试。")
            return
        self.audio_queue = failed_paths
        self._refresh_queue()
        self.start_transcription()

    def paste_api_key(self) -> None:
        clipboard = QApplication.clipboard()
        key = clipboard.text().strip()
        if not key:
            QMessageBox.warning(self, "提示", "剪贴板为空或无法读取。")
            return
        self._set_api_keys_from_text(key)

    def import_api_key(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "导入 API Key",
            "",
            "文本文件 (*.txt *.env);;所有文件 (*.*)",
        )
        if not filename:
            return
        try:
            with open(filename, "r", encoding="utf-8") as key_file:
                content = key_file.read().strip()
        except OSError as exc:
            QMessageBox.critical(self, "错误", f"读取失败: {exc}")
            return

        keys = self._extract_api_keys(content)
        if not keys:
            QMessageBox.warning(self, "提示", "未找到有效的 API Key。")
            return
        self._set_api_keys(keys)

    def clear_api_key(self) -> None:
        self.api_key_combo.setCurrentText("")
        self.api_key_history.clear()
        self._update_api_key_combo()

    def remove_selected_api_key(self) -> None:
        selected = self.api_key_combo.currentText().strip()
        if not selected:
            return
        if selected in self.api_key_history:
            self.api_key_history.remove(selected)
        self.api_key_combo.setCurrentText(self.api_key_history[0] if self.api_key_history else "")
        self._update_api_key_combo()

    def _extract_api_keys(self, content: str) -> list[str]:
        keys: list[str] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("DEEPGRAM_API_KEY"):
                _, value = line.split("=", 1)
                candidate = value.strip().strip("'").strip('"')
                if candidate:
                    keys.append(candidate)
                continue
            for token in line.replace(",", " ").split():
                if token:
                    keys.append(token)
        return list(dict.fromkeys(keys))

    def _set_api_keys_from_text(self, text: str) -> None:
        keys = self._extract_api_keys(text)
        if not keys:
            QMessageBox.warning(self, "提示", "未找到有效的 API Key。")
            return
        self._set_api_keys(keys)

    def _set_api_keys(self, keys: list[str]) -> None:
        for key in keys:
            self._add_api_key_to_history(key)
        self.api_key_combo.setCurrentText(keys[0])
        self._update_api_key_combo()

    def _add_api_key_to_history(self, key: str) -> None:
        if key and key not in self.api_key_history:
            self.api_key_history.append(key)

    def _update_api_key_combo(self) -> None:
        current_text = self.api_key_combo.currentText()
        self.api_key_combo.blockSignals(True)
        self.api_key_combo.clear()
        self.api_key_combo.addItems(self.api_key_history)
        self.api_key_combo.setCurrentText(current_text)
        self.api_key_combo.blockSignals(False)

    def _on_api_key_selected(self, selected: str) -> None:
        if selected.strip():
            self._add_api_key_to_history(selected.strip())

    def _remember_current_api_key(self) -> None:
        selected = self.api_key_combo.currentText().strip()
        if selected:
            self._add_api_key_to_history(selected)
            self._update_api_key_combo()

    def _normalize_language(self, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        return LANGUAGE_OPTIONS.get(value, value)

    def _collect_options(self) -> TranscriptionOptions:
        output_directory = self.output_dir_edit.text().strip()
        if output_directory:
            output_directory = os.path.abspath(output_directory)

        return TranscriptionOptions(
            api_key=self.api_key_combo.currentText().strip(),
            model=self.model_edit.text().strip(),
            language=self._normalize_language(self.language_combo.currentText().strip()),
            detect_language=self.checkboxes["detect_language"].isChecked(),
            word_timestamps=self.checkboxes["word_timestamps"].isChecked(),
            punctuate=self.checkboxes["punctuate"].isChecked(),
            smart_format=self.checkboxes["smart_format"].isChecked(),
            utterances=self.checkboxes["utterances"].isChecked(),
            diarize=self.checkboxes["diarize"].isChecked(),
            numerals=self.checkboxes["numerals"].isChecked(),
            filler_words=self.checkboxes["filler_words"].isChecked(),
            profanity_filter=self.checkboxes["profanity_filter"].isChecked(),
            paragraphs=self.checkboxes["paragraphs"].isChecked(),
            max_chars=int(self.max_chars_edit.text().strip() or "16"),
            max_duration=float(self.max_duration_edit.text().strip() or "6.0"),
            max_pause=float(self.max_pause_edit.text().strip() or "1.0"),
            word_segmentation=self.checkboxes["word_segmentation"].isChecked(),
            timeout_seconds=int(self.timeout_edit.text().strip() or "300"),
            max_retries=int(self.retry_edit.text().strip() or "2"),
            output_directory=output_directory,
        )

    def start_transcription(self) -> None:
        if self.worker_thread is not None:
            QMessageBox.information(self, "提示", "已有转录任务正在运行，请等待完成。")
            return
        if not self.audio_queue:
            QMessageBox.warning(self, "提示", "请先选择或拖放音频文件。")
            return

        invalid_paths = [path for path in self.audio_queue if not os.path.exists(path)]
        if invalid_paths:
            QMessageBox.critical(self, "错误", f"文件不存在: {invalid_paths[0]}")
            return

        try:
            options = self._collect_options()
        except ValueError as exc:
            QMessageBox.critical(self, "错误", f"参数格式错误: {exc}")
            return

        self._save_settings()
        self._reset_progress(len(self.audio_queue))
        self._initialize_file_statuses(self.audio_queue)
        self.statusBar().showMessage("转录中，请稍候...")
        self.log("开始转录，请等待...")
        self._set_running_state(True)

        self.worker_thread = QThread(self)
        self.worker = TranscriptionWorker(list(self.audio_queue), options)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.log_message.connect(self.log)
        self.worker.progress_message.connect(self.statusBar().showMessage)
        self.worker.progress_updated.connect(self._update_progress)
        self.worker.file_status_changed.connect(self._update_file_status)
        self.worker.finished.connect(self._on_transcription_finished)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self._cleanup_worker_thread)
        self.worker_thread.start()

    def _set_running_state(self, running: bool) -> None:
        for widget in [
            self.start_button,
            self.add_files_button,
            self.remove_files_button,
            self.clear_files_button,
            self.paste_api_button,
            self.import_api_button,
            self.clear_api_button,
            self.remove_api_button,
            self.output_dir_button,
        ]:
            widget.setDisabled(running)
        self.retry_failed_button.setDisabled(running or not self._failed_paths())

    def _on_transcription_finished(self, total: int, failed: int) -> None:
        self.retry_failed_button.setEnabled(bool(self._failed_paths()))
        if failed:
            self.statusBar().showMessage(f"完成（失败 {failed}/{total}）")
            self.log(f"全部任务结束：总计 {total} 个，失败 {failed} 个。")
        else:
            self.statusBar().showMessage("完成")
            self.log(f"全部任务结束：总计 {total} 个，全部成功。")
        self._set_running_state(False)

    def _cleanup_worker_thread(self) -> None:
        self.worker_thread = None
        self.worker = None

    def _load_settings(self) -> None:
        api_keys = self.settings.value("api_keys", [], list)
        if isinstance(api_keys, str):
            api_keys = [api_keys]
        self.api_key_history = list(dict.fromkeys(api_keys))
        api_key = self.settings.value("api_key", "", str)
        if api_key:
            self._add_api_key_to_history(api_key)
        self._update_api_key_combo()
        self.api_key_combo.setCurrentText(api_key)

        self.model_edit.setText(self.settings.value("model", self.model_edit.text(), str))
        language = self.settings.value("language", self.language_combo.currentText(), str)
        index = self.language_combo.findText(language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        else:
            self.language_combo.setCurrentText(language)

        for key in self.checkboxes:
            default = self.checkboxes[key].isChecked()
            value = self.settings.value(key, default, bool)
            if isinstance(value, str):
                value = value.lower() in {"1", "true", "yes"}
            self.checkboxes[key].setChecked(bool(value))

        self.max_chars_edit.setText(str(self.settings.value("max_chars", self.max_chars_edit.text(), str)))
        self.max_duration_edit.setText(str(self.settings.value("max_duration", self.max_duration_edit.text(), str)))
        self.max_pause_edit.setText(str(self.settings.value("max_pause", self.max_pause_edit.text(), str)))
        self.timeout_edit.setText(str(self.settings.value("timeout_seconds", self.timeout_edit.text(), str)))
        self.retry_edit.setText(str(self.settings.value("max_retries", self.retry_edit.text(), str)))
        self.output_dir_edit.setText(str(self.settings.value("output_directory", "", str)))

    def _save_settings(self) -> None:
        self._remember_current_api_key()
        self.settings.setValue("api_key", self.api_key_combo.currentText().strip())
        self.settings.setValue("api_keys", self.api_key_history)
        self.settings.setValue("model", self.model_edit.text().strip())
        self.settings.setValue("language", self.language_combo.currentText().strip())
        for key, checkbox in self.checkboxes.items():
            self.settings.setValue(key, checkbox.isChecked())
        self.settings.setValue("max_chars", self.max_chars_edit.text().strip())
        self.settings.setValue("max_duration", self.max_duration_edit.text().strip())
        self.settings.setValue("max_pause", self.max_pause_edit.text().strip())
        self.settings.setValue("timeout_seconds", self.timeout_edit.text().strip())
        self.settings.setValue("max_retries", self.retry_edit.text().strip())
        self.settings.setValue("output_directory", self.output_dir_edit.text().strip())
        self.settings.sync()

    def log(self, message: str) -> None:
        self.log_output.appendPlainText(message)
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_settings()
        if self.worker_thread is not None:
            QMessageBox.information(self, "提示", "后台任务仍在运行，窗口将在任务结束后退出。")
            event.ignore()
            return
        event.accept()


def _apply_theme(app: QApplication) -> None:
    custom_qss = """
        QWidget { color: #E6EAF2; }
        QLabel { color: #E6EAF2; background: transparent; }
        QLabel#titleLabel { font-size: 28px; font-weight: 700; color: #F8FAFC; }
        QLabel#subtitleLabel { color: #CBD5E1; font-size: 13px; }
        QGroupBox {
            font-weight: 600;
            border: 1px solid #3F4654;
            border-radius: 10px;
            margin-top: 12px;
            padding-top: 14px;
            background: #22252D;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: #F8FAFC;
        }
        QPushButton {
            padding: 8px 14px;
            border-radius: 8px;
            border: 1px solid #4B5563;
            background: #2D3748;
            color: #F8FAFC;
        }
        QPushButton:hover { background: #3B475A; }
        QPlainTextEdit, QListWidget, QLineEdit, QComboBox {
            border-radius: 8px;
            border: 1px solid #4B5563;
            background: #151821;
            color: #F8FAFC;
            selection-background-color: #2563EB;
            selection-color: #F8FAFC;
            padding: 6px;
        }
        QComboBox QAbstractItemView {
            background: #151821;
            color: #F8FAFC;
            border: 1px solid #4B5563;
            selection-background-color: #2563EB;
        }
        QPlainTextEdit[readOnly="true"], QListWidget { background: #11141B; }
        QFrame#heroCard {
            border-radius: 16px;
            border: 1px solid #3F4654;
            background: #2A2F3A;
        }
        QStatusBar { color: #E6EAF2; }
    """

    if qdarktheme is None:
        app.setStyle("Fusion")
        app.setStyleSheet(custom_qss)
        return

    if hasattr(qdarktheme, "setup_theme"):
        qdarktheme.setup_theme("auto", additional_qss=custom_qss)
        return

    if hasattr(qdarktheme, "load_stylesheet"):
        app.setStyleSheet(qdarktheme.load_stylesheet() + "\n" + custom_qss)
        return

    app.setStyle("Fusion")
    app.setStyleSheet(custom_qss)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Deepgram Subtitle GUI")
    app.setOrganizationName("deepgram-python-sdk")
    _apply_theme(app)

    window = DeepgramSubtitleGUI()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
