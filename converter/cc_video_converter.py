"""
CC Video Converter -- Tkinter GUI

Converts a video file (with optional audio) into a .ccvid file that the
companion CC: Tweaked Lua program (computercraft/video_player.lua) can play
on a monitor + speaker.

Run with:  python3 cc_video_converter.py
Requires:  ffmpeg + ffprobe on PATH, and the numpy package.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from core import ConversionOptions, ConversionError, convert, probe
from video_format import COLOR_MODE_1BIT, COLOR_MODE_2BIT, COLOR_MODE_16, MAX_WIDTH, MAX_HEIGHT

APP_TITLE = "CC Video Converter"

COLOR_MODES = [
    ("Black & white (1-bit) -- best for Bad Apple-style silhouette video", COLOR_MODE_1BIT),
    ("Grayscale (4-level) -- better for photographic / old-film footage", COLOR_MODE_2BIT),
    ("16 colors (full CC palette) -- needs an Advanced monitor, much bigger files", COLOR_MODE_16),
]
DITHER_MODES = [
    ("Floyd-Steinberg (best quality, slower)", "floyd"),
    ("Ordered / Bayer (fast, patterned look)", "bayer"),
    ("None (hard threshold, high contrast)", "none"),
]


class ConverterApp(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        self.master = master
        master.title(APP_TITLE)
        master.minsize(640, 640)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._progress_queue: "queue.Queue" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._probe_info = None

        self._build_widgets()
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        row = 0

        # -- File selection -------------------------------------------------
        file_frame = ttk.LabelFrame(self, text="Files", padding=10)
        file_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        file_frame.columnconfigure(1, weight=1)
        row += 1

        ttk.Label(file_frame, text="Input video:").grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(file_frame, text="Browse...", command=self._browse_input).grid(row=0, column=2)

        ttk.Label(file_frame, text="Output .ccvid:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.output_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(file_frame, text="Save as...", command=self._browse_output).grid(row=1, column=2, pady=(6, 0))

        self.source_info_var = tk.StringVar(value="Pick an input video to see its details here.")
        ttk.Label(file_frame, textvariable=self.source_info_var, foreground="#555").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # -- Size / framerate -------------------------------------------------
        size_frame = ttk.LabelFrame(self, text="Size & Frame Rate", padding=10)
        size_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        row += 1
        for c in range(4):
            size_frame.columnconfigure(c, weight=1)

        ttk.Label(size_frame, text="Max width (chars):").grid(row=0, column=0, sticky="w")
        self.max_w_var = tk.IntVar(value=MAX_WIDTH)
        ttk.Spinbox(size_frame, from_=4, to=MAX_WIDTH, textvariable=self.max_w_var, width=6).grid(
            row=0, column=1, sticky="w", padx=(4, 16))

        ttk.Label(size_frame, text="Max height (chars):").grid(row=0, column=2, sticky="w")
        self.max_h_var = tk.IntVar(value=MAX_HEIGHT)
        ttk.Spinbox(size_frame, from_=4, to=MAX_HEIGHT, textvariable=self.max_h_var, width=6).grid(
            row=0, column=3, sticky="w", padx=(4, 0))

        ttk.Label(size_frame, text="Max FPS:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.fps_var = tk.DoubleVar(value=20.0)
        ttk.Spinbox(size_frame, from_=1, to=40, textvariable=self.fps_var, width=6).grid(
            row=1, column=1, sticky="w", padx=(4, 16), pady=(8, 0))
        ttk.Label(size_frame, text="(actual fps is also capped at the source video's own fps)",
                  foreground="#555").grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))

        self.cell_aspect_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            size_frame,
            text="Correct for monitor pixel shape (character cells are 6x9, not square)",
            variable=self.cell_aspect_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        ttk.Label(
            size_frame,
            text="An 8x6-block monitor at the default text scale is exactly 70x40; 140x80 at twice scale; that's why those are the maximums.",
            foreground="#555", wraplength=560, justify="left",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # -- Picture -------------------------------------------------
        pic_frame = ttk.LabelFrame(self, text="Picture", padding=10)
        pic_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        row += 1
        pic_frame.columnconfigure(1, weight=1)

        ttk.Label(pic_frame, text="Color mode:").grid(row=0, column=0, sticky="w")
        self.color_mode_var = tk.StringVar(value=COLOR_MODES[0][0])
        ttk.Combobox(pic_frame, textvariable=self.color_mode_var, values=[c[0] for c in COLOR_MODES],
                     state="readonly", width=52).grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Label(pic_frame, text="Dithering:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.dither_var = tk.StringVar(value=DITHER_MODES[0][0])
        ttk.Combobox(pic_frame, textvariable=self.dither_var, values=[d[0] for d in DITHER_MODES],
                     state="readonly", width=52).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))

        ttk.Label(pic_frame, text="Contrast:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.contrast_var = tk.DoubleVar(value=1.0)
        ttk.Scale(pic_frame, from_=0.5, to=2.0, variable=self.contrast_var, orient="horizontal").grid(
            row=2, column=1, sticky="ew", padx=6, pady=(6, 0))

        ttk.Label(pic_frame, text="Brightness:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.brightness_var = tk.DoubleVar(value=0.0)
        ttk.Scale(pic_frame, from_=-50, to=50, variable=self.brightness_var, orient="horizontal").grid(
            row=3, column=1, sticky="ew", padx=6, pady=(6, 0))

        # -- Audio -------------------------------------------------
        audio_frame = ttk.LabelFrame(self, text="Audio", padding=10)
        audio_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        row += 1

        self.include_audio_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(audio_frame, text="Include audio (DFPWM, for a second dedicated audio computer)",
                        variable=self.include_audio_var).grid(row=0, column=0, sticky="w")
        ttk.Label(
            audio_frame,
            text="Audio always ships as a separate .dfpwm file (bundled in a .zip with the .ccvid), meant "
                 "to be played by a second computer over a wired/wireless network -- see the README. "
                 "Decoding audio and rendering video on the same computer competes for the same CPU budget "
                 "and tends to drop frames.",
            foreground="#555", wraplength=560, justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # -- Progress -------------------------------------------------
        prog_frame = ttk.LabelFrame(self, text="Progress", padding=10)
        prog_frame.grid(row=row, column=0, sticky="nsew", pady=(0, 10))
        self.rowconfigure(row, weight=1)
        row += 1
        prog_frame.columnconfigure(0, weight=1)
        prog_frame.rowconfigure(1, weight=1)

        self.progress_bar = ttk.Progressbar(prog_frame, mode="determinate")
        self.progress_bar.grid(row=0, column=0, sticky="ew")

        self.log_text = tk.Text(prog_frame, height=10, wrap="word", state="disabled",
                                 background="#f7f7f7", relief="flat")
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        scroll = ttk.Scrollbar(prog_frame, command=self.log_text.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        self.log_text["yscrollcommand"] = scroll.set

        # -- Action -------------------------------------------------
        action_frame = ttk.Frame(self)
        action_frame.grid(row=row, column=0, sticky="ew")
        action_frame.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(action_frame, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

        self.convert_btn = ttk.Button(action_frame, text="Convert", command=self._start_conversion)
        self.convert_btn.grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------
    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a video file",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.input_var.set(path)
        self._probe_input(path)
        if not self.output_var.get():
            base, _ = os.path.splitext(path)
            ext = ".zip" if (self._probe_info and self._probe_info.has_audio) else ".ccvid"
            self.output_var.set(base + ext)

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save as", defaultextension=".ccvid",
            filetypes=[("CC Video (with audio: .zip)", "*.zip *.ccvid"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _probe_input(self, path: str) -> None:
        try:
            info = probe(path)
        except ConversionError as e:
            self.source_info_var.set(f"Could not read this file: {e}")
            self._probe_info = None
            return
        self._probe_info = info
        audio_txt = "has audio -- will export as a .zip (video + audio)" if info.has_audio else "no audio track"
        self.source_info_var.set(
            f"Source: {info.width}x{info.height}, {info.fps:.2f} fps, "
            f"{info.duration:.1f}s, {audio_txt}"
        )
        if not info.has_audio:
            self.include_audio_var.set(False)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def _log(self, message: str) -> None:
        self.log_text["state"] = "normal"
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text["state"] = "disabled"

    def _start_conversion(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        if not input_path:
            messagebox.showerror(APP_TITLE, "Choose an input video first.")
            return
        if not os.path.isfile(input_path):
            messagebox.showerror(APP_TITLE, "Input file does not exist.")
            return
        if not output_path:
            messagebox.showerror(APP_TITLE, "Choose where to save the .ccvid file.")
            return

        color_mode = dict((label, val) for label, val in COLOR_MODES)[self.color_mode_var.get()]
        dither = dict((label, val) for label, val in DITHER_MODES)[self.dither_var.get()]

        opts = ConversionOptions(
            input_path=input_path,
            output_path=output_path,
            max_width=int(self.max_w_var.get()),
            max_height=int(self.max_h_var.get()),
            max_fps=float(self.fps_var.get()),
            color_mode=color_mode,
            dither=dither,
            correct_cell_aspect=bool(self.cell_aspect_var.get()),
            include_audio=bool(self.include_audio_var.get()),
            contrast=float(self.contrast_var.get()),
            brightness=float(self.brightness_var.get()),
            progress_cb=self._progress_from_worker,
        )

        self.log_text["state"] = "normal"
        self.log_text.delete("1.0", "end")
        self.log_text["state"] = "disabled"
        self.progress_bar["value"] = 0
        self.status_var.set("Converting...")
        self.convert_btn["state"] = "disabled"

        self._worker = threading.Thread(target=self._run_conversion, args=(opts,), daemon=True)
        self._worker.start()

    def _progress_from_worker(self, stage: str, current: int, total: int, message: str) -> None:
        # called from the worker thread -- hand off to the GUI thread via the queue
        self._progress_queue.put(("progress", stage, current, total, message))

    def _run_conversion(self, opts: ConversionOptions) -> None:
        try:
            result = convert(opts)
            self._progress_queue.put(("done", result))
        except ConversionError as e:
            self._progress_queue.put(("error", str(e)))
        except Exception as e:  # noqa: BLE001 -- surface any unexpected error to the user
            self._progress_queue.put(("error", f"Unexpected error: {e}"))

    def _poll_queue(self) -> None:
        try:
            while True:
                item = self._progress_queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, stage, current, total, message = item
                    if total > 0:
                        self.progress_bar["value"] = 100 * current / total
                    self._log(f"[{stage}] {message}")
                    self.status_var.set(message or stage)
                elif kind == "done":
                    result = item[1]
                    self.progress_bar["value"] = 100
                    self.convert_btn["state"] = "normal"
                    self.status_var.set("Done.")
                    self._log(
                        f"\nDone! {result.width}x{result.height} @ {result.fps:.2f}fps, "
                        f"{result.frame_count} frames, {result.duration_s:.1f}s"
                    )
                    self._log(
                        f"Output: {os.path.basename(result.output_path)}  "
                        f"({result.output_bytes/1024:.1f} KB total; "
                        f"video {result.video_bytes/1024:.1f} KB, audio {result.audio_bytes/1024:.1f} KB)"
                    )
                    if result.has_audio:
                        base = os.path.splitext(os.path.basename(result.output_path))[0]
                        messagebox.showinfo(
                            APP_TITLE,
                            f"Done! {os.path.basename(result.output_path)} contains two files:\n\n"
                            f"  {base}.ccvid  -> the VIDEO computer's videos folder\n"
                            f"  {base}.dfpwm  -> the AUDIO computer's videos folder\n\n"
                            "See the README for how to set up the two-computer audio setup."
                        )
                    else:
                        messagebox.showinfo(
                            APP_TITLE,
                            f"Done! Copy {os.path.basename(result.output_path)} into your video "
                            "computer's videos folder to play it."
                        )
                elif kind == "error":
                    self.convert_btn["state"] = "normal"
                    self.status_var.set("Error.")
                    self._log(f"\nERROR: {item[1]}")
                    messagebox.showerror(APP_TITLE, item[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


def main() -> None:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
