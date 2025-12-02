"""Tkinter-based UI for interactive video cropping."""
from __future__ import annotations

import platform
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
import vlc

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
        # Enable normal audio + hardware decoding for smoother playback.
        # Suppress the on-video title overlay and reduce log noise.
        self.vlc_instance = vlc.Instance(
            "--no-video-title-show",
            "--quiet",
        )

        self.media_player: vlc.MediaPlayer | None = None
        # Frame that hosts the VLC video surface.
        self.video_panel: tk.Frame | None = None

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
        ttk.Button(top_bar, text="Save As…", command=self._save_as_video).pack(side=tk.LEFT)
        ttk.Button(top_bar, text="Save", command=self._save_overwrite).pack(side=tk.LEFT, padx=(6, 0))

        content = ttk.Frame(container)
        content.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(content)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Container that holds the VLC video surface plus a transparent
        # Canvas overlay where the crop box is drawn and mouse events are
        # handled.
        video_container = tk.Frame(left_panel, width=900, height=520, bg="#000000")
        video_container.pack(fill=tk.BOTH, expand=True)

        # This frame is the actual render target for VLC.
        self.video_panel = tk.Frame(video_container, bg="#000000")
        self.video_panel.pack(fill=tk.BOTH, expand=True)

        # Canvas placed on top of the video surface; it does not paint a
        # background so the video stays visible underneath, but it draws the
        # crop rectangle and handles mouse interaction.
        self.canvas = tk.Canvas(video_container, highlightthickness=0, bd=0)
        self.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
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
            self._load_media_player()
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

    def _save_as_video(self) -> None:
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

    def _save_overwrite(self) -> None:
        if not self.video_path:
            messagebox.showinfo("Select a video", "Please open a video before exporting.")
            return

        # Ensure VLC is not holding the file handle so Windows allows us
        # to delete/replace the file during overwrite.
        if self.media_player:
            try:
                self.media_player.stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.media_player.release()
            except Exception:  # noqa: BLE001
                pass
            self.media_player = None

        try:
            with tempfile.NamedTemporaryFile(
                suffix=self.video_path.suffix, dir=self.video_path.parent, delete=False
            ) as tmp:
                temp_output = Path(tmp.name)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", f"Unable to create a temporary file: {exc}")
            return

        self._log(f"Saving over {self.video_path.name}…")
        thread = threading.Thread(
            target=self._run_export,
            args=(temp_output,),
            kwargs={"finalize": self._finalize_overwrite},
            daemon=True,
        )
        thread.start()

    def _finalize_overwrite(self, temp_output: Path) -> Path:
        assert self.video_path
        try:
            if self.video_path.exists():
                self.video_path.unlink()
            temp_output.rename(self.video_path)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to replace file: {exc}")
        return self.video_path

    def _reload_after_export(self, path: Path) -> None:
        """Reload the just-exported file back into the player."""
        self.video_path = path
        try:
            self.metadata = probe_video(self.video_path)
            self._load_media_player()
            self._update_info()
            self._load_preview_frame()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", str(exc))
            self._log(str(exc))

    def _run_export(self, output: Path, *, finalize: Callable[[Path], Path] | None = None) -> None:
        try:
            crop_video(self.video_path, output, self.crop_box.as_tuple(), progress_callback=self._log)
            final_path = finalize(output) if finalize else output
            self._log("Export complete!")
            # Schedule UI update on the Tk main thread: reopen the file.
            self.root.after(0, lambda: self._reload_after_export(final_path))
            messagebox.showinfo("Done", f"Saved cropped video to {final_path}")
        except Exception as exc:  # noqa: BLE001
            if output.exists():
                output.unlink(missing_ok=True)
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
        if not self._capture_vlc_snapshot(preview_path, timestamp=timestamp):
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
        if self.is_playing:
            if self.media_player:
                self.media_player.set_time(int(timestamp * 1000))
        self._load_frame_at(timestamp)

    def _toggle_playback(self) -> None:
        if not self.video_path or self.duration == 0:
            messagebox.showinfo("Select a video", "Please open a video before playing.")
            return
        if self.is_playing:
            self._stop_playback()
        else:
            if self.media_player:
                self.media_player.set_time(int(self.timeline_var.get() * 1000))
                self.media_player.play()
            self.is_playing = True
            self.play_button.config(text="Pause")
            self._poll_playback()

    def _stop_playback(self) -> None:
        self.is_playing = False
        self.play_button.config(text="Play")
        if self.media_player:
            self.media_player.pause()
        if self.playback_job:
            self.root.after_cancel(self.playback_job)
            self.playback_job = None

    def _poll_playback(self) -> None:
        if not self.is_playing or not self.media_player:
            return

        current_ms = self.media_player.get_time()
        if current_ms >= 0:
            current = current_ms / 1000
            self.timeline_var.set(current)
            self._update_time_label(current)
            self._capture_vlc_snapshot(self._temp_dir / "preview.png")
            if self.current_image:
                self._draw_canvas()

            if current >= self.duration - 0.05:
                self._stop_playback()
                self.timeline_var.set(self.duration)
                self._update_time_label(self.duration)
                return

        state = self.media_player.get_state()
        if state in (vlc.State.Ended, vlc.State.Error):
            self._stop_playback()
            return

        self.playback_job = self.root.after(int(self._playback_step * 1000), self._poll_playback)

    def _set_media_player_window(self) -> None:
        """Attach VLC video output to an in-app widget instead of a new window."""
        if not self.media_player or not self.video_panel:
            return

        # Ensure the Tk widget has a valid native window handle.
        self.root.update_idletasks()
        handle = self.video_panel.winfo_id()
        system = platform.system()
        try:
            if system == "Windows":
                self.media_player.set_hwnd(handle)
            elif system == "Linux":
                # On X11, winfo_id() returns the XID.
                self.media_player.set_xwindow(handle)
            elif system == "Darwin":
                # On macOS, python-vlc expects an NSView pointer; winfo_id()
                # usually returns a compatible value for Tk widgets.
                self.media_player.set_nsobject(handle)
        except Exception:
            # If binding fails for any reason, fall back to the default
            # behavior (VLC may create its own window).
            pass

    def _load_media_player(self) -> None:
        if self.media_player:
            self.media_player.stop()
        self.media_player = self.vlc_instance.media_player_new()
        self._set_media_player_window()
        media = self.vlc_instance.media_new(str(self.video_path))
        self.media_player.set_media(media)
        self.media_player.play()
        time.sleep(0.1)
        self.media_player.pause()

    def _capture_vlc_snapshot(self, output_path: Path, *, timestamp: float | None = None) -> bool:
        if not self.media_player:
            return False
        try:
            if timestamp is not None:
                self.media_player.set_time(int(timestamp * 1000))
                self.media_player.pause()
                time.sleep(0.05)
            if self.media_player.video_take_snapshot(0, str(output_path), 0, 0) == 0:
                self.current_image = Image.open(output_path).convert("RGB")
                return True
        except Exception:
            return False
        return False


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
