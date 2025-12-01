# Video Cropper

A lightweight Tkinter desktop tool for Windows that makes it easy to crop common video formats with ffmpeg. The UI borrows familiar ideas from CapCut: open a video, pick a cinematic or social-friendly aspect preset (Reels, TikTok, YouTube, CinemaScope, square), drag the crop box, preview, and export.

## Features

- Supports common formats: `.mp4`, `.m4v`, `.mov`, `.mpg`, `.mpeg`, `.3gp`.
- Drag-to-select crop box with optional aspect presets (CinemaScope 2.39:1, YouTube 16:9, Instagram Reels/TikTok 9:16, Square 1:1, Freeform).
- Quick preview by grabbing a frame after applying the crop filter.
- ffmpeg-powered export that copies audio streams and uses the crop filter for reliable, hardware-independent results.

## Requirements

- Python 3.10+
- ffmpeg and ffprobe available on your PATH (install the static Windows builds or use a package manager like `choco install ffmpeg`).
- Pillow (installed automatically via `pip`).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
```

## Usage

```bash
python -m video_cropper
```

1. **Open Video** and pick a file.
2. Select an **Aspect Preset** (or leave as Freeform).
3. Drag on the preview to set the crop box. You can also type coordinates in the sidebar.
4. Click **Preview Crop** to refresh the preview with the crop applied.
5. Click **Export Video** to render and save.

## Notes

- Export runs in a background thread and streams ffmpeg logs to the sidebar so the UI stays responsive.
- The preview uses a single cropped frame for speed; the export runs the full crop filter on the entire video.
