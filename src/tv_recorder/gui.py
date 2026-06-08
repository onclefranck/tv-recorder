from __future__ import annotations

import contextlib
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from tv_recorder.cli import run_existing_comskip, run_record_command
from tv_recorder.config import load_config


_DONE_MESSAGE = "__TV_RECORDER_DONE__"
_ACTIVITY_PREFIX = "__TV_RECORDER_ACTIVITY__"
_COMSKIP_ACTIVITY_PREFIX = "__TV_RECORDER_COMSKIP_ACTIVITY__"


class _QueueWriter:
    def __init__(self, log_queue: queue.Queue[str]) -> None:
        self._log_queue = log_queue
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._log_queue.put(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._log_queue.put(self._buffer)
            self._buffer = ""

    def isatty(self) -> bool:
        return False


class RecorderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("tv-recorder")
        self.root.geometry("920x680")
        self.root.minsize(760, 560)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancellation_event = threading.Event()
        self.config: dict = {}

        self.source = tk.StringVar()
        now = datetime.now()
        self.start_now = tk.BooleanVar(value=True)
        self.start_date = tk.StringVar(value=now.strftime("%Y-%m-%d"))
        self.start_hour = tk.IntVar(value=now.hour)
        self.start_minute = tk.IntVar(value=now.minute)
        self.duration = tk.StringVar(value="30m")
        self.output_dir = tk.StringVar(value=str(Path.cwd()))
        self.timeout_ms = tk.IntVar(value=45_000)
        self.comskip = tk.BooleanVar(value=False)
        self.comskip_file = tk.StringVar()
        self.ffmpeg_activity = tk.StringVar(value="ffmpeg: idle")
        self.comskip_activity = tk.StringVar(value="comskip: idle")

        self._build_ui()
        self._load_config()
        self._poll_logs()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        panes.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        options = ttk.Frame(panes, padding=0)
        options.columnconfigure(0, weight=1)
        panes.add(options, weight=0)

        notebook = ttk.Notebook(options)
        notebook.grid(row=0, column=0, sticky="ew")

        record_tab = ttk.Frame(notebook, padding=12)
        record_tab.columnconfigure(1, weight=1)
        notebook.add(record_tab, text="Recording")

        ttk.Label(record_tab, text="Channel").grid(row=0, column=0, sticky="w", pady=4)
        self.source_combo = ttk.Combobox(record_tab, textvariable=self.source, state="readonly")
        self.source_combo.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(record_tab, text="Start").grid(row=1, column=0, sticky="w", pady=4)
        start_frame = ttk.Frame(record_tab)
        start_frame.grid(row=1, column=1, sticky="w", pady=4)
        self.start_now_check = ttk.Checkbutton(
            start_frame,
            text="Now",
            variable=self.start_now,
            command=self._sync_start_controls,
        )
        self.start_now_check.grid(row=0, column=0, padx=(0, 12))
        self.start_date_entry = ttk.Entry(start_frame, textvariable=self.start_date, width=12)
        self.start_date_entry.grid(row=0, column=1, padx=(0, 8))
        self.start_hour_spin = ttk.Spinbox(
            start_frame,
            textvariable=self.start_hour,
            from_=0,
            to=23,
            width=3,
            format="%02.0f",
        )
        self.start_hour_spin.grid(row=0, column=2)
        ttk.Label(start_frame, text=":").grid(row=0, column=3, padx=2)
        self.start_minute_spin = ttk.Spinbox(
            start_frame,
            textvariable=self.start_minute,
            from_=0,
            to=59,
            width=3,
            format="%02.0f",
        )
        self.start_minute_spin.grid(row=0, column=4)

        ttk.Label(record_tab, text="Duration").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(record_tab, textvariable=self.duration).grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(record_tab, text="Output folder").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(record_tab, textvariable=self.output_dir).grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Button(record_tab, text="Browse", command=self._browse_output_dir).grid(row=3, column=2, padx=(8, 0))

        ttk.Label(record_tab, text="Timeout Playwright").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Spinbox(record_tab, textvariable=self.timeout_ms, from_=1_000, to=300_000, increment=1_000).grid(
            row=4,
            column=1,
            sticky="w",
            pady=4,
        )

        checks = ttk.Frame(record_tab)
        checks.grid(row=5, column=1, sticky="w", pady=(8, 4))
        ttk.Checkbutton(checks, text="Comskip", variable=self.comskip).grid(row=0, column=0)

        actions = ttk.Frame(record_tab)
        actions.grid(row=6, column=1, sticky="e", pady=(10, 0))
        self.record_button = ttk.Button(actions, text="Start", command=self._start_recording)
        self.record_button.grid(row=0, column=0)
        self.stop_record_button = ttk.Button(actions, text="Stop", command=self._request_stop, state="disabled")
        self.stop_record_button.grid(row=0, column=1, padx=(8, 0))

        comskip_tab = ttk.Frame(notebook, padding=12)
        comskip_tab.columnconfigure(1, weight=1)
        notebook.add(comskip_tab, text="Comskip")

        ttk.Label(comskip_tab, text="File").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(comskip_tab, textvariable=self.comskip_file).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(comskip_tab, text="Browse", command=self._browse_comskip_file).grid(row=0, column=2, padx=(8, 0))
        self.comskip_button = ttk.Button(comskip_tab, text="Run Comskip", command=self._start_existing_comskip)
        self.comskip_button.grid(row=1, column=1, sticky="e", pady=(10, 0))
        self.stop_comskip_button = ttk.Button(comskip_tab, text="Stop", command=self._request_stop, state="disabled")
        self.stop_comskip_button.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(10, 0))

        log_frame = ttk.Frame(panes)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        panes.add(log_frame, weight=1)

        log_header = ttk.Frame(log_frame)
        log_header.grid(row=0, column=0, sticky="ew", pady=(10, 4))
        log_header.columnconfigure(0, weight=1)
        ttk.Label(log_header, text="Logs").grid(row=0, column=0, sticky="w")
        ttk.Label(log_header, textvariable=self.ffmpeg_activity, width=18).grid(row=0, column=1, padx=(0, 12))
        ttk.Label(log_header, textvariable=self.comskip_activity, width=20).grid(row=0, column=2, padx=(0, 12))
        ttk.Button(log_header, text="Clear", command=self._clear_logs).grid(row=0, column=3)

        self.logs = ScrolledText(log_frame, height=16, wrap=tk.WORD, state="disabled")
        self.logs.grid(row=1, column=0, sticky="nsew")
        self._sync_start_controls()

    def _browse_output_dir(self) -> None:
        path = filedialog.askdirectory(title="Choose the output folder")
        if path:
            self.output_dir.set(path)

    def _browse_comskip_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a recording",
            filetypes=[("Videos", "*.mp4 *.mkv *.ts"), ("All files", "*.*")],
        )
        if path:
            self.comskip_file.set(path)

    def _load_config(self) -> None:
        try:
            self.config = load_config()
            sources = self.config.get("sources") or {}
            values = [
                f"{key} - {sources[key].get('display_name') or key}"
                for key in sorted(sources)
            ]
            self.source_combo["values"] = values
            if values and not self.source.get():
                self.source.set(_default_source_value(self.config, values))
            self._log(f"Loaded configuration: {len(values)} channel(s).")
        except Exception as exc:
            messagebox.showerror("Configuration", str(exc))
            self._log(f"Configuration error: {exc}")

    def _start_recording(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        source_key = self._selected_source_key()
        if not source_key:
            messagebox.showerror("Recording", "Choose a channel.")
            return
        self.cancellation_event = threading.Event()
        self._set_running(True)
        self.ffmpeg_activity.set("ffmpeg: waiting")
        self.comskip_activity.set("comskip: idle")
        self._log("Starting recording.")
        self.worker = threading.Thread(
            target=self._recording_worker,
            args=(source_key,),
            daemon=True,
        )
        self.worker.start()

    def _start_existing_comskip(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.comskip_file.get().strip():
            messagebox.showerror("Comskip", "Choose a recording file.")
            return
        self.cancellation_event = threading.Event()
        self._set_running(True)
        self.ffmpeg_activity.set("ffmpeg: idle")
        self.comskip_activity.set("comskip: waiting")
        self._log("Starting Comskip.")
        self.worker = threading.Thread(target=self._existing_comskip_worker, daemon=True)
        self.worker.start()

    def _recording_worker(self, source_key: str) -> None:
        writer = _QueueWriter(self.log_queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                exit_code = run_record_command(
                    self.config,
                    source_key=source_key,
                    start_value=self._start_value(),
                    duration_value=self.duration.get(),
                    output_dir=Path(self.output_dir.get()),
                    headful=False,
                    timeout_ms=int(self.timeout_ms.get()),
                    ffmpeg_path="ffmpeg",
                    comskip=self.comskip.get(),
                    dry_run=False,
                    log_level="info",
                    activity_callback=self._queue_ffmpeg_activity,
                    comskip_activity_callback=self._queue_comskip_activity,
                    cancellation_event=self.cancellation_event,
                )
                print(f"Finished with exit code {exit_code}.")
        except Exception as exc:
            self.log_queue.put(f"Error: {exc}")
        finally:
            writer.flush()
            self.log_queue.put(_DONE_MESSAGE)

    def _existing_comskip_worker(self) -> None:
        writer = _QueueWriter(self.log_queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                exit_code = run_existing_comskip(
                    self.config,
                    Path(self.comskip_file.get()),
                    ffmpeg_path="ffmpeg",
                    log_level="info",
                    activity_callback=self._queue_comskip_activity,
                    cancellation_event=self.cancellation_event,
                )
                print(f"Finished with exit code {exit_code}.")
        except Exception as exc:
            self.log_queue.put(f"Error: {exc}")
        finally:
            writer.flush()
            self.log_queue.put(_DONE_MESSAGE)

    def _selected_source_key(self) -> str:
        value = self.source.get()
        return value.split(" - ", 1)[0].strip()

    def _start_value(self) -> str:
        if self.start_now.get():
            return "now"
        date_text = self.start_date.get().strip()
        hour = int(self.start_hour.get())
        minute = int(self.start_minute.get())
        return f"{date_text}T{hour:02d}:{minute:02d}:00"

    def _sync_start_controls(self) -> None:
        state = "disabled" if self.start_now.get() else "normal"
        self.start_date_entry.configure(state=state)
        self.start_hour_spin.configure(state=state)
        self.start_minute_spin.configure(state=state)

    def _poll_logs(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()
                if message == _DONE_MESSAGE:
                    self._set_running(False)
                    self.ffmpeg_activity.set("ffmpeg: idle")
                    self.comskip_activity.set("comskip: idle")
                elif message.startswith(_ACTIVITY_PREFIX):
                    frame = message.removeprefix(_ACTIVITY_PREFIX)
                    self.ffmpeg_activity.set(f"ffmpeg: {frame}")
                elif message.startswith(_COMSKIP_ACTIVITY_PREFIX):
                    frame = message.removeprefix(_COMSKIP_ACTIVITY_PREFIX)
                    self.comskip_activity.set(f"comskip: {frame}")
                else:
                    self._log(message)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_logs)

    def _log(self, message: str) -> None:
        self.logs.configure(state="normal")
        self.logs.insert(tk.END, message + "\n")
        self.logs.see(tk.END)
        self.logs.configure(state="disabled")

    def _clear_logs(self) -> None:
        self.logs.configure(state="normal")
        self.logs.delete("1.0", tk.END)
        self.logs.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        start_state = "disabled" if running else "normal"
        stop_state = "normal" if running else "disabled"
        self.record_button.configure(state=start_state)
        self.comskip_button.configure(state=start_state)
        self.stop_record_button.configure(state=stop_state)
        self.stop_comskip_button.configure(state=stop_state)

    def _request_stop(self) -> None:
        if self.cancellation_event.is_set():
            return
        self.cancellation_event.set()
        self.stop_record_button.configure(state="disabled")
        self.stop_comskip_button.configure(state="disabled")
        self._log("Stop requested.")

    def _queue_ffmpeg_activity(self, frame: str) -> None:
        self.log_queue.put(f"{_ACTIVITY_PREFIX}{frame}")

    def _queue_comskip_activity(self, frame: str) -> None:
        self.log_queue.put(f"{_COMSKIP_ACTIVITY_PREFIX}{frame}")


def main() -> None:
    root = tk.Tk()
    RecorderApp(root)
    root.mainloop()


def _default_source_value(config: dict, values: list[str]) -> str:
    default_key = config.get("default")
    if isinstance(default_key, str):
        prefix = f"{default_key} - "
        match = next((value for value in values if value.startswith(prefix)), None)
        if match:
            return match
    return values[0]
