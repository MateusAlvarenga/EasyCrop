"""Core, UI-agnostic logic for the video cropper.

This module contains:
  - Data structures shared across the app
  - Pure helpers for crop-box math
  - Small utilities for formatting metadata

It intentionally has no dependencies on Tkinter, VLC, or other UI layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Tuple


ASPECT_PRESETS: dict[str, float | None] = {
    "Freeform": None,
    "CinemaScope 2.39:1": 2.39,
    "YouTube 16:9": 16 / 9,
    "Instagram Reel 9:16": 9 / 16,
    "TikTok 9:16": 9 / 16,
    "Square 1:1": 1.0,
}


@dataclass
class CropBox:
    """Represents a crop region in pixel coordinates."""

    x: int
    y: int
    width: int
    height: int

    def as_tuple(self) -> Tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height


def full_frame_crop(image_width: int, image_height: int) -> CropBox:
    """Return a crop box that covers the full image."""
    return CropBox(0, 0, image_width, image_height)


def centered_crop_for_ratio(
    image_width: int,
    image_height: int,
    ratio: float,
) -> CropBox:
    """Return a centered crop box that fits the requested aspect ratio."""
    base_height = int(image_width / ratio)
    if base_height <= image_height:
        width = image_width
        height = base_height
    else:
        height = image_height
        width = int(image_height * ratio)

    x = (image_width - width) // 2
    y = (image_height - height) // 2
    return CropBox(x, y, width, height)


def _display_rect(
    image_width: int,
    image_height: int,
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int, int, int]:
    """Compute how the image is letterboxed inside the canvas."""
    image_ratio = image_width / image_height
    canvas_ratio = canvas_width / canvas_height
    if image_ratio > canvas_ratio:
        display_width = canvas_width
        display_height = int(canvas_width / image_ratio)
    else:
        display_height = canvas_height
        display_width = int(canvas_height * image_ratio)

    offset_x = (canvas_width - display_width) // 2
    offset_y = (canvas_height - display_height) // 2
    return display_width, display_height, offset_x, offset_y


def crop_box_from_canvas_drag(
    image_width: int,
    image_height: int,
    canvas_width: int,
    canvas_height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    aspect_ratio: float | None,
) -> CropBox:
    """Translate a drag rectangle on the canvas into an image-space crop box.

    The logic mirrors the original method in the Tkinter app but is kept
    independent from any widget APIs.
    """
    display_width, display_height, offset_x, offset_y = _display_rect(
        image_width,
        image_height,
        canvas_width,
        canvas_height,
    )

    # Clamp drag coordinates into the displayed image area.
    x0 = max(offset_x, min(x0, offset_x + display_width))
    y0 = max(offset_y, min(y0, offset_y + display_height))
    x1 = max(offset_x, min(x1, offset_x + display_width))
    y1 = max(offset_y, min(y1, offset_y + display_height))

    scale_x = image_width / display_width
    scale_y = image_height / display_height

    x = int((x0 - offset_x) * scale_x)
    y = int((y0 - offset_y) * scale_y)
    width = int((x1 - x0) * scale_x)
    height = int((y1 - y0) * scale_y)

    if aspect_ratio:
        width = int(height * aspect_ratio)

    width = max(1, min(width, image_width - x))
    height = max(1, min(height, image_height - y))

    return CropBox(x, y, width, height)


def describe_video(
    video_path: Path,
    metadata: Mapping[str, Any],
) -> tuple[str, float]:
    """Return a human-readable info string and duration in seconds."""
    duration = float(metadata["format"]["duration"])
    stream = metadata["streams"][0]
    width = stream.get("width")
    height = stream.get("height")
    msg = f"Loaded: {video_path.name}\n{width}x{height} • {duration:.2f}s"
    return msg, duration

