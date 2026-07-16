#!/usr/bin/env python3
"""Render the five-minute walkthrough with aligned narration and steady background music."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGE_PATHS = (
    REPO_ROOT / "docs/diagrams/business-flow.png",
    REPO_ROOT / "docs/diagrams/deployment-runtime.png",
    REPO_ROOT / "docs/evidence/cloud-console.jpg",
    REPO_ROOT / "docs/evidence/cloud-trace.jpg",
    REPO_ROOT / "docs/evidence/evaluation-summary.png",
)
SEGMENT_SECONDS = (46.0, 60.0, 57.0, 58.0, 79.0)
FADE_SECONDS = 0.4
LEADING_SILENCE_SECONDS = 0.45
TRAILING_SILENCE_SECONDS = 0.75
TOTAL_SECONDS = 300.0
BGM_START_SECONDS = 24.0
BGM_CROSSFADE_SECONDS = 5.0
BGM_TARGET_LUFS = -25.4
FINAL_GAIN_DB = -2.6


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def media_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(completed.stdout)["format"]["duration"])


def require_inputs(narration_dir: Path, bgm_source: Path) -> tuple[Path, ...]:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise SystemExit("ffmpeg and ffprobe are required")

    narration_paths = tuple(narration_dir / f"section-{index}.mp3" for index in range(1, 6))
    missing = [
        path for path in (*IMAGE_PATHS, *narration_paths, bgm_source) if not path.is_file()
    ]
    if missing:
        rendered = "\n".join(f"- {path}" for path in missing)
        raise SystemExit(f"Missing walkthrough inputs:\n{rendered}")
    return narration_paths


def render_voice(narration_paths: tuple[Path, ...], output: Path) -> None:
    command = ["ffmpeg", "-y"]
    for path in narration_paths:
        command.extend(["-i", str(path)])

    filters: list[str] = []
    labels: list[str] = []
    for index, (path, segment_seconds) in enumerate(
        zip(narration_paths, SEGMENT_SECONDS, strict=True)
    ):
        duration = media_duration(path)
        spoken_target = (
            segment_seconds - LEADING_SILENCE_SECONDS - TRAILING_SILENCE_SECONDS
        )
        tempo = duration / spoken_target
        fade_out_start = max(0.0, spoken_target - 0.30)
        label = f"voice{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:a]aresample=48000,atempo={tempo:.8f},"
            "afade=t=in:st=0:d=0.08,"
            f"afade=t=out:st={fade_out_start:.6f}:d=0.30,"
            f"adelay={int(LEADING_SILENCE_SECONDS * 1000)},"
            "apad=pad_dur=5,"
            f"atrim=duration={segment_seconds:.6f},asetpts=N/SR/TB[{label}]"
        )

    filters.append(
        f"{''.join(labels)}concat=n=5:v=0:a=1,"
        "highpass=f=70,lowpass=f=14500,"
        "loudnorm=I=-16:TP=-1.5:LRA=7,aresample=48000[voice]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[voice]",
            "-c:a",
            "flac",
            str(output),
        ]
    )
    run(command)


def render_bgm(source: Path, output: Path) -> None:
    # Keep the selected track at its original tempo. Two separately decoded passes avoid the
    # single-input asplit/acrossfade EOF behavior that can truncate the second pass.
    source_duration = media_duration(source)
    usable_seconds = source_duration - BGM_START_SECONDS
    rendered_seconds = 2 * usable_seconds - BGM_CROSSFADE_SECONDS
    if rendered_seconds < TOTAL_SECONDS:
        raise SystemExit(
            f"BGM provides only {rendered_seconds:.1f}s after the two-pass crossfade; "
            f"at least {TOTAL_SECONDS:.1f}s is required"
        )
    filters = (
        "[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=start={BGM_START_SECONDS:.3f},asetpts=N/SR/TB[first];"
        "[1:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=start={BGM_START_SECONDS:.3f},asetpts=N/SR/TB[second];"
        f"[first][second]acrossfade=d={BGM_CROSSFADE_SECONDS:.3f}:c1=tri:c2=tri,"
        f"atrim=duration={TOTAL_SECONDS:.3f},asetpts=N/SR/TB,"
        "highpass=f=55,lowpass=f=15000,"
        "afade=t=in:st=0:d=2,afade=t=out:st=295:d=5,"
        f"loudnorm=I={BGM_TARGET_LUFS}:TP=-3:LRA=8,aresample=48000[bgm]"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-i",
            str(source),
            "-filter_complex",
            filters,
            "-map",
            "[bgm]",
            "-c:a",
            "flac",
            str(output),
        ]
    )


def render_mix(voice: Path, bgm: Path, output: Path) -> None:
    # The music remains at one fixed level. Speech-driven sidechain compression caused audible
    # pumping in continuous narration, so the accepted mix uses no phrase-level automation.
    filters = (
        "[0:a]aformat=sample_fmts=fltp:channel_layouts=mono,"
        "pan=stereo|FL=c0|FR=c0[voice];"
        "[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo[music];"
        "[voice][music]amix=inputs=2:normalize=0:weights='1 1',"
        f"volume={FINAL_GAIN_DB}dB,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=pad_dur=5,atrim=duration={TOTAL_SECONDS:.3f},aresample=48000[mix]"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(voice),
            "-i",
            str(bgm),
            "-filter_complex",
            filters,
            "-map",
            "[mix]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output),
        ]
    )


def render_video(mix: Path, output: Path, work_dir: Path) -> None:
    command = ["ffmpeg", "-y"]
    for image, duration in zip(IMAGE_PATHS, SEGMENT_SECONDS, strict=True):
        command.extend(
            ["-loop", "1", "-framerate", "30", "-t", f"{duration:.3f}", "-i", str(image)]
        )
    command.extend(["-i", str(mix)])

    filters: list[str] = []
    for index, segment_seconds in enumerate(SEGMENT_SECONDS):
        foreground_size = "1920:1080" if index in {0, 1, 4} else "1840:1000"
        fade_out_start = segment_seconds - FADE_SECONDS
        filters.append(
            f"[{index}:v]split=2[bg{index}][fg{index}];"
            f"[bg{index}]scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,gblur=sigma=28,eq=brightness=-0.22:saturation=0.65[bgp{index}];"
            f"[fg{index}]scale={foreground_size}:force_original_aspect_ratio=decrease[fgp{index}];"
            f"[bgp{index}][fgp{index}]overlay=(W-w)/2:(H-h)/2,"
            f"format=yuv420p,setsar=1,trim=duration={segment_seconds:.3f},"
            "fps=30,settb=expr=1/30,setpts=N,"
            f"fade=t=in:st=0:d={FADE_SECONDS:.3f},"
            f"fade=t=out:st={fade_out_start:.3f}:d={FADE_SECONDS:.3f}[v{index}]"
        )

    filters.append("[v0][v1][v2][v3][v4]concat=n=5:v=1:a=0[video]")

    temporary_output = work_dir / "walkthrough-rendered.mp4"
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[video]",
            "-map",
            "5:a:0",
            "-t",
            f"{TOTAL_SECONDS:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "19",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(temporary_output),
        ]
    )
    run(command)
    output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary_output, output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--narration-dir",
        type=Path,
        required=True,
        help="Directory containing section-1.mp3 through section-5.mp3",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "docs/evidence/walkthrough.mp4",
    )
    parser.add_argument(
        "--bgm",
        type=Path,
        required=True,
        help=(
            "Creator-permitted background-music file; attribution remains the caller's "
            "responsibility"
        ),
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Keep intermediate voice, BGM, and mix files in this directory",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    narration_paths = require_inputs(args.narration_dir, args.bgm)

    if args.work_dir:
        work_dir = args.work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="enterprise-walkthrough-")
        work_dir = Path(cleanup.name)

    voice = work_dir / "walkthrough-voice.flac"
    bgm = work_dir / "walkthrough-licensed-bgm.flac"
    mix = work_dir / "walkthrough-mix.m4a"
    render_voice(narration_paths, voice)
    render_bgm(args.bgm.resolve(), bgm)
    render_mix(voice, bgm, mix)
    render_video(mix, args.output.resolve(), work_dir)

    if cleanup is not None:
        cleanup.cleanup()


if __name__ == "__main__":
    main()
