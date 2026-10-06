"""FFmpeg grid renderer. Each segment contains time-aligned sources for one roster.

Run: python -m pipeline.composite manifest.json output.mp4
Segments are recomposed at participant transitions and concatenated to one H.264/AAC MP4.
"""
import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path


def grid(count: int, width: int = 1280, height: int = 720):
    if not 1 <= count <= 16:
        raise ValueError("Grid supports 1–16 simultaneous tiles")
    columns = math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    tile_width = (width // columns) // 2 * 2
    tile_height = (height // rows) // 2 * 2
    # Center incomplete final rows; all coordinates and sizes are even for yuv420p.
    return [
        {"x": ((width - min(columns, count - (i // columns) * columns) * tile_width) // 2) // 2 * 2
              + (i % columns) * tile_width,
         "y": (i // columns) * tile_height, "width": tile_width, "height": tile_height}
        for i in range(count)
    ]


def run(command):
    subprocess.run(command, check=True)


def render(manifest: dict, root: Path, output: Path):
    width, height, fps = manifest.get("width", 1280), manifest.get("height", 720), manifest.get("fps", 30)
    if (not isinstance(width, int) or not isinstance(height, int) or
            width < 320 or height < 180 or width % 2 or height % 2 or not 1 <= fps <= 60):
        raise ValueError("Use even dimensions >= 320x180 and fps 1–60")
    segments = manifest.get("segments", [])
    if not segments:
        raise ValueError("At least one segment is required")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vc-grid-") as directory:
        work = Path(directory)
        files = []
        for segment_index, segment in enumerate(segments):
            duration = float(segment["duration"])
            if not math.isfinite(duration) or not 0 < duration <= 14400:
                raise ValueError("Segment duration must be between 0 and 14400 seconds")
            videos = segment["videos"]
            tiles = grid(len(videos), width, height)
            command = ["ffmpeg", "-y", "-v", "error", "-filter_complex_threads", "1"]
            for source in videos:
                command += ["-i", str((root / source["path"]).resolve())]
            audio = segment.get("audio", [])
            for source in audio:
                command += ["-i", str((root / source).resolve())]
            if not audio:
                command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            filters = [f"color=c=0x0b1020:s={width}x{height}:r={fps}:d={duration}[base]"]
            for index, (video, tile) in enumerate(zip(videos, tiles)):
                name = work / f"name-{segment_index}-{index}.txt"
                name.write_text(str(video.get("name", f"Participant {index + 1}"))[:100], encoding="utf-8")
                tw, th = tile["width"], tile["height"]
                filters.append(
                    f"[{index}:v]setpts=PTS-STARTPTS,scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                    f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=0x111827,setsar=1,fps={fps},"
                    f"tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration},"
                    f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
                    f"textfile={name}:expansion=none:fontcolor=white:fontsize={max(14, th // 16)}:"
                    f"box=1:boxcolor=black@0.65:boxborderw=8:x=16:y=h-th-16[v{index}]"
                )
                previous = "base" if index == 0 else f"grid{index - 1}"
                filters.append(f"[{previous}][v{index}]overlay=x={tile['x']}:y={tile['y']}:shortest=1[grid{index}]")
            audio_count = max(1, len(audio))
            for index in range(audio_count):
                filters.append(f"[{len(videos) + index}:a]asetpts=PTS-STARTPTS,aresample=48000,"
                               f"aformat=channel_layouts=stereo,apad,atrim=duration={duration}[a{index}]")
            filters.append("".join(f"[a{i}]" for i in range(audio_count)) +
                           f"amix=inputs={audio_count}:duration=longest:normalize=1[audio]")
            target = work / f"segment-{segment_index}.mp4"
            command += ["-filter_complex", ";".join(filters), "-map", f"[grid{len(videos)-1}]", "-map", "[audio]",
                        "-t", str(duration), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(target)]
            run(command)
            files.append(target)
        listing = work / "concat.txt"
        listing.write_text("".join(f"file '{path}'\n" for path in files), encoding="utf-8")
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", "-movflags", "+faststart", str(output)])
    return output


def finalize(raw: Path, output: Path):
    # Live capture already has the dynamic grid; remux into a seekable MP4.
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-map", "0:v:0", "-map", "0:a:0",
         "-c", "copy", "-movflags", "+faststart", str(output)])


def probe(path: Path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(json.loads(args.manifest.read_text()), args.manifest.resolve().parent, args.output)
