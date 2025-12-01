"""Tkinter-based UI for interactive video cropping."""
from __future__ import annotations

import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk

from .ffmpeg_utils import crop_video, extract_frame, probe_video


ASPECT_PRESETS = {
    "Freeform": None,
    "CinemaScope 2.39:1": 2.39,
    "YouTube 16:9": 16 / 9,
    "Instagram Reel 9:16": 9 / 16,
    "TikTok 9:16": 9 / 16,
    "Square 1:1": 1.0,
}


@dataclass
class CropBox:
    x: int
    y: int
    width: int
    height: int

    def as_tuple(self) -> Tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height


class VideoCropperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Video Cropper")
        self.video_path: Path | None = None
        self.metadata = None
        self.current_image: Image.Image | None = None
        self.photo_image: ImageTk.PhotoImage | None = None
        self.crop_box = CropBox(0, 0, 0, 0)
        self.drag_start = None
        self.aspect_ratio: float | None = None
        self.duration: float = 0.0
        self.timeline_var = tk.DoubleVar(value=0.0)
        self.is_playing = False
        self.playback_job: str | None = None
        self._playback_step = 0.5
        self._temp_dir = Path(tempfile.mkdtemp(prefix="video_cropper_"))

        self._build_ui()

    # UI construction -----------------------------------------------------
    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        top_bar = ttk.Frame(container)
        top_bar.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(top_bar, text="Open Video", command=self._choose_video).pack(side=tk.LEFT)
        ttk.Label(top_bar, text="Aspect Preset:").pack(side=tk.LEFT, padx=(10, 4))
        self.aspect_select = ttk.Combobox(top_bar, values=list(ASPECT_PRESETS.keys()), state="readonly")
        self.aspect_select.current(0)
        self.aspect_select.bind("<<ComboboxSelected>>", self._apply_preset)
        self.aspect_select.pack(side=tk.LEFT)
        ttk.Button(top_bar, text="Preview Crop", command=self._preview_crop).pack(side=tk.LEFT, padx=6)
        ttk.Button(top_bar, text="Export Video", command=self._export_video).pack(side=tk.LEFT)

        content = ttk.Frame(container)
        content.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(content)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(left_panel, width=900, height=520, bg="#1c1c1c", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        controls = ttk.Frame(left_panel)
        controls.pack(fill=tk.X, pady=(8, 0))
        self.play_button = ttk.Button(controls, text="Play", command=self._toggle_playback, width=10)
        self.play_button.pack(side=tk.LEFT, padx=(0, 8))
        self.timeline = ttk.Scale(
            controls,
            from_=0.0,
            to=0.0,
            orient=tk.HORIZONTAL,
            variable=self.timeline_var,
            command=self._on_seek,
        )
        self.timeline.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.time_label = ttk.Label(controls, text="0.0s / 0.0s")
        self.time_label.pack(side=tk.LEFT, padx=(8, 0))

        sidebar = ttk.Frame(content, width=280)
        sidebar.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))

        self.info_label = ttk.Label(sidebar, text="Load a video to start", wraplength=240, justify=tk.LEFT)
        self.info_label.pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(sidebar, text="Crop coordinates (px)").pack(anchor=tk.W)
        coords_frame = ttk.Frame(sidebar)
        coords_frame.pack(anchor=tk.W, pady=(4, 8))
        self.x_var = tk.IntVar(value=0)
        self.y_var = tk.IntVar(value=0)
        self.w_var = tk.IntVar(value=0)
        self.h_var = tk.IntVar(value=0)
        for label, var in (("X", self.x_var), ("Y", self.y_var), ("W", self.w_var), ("H", self.h_var)):
            row = ttk.Frame(coords_frame)
            row.pack(anchor=tk.W)
            ttk.Label(row, text=f"{label}:", width=2).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=var, width=10).pack(side=tk.LEFT, padx=(0, 10))

        self.log_box = tk.Text(sidebar, height=12, width=32, state=tk.DISABLED)
        self.log_box.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

    # Event handlers ------------------------------------------------------
    def _choose_video(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Select a video",
            filetypes=[
                ("Video files", ".mp4 .m4v .mov .mpg .mpeg .3gp"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            return
        self.video_path = Path(file_path)
        try:
            self.metadata = probe_video(self.video_path)
            self._update_info()
            self._load_preview_frame()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", str(exc))
            self._log(str(exc))

    def _apply_preset(self, _event=None) -> None:
        preset_name = self.aspect_select.get()
        self.aspect_ratio = ASPECT_PRESETS.get(preset_name)
        if self.current_image and self.aspect_ratio:
            self._set_box_from_ratio(self.aspect_ratio)
            self._draw_canvas()

    def _on_press(self, event):
        if not self.current_image:
            return
        self.drag_start = (event.x, event.y)

    def _on_drag(self, event):
        if not self.drag_start or not self.current_image:
            return
        start_x, start_y = self.drag_start
        end_x, end_y = event.x, event.y
        x0, y0 = min(start_x, end_x), min(start_y, end_y)
        x1, y1 = max(start_x, end_x), max(start_y, end_y)
        self._update_crop_from_canvas(x0, y0, x1, y1)
        self._draw_canvas()

    def _on_release(self, _event):
        self.drag_start = None

    # Core logic ----------------------------------------------------------
    def _load_preview_frame(self) -> None:
        assert self.video_path
        self._load_frame_at(0.0, reset_crop=True)

    def _reset_crop_to_full_frame(self) -> None:
        assert self.current_image
        width, height = self.current_image.size
        self.crop_box = CropBox(0, 0, width, height)
        self._sync_vars()

    def _update_info(self) -> None:
        if not self.metadata:
            return
        duration = float(self.metadata["format"]["duration"])
        self.duration = duration
        width = self.metadata["streams"][0]["width"]
        height = self.metadata["streams"][0]["height"]
        msg = f"Loaded: {self.video_path.name}\n{width}x{height} • {duration:.2f}s"
        self.info_label.config(text=msg)
        self.timeline.configure(to=max(duration, 0.01))
        self.timeline_var.set(0.0)
        self._update_time_label(0.0)

    def _draw_canvas(self) -> None:
        if not self.current_image:
            return
        canvas_width = self.canvas.winfo_width() or 900
        canvas_height = self.canvas.winfo_height() or 520
        image_ratio = self.current_image.width / self.current_image.height
        canvas_ratio = canvas_width / canvas_height
        if image_ratio > canvas_ratio:
            display_width = canvas_width
            display_height = int(canvas_width / image_ratio)
        else:
            display_height = canvas_height
            display_width = int(canvas_height * image_ratio)
        resized = self.current_image.resize((display_width, display_height), Image.Resampling.LANCZOS)
        self.photo_image = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        offset_x = (canvas_width - display_width) // 2
        offset_y = (canvas_height - display_height) // 2
        self.canvas.create_image(offset_x, offset_y, anchor=tk.NW, image=self.photo_image)

        scale_x = display_width / self.current_image.width
        scale_y = display_height / self.current_image.height
        x0 = offset_x + int(self.crop_box.x * scale_x)
        y0 = offset_y + int(self.crop_box.y * scale_y)
        x1 = offset_x + int((self.crop_box.x + self.crop_box.width) * scale_x)
        y1 = offset_y + int((self.crop_box.y + self.crop_box.height) * scale_y)

        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#00e5ff", width=3)
        self.canvas.create_text(x0 + 8, y0 + 12, anchor=tk.W, text=f"{self.crop_box.width}x{self.crop_box.height}", fill="white")

    def _update_crop_from_canvas(self, x0: int, y0: int, x1: int, y1: int) -> None:
        assert self.current_image
        canvas_width = self.canvas.winfo_width() or 900
        canvas_height = self.canvas.winfo_height() or 520
        image_ratio = self.current_image.width / self.current_image.height
        canvas_ratio = canvas_width / canvas_height
        if image_ratio > canvas_ratio:
            display_width = canvas_width
            display_height = int(canvas_width / image_ratio)
        else:
            display_height = canvas_height
            display_width = int(canvas_height * image_ratio)
        offset_x = (canvas_width - display_width) // 2
        offset_y = (canvas_height - display_height) // 2
        scale_x = self.current_image.width / display_width
        scale_y = self.current_image.height / display_height

        x0 = max(offset_x, min(x0, offset_x + display_width))
        y0 = max(offset_y, min(y0, offset_y + display_height))
        x1 = max(offset_x, min(x1, offset_x + display_width))
        y1 = max(offset_y, min(y1, offset_y + display_height))

        x = int((x0 - offset_x) * scale_x)
        y = int((y0 - offset_y) * scale_y)
        width = int((x1 - x0) * scale_x)
        height = int((y1 - y0) * scale_y)

        if self.aspect_ratio:
            width = int(height * self.aspect_ratio)

        width = max(1, min(width, self.current_image.width - x))
        height = max(1, min(height, self.current_image.height - y))

        self.crop_box = CropBox(x, y, width, height)
        self._sync_vars()

    def _sync_vars(self) -> None:
        self.x_var.set(self.crop_box.x)
        self.y_var.set(self.crop_box.y)
        self.w_var.set(self.crop_box.width)
        self.h_var.set(self.crop_box.height)

    def _set_box_from_ratio(self, ratio: float) -> None:
        assert self.current_image
        w, h = self.current_image.size
        base_height = int(w / ratio)
        if base_height <= h:
            width = w
            height = base_height
        else:
            height = h
            width = int(h * ratio)
        x = (w - width) // 2
        y = (h - height) // 2
        self.crop_box = CropBox(x, y, width, height)
        self._sync_vars()

    def _preview_crop(self) -> None:
        if not self.video_path:
            messagebox.showinfo("Select a video", "Please open a video before previewing.")
            return
        preview_out = self._temp_dir / "preview_cropped.png"
        try:
            self._log("Generating preview frame…")
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            from_box = self.crop_box.as_tuple()
            x, y, w, h = from_box
            args = [
                "ffmpeg",
                "-y",
                "-ss",
                "1",
                "-i",
                str(self.video_path),
                "-filter:v",
                f"crop={w}:{h}:{x}:{y}",
                "-vframes",
                "1",
                str(preview_out),
            ]
            import subprocess

            proc = subprocess.run(args, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr)
            img = Image.open(preview_out).convert("RGB")
            self.current_image = img
            self._draw_canvas()
            self._log("Preview updated using cropped frame.")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", str(exc))
            self._log(str(exc))
        finally:
            self.root.config(cursor="")

    def _export_video(self) -> None:
        if not self.video_path:
            messagebox.showinfo("Select a video", "Please open a video before exporting.")
            return
        save_path = filedialog.asksaveasfilename(
            title="Save cropped video",
            defaultextension=".mp4",
            filetypes=[("MP4", ".mp4"), ("All files", "*.*")],
        )
        if not save_path:
            return
        output = Path(save_path)
        self._log(f"Exporting to {output}…")
        thread = threading.Thread(
            target=self._run_export,
            args=(output,),
            daemon=True,
        )
        thread.start()

    def _run_export(self, output: Path) -> None:
        try:
            crop_video(self.video_path, output, self.crop_box.as_tuple(), progress_callback=self._log)
            self._log("Export complete!")
            messagebox.showinfo("Done", f"Saved cropped video to {output}")
        except Exception as exc:  # noqa: BLE001
            self._log(str(exc))
            messagebox.showerror("Error", str(exc))

    def _log(self, text: str) -> None:
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.configure(state=tk.DISABLED)
        self.log_box.see(tk.END)

    def _load_frame_at(self, timestamp: float, *, reset_crop: bool = False) -> None:
        if not self.video_path:
            return
        preview_path = self._temp_dir / "preview.png"
        extract_frame(self.video_path, preview_path, timestamp=timestamp)
        self.current_image = Image.open(preview_path).convert("RGB")
        if reset_crop or self.crop_box.width == 0:
            self._reset_crop_to_full_frame()
        self._draw_canvas()

    def _update_time_label(self, current: float) -> None:
        self.time_label.config(text=f"{current:.1f}s / {self.duration:.1f}s")

    def _on_seek(self, value: str) -> None:
        if not self.video_path:
            return
        timestamp = float(value)
        self._update_time_label(timestamp)
        self._load_frame_at(timestamp)
        if self.is_playing:
            self._stop_playback()

    def _toggle_playback(self) -> None:
        if not self.video_path or self.duration == 0:
            messagebox.showinfo("Select a video", "Please open a video before playing.")
            return
        if self.is_playing:
            self._stop_playback()
        else:
            self.is_playing = True
            self.play_button.config(text="Pause")
            self._schedule_next_frame()

    def _stop_playback(self) -> None:
        self.is_playing = False
        self.play_button.config(text="Play")
        if self.playback_job:
            self.root.after_cancel(self.playback_job)
            self.playback_job = None

    def _schedule_next_frame(self) -> None:
        if not self.is_playing:
            return
        self.playback_job = self.root.after(int(self._playback_step * 1000), self._advance_frame)

    def _advance_frame(self) -> None:
        if not self.is_playing:
            return
        next_time = self.timeline_var.get() + self._playback_step
        if next_time >= self.duration:
            next_time = self.duration
            self._stop_playback()
        self.timeline_var.set(next_time)
        self._update_time_label(next_time)
        self._load_frame_at(next_time)
        if self.is_playing:
            self._schedule_next_frame()


def run() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background="#111")
    style.configure("TLabel", background="#111", foreground="#f5f5f5")
    style.configure("TButton", padding=6)
    app = VideoCropperApp(root)
    root.geometry("1200x640")
    root.mainloop()


if __name__ == "__main__":
    run()
