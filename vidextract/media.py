"""FFmpeg helpers: probing, streaming frame reader, raw-frame encoders."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def ffmpeg_exe() -> str:
    """FFmpeg binary: $VIDEXTRACT_FFMPEG > imageio-ffmpeg (static, has libvpx/x264) > PATH."""
    env = os.environ.get("VIDEXTRACT_FFMPEG")
    if env:
        return env
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg not found: pip install imageio-ffmpeg or set VIDEXTRACT_FFMPEG")
    return found


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    duration: float
    frames: int
    has_audio: bool
    video_codec: str


def probe(path: str | Path) -> VideoInfo:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    err = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise RuntimeError(f"cannot read duration of {path}:\n{err}")
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    vline = next((l for l in err.splitlines() if "Video:" in l), None)
    if vline is None:
        raise RuntimeError(f"no video stream in {path}")
    size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", vline)
    fps_m = re.search(r"(\d+(?:\.\d+)?)\s*fps", vline) or re.search(r"(\d+(?:\.\d+)?)\s*tbr", vline)
    codec = re.search(r"Video:\s*(\w+)", vline)
    fps = float(fps_m[1]) if fps_m else 30.0
    return VideoInfo(
        path=str(path),
        width=int(size[1]),
        height=int(size[2]),
        fps=fps,
        duration=duration,
        frames=int(round(duration * fps)),
        has_audio="Audio:" in err,
        video_codec=codec[1] if codec else "?",
    )


def even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def read_frames(path: str, width: int, height: int, decoder: str | None = None):
    """Yield BGR uint8 frames (height, width, 3) scaled to the requested size."""
    cmd = [ffmpeg_exe(), "-v", "error"]
    if decoder:
        cmd += ["-c:v", decoder]
    cmd += ["-i", str(path), "-vf", f"scale={width}:{height}:flags=area", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = width * height * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def read_frames_at(path: str, indices: list[int], width: int, height: int, alpha: bool = False) -> dict[int, np.ndarray]:
    """Decode specific frame numbers. alpha=True decodes VP9 alpha (BGRA) via libvpx."""
    if not indices:
        return {}
    wanted = sorted(set(indices))
    expr = "+".join(f"eq(n\\,{i})" for i in wanted)
    ch = 4 if alpha else 3
    cmd = [ffmpeg_exe(), "-v", "error"]
    if alpha:
        cmd += ["-c:v", "libvpx-vp9"]
    cmd += [
        "-i", str(path),
        "-vf", f"select='{expr}',scale={width}:{height}",
        "-fps_mode", "vfr",
        "-f", "rawvideo", "-pix_fmt", "bgra" if alpha else "bgr24", "-",
    ]
    out = subprocess.run(cmd, capture_output=True).stdout
    size = width * height * ch
    frames = [np.frombuffer(out[i * size:(i + 1) * size], np.uint8).reshape(height, width, ch)
              for i in range(len(out) // size)]
    return dict(zip(wanted, frames))


class Encoder:
    """Pipe raw frames into an ffmpeg encoder process."""

    PRESETS = {
        # clean plate: plain H.264, plays everywhere
        "h264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        # overlay with alpha: VP9 yuva420p in WebM (Chrome + HyperFrames keep the alpha)
        "vp9a": ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30",
                 "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-auto-alt-ref", "0"],
    }

    def __init__(self, out: str | Path, width: int, height: int, fps: float, preset: str, pix_fmt: str):
        self.path = Path(out)
        self.frame_bytes = width * height * (4 if pix_fmt == "bgra" else 3)
        cmd = [
            ffmpeg_exe(), "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{width}x{height}", "-r", f"{fps}", "-i", "-",
            *self.PRESETS[preset], str(self.path),
        ]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, frame: np.ndarray) -> None:
        self.proc.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self) -> None:
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode(errors="replace")
        if self.proc.wait() != 0:
            raise RuntimeError(f"encoder failed for {self.path}: {err}")


def extract_audio(src: str, out: str | Path) -> None:
    subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "192k", str(out)],
        check=True,
    )
