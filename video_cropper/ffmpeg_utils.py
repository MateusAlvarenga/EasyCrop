"""Helpers for interacting with ffmpeg and ffprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Tuple


def ensure_ffmpeg_available() -> None:
    """Raise a helpful error when ffmpeg is not on PATH."""
    if shutil.which("ffmpeg") is None:
        raise EnvironmentError(
            "ffmpeg is required but was not found on PATH. Install ffmpeg and try again."
        )
    if shutil.which("ffprobe") is None:
        raise EnvironmentError(
            "ffprobe is required but was not found on PATH. Install ffmpeg and try again."
        )


def run_command(args: list[str]) -> subprocess.CompletedProcess:
    """Run a subprocess command and return the completed process."""
    return subprocess.run(args, capture_output=True, text=True, check=False)


def probe_video(video_path: Path) -> Dict[str, Any]:
    """Return basic video metadata using ffprobe."""
    ensure_ffmpeg_available()
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_streams",
            "-print_format",
            "json",
            str(video_path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not probe video: {result.stderr}")
    return json.loads(result.stdout)


def extract_frame(video_path: Path, output_path: Path, timestamp: float = 0.0) -> None:
    """Extract a single frame at the given timestamp for preview purposes."""
    ensure_ffmpeg_available()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(video_path),
        "-vframes",
        "1",
        str(output_path),
    ]
    result = run_command(args)
    if result.returncode != 0:
        raise RuntimeError(f"Could not extract frame: {result.stderr}")


def crop_video(
    video_path: Path,
    output_path: Path,
    crop_box: Tuple[int, int, int, int],
    progress_callback: callable | None = None,
) -> None:
    """Crop the video using ffmpeg with the provided crop box (x, y, width, height)."""
    ensure_ffmpeg_available()
    x, y, width, height = crop_box
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-filter:v",
        f"crop={width}:{height}:{x}:{y}",
        "-c:a",
        "copy",
        str(output_path),
    ]

    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    for line in process.stdout:
        if progress_callback:
            progress_callback(line.strip())
    process.wait()
    if process.returncode != 0:
        raise RuntimeError("Cropping failed. See logs for details.")
