"""Generate real media: 1 → 2 → 4 → 3 participant grids, mixed audio, optional S3 upload."""
import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.composite import probe, render, run


def create_demo(directory: Path, upload=False):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    colors = ["0x2563eb", "0x7c3aed", "0x059669", "0xdc2626"]
    names = ["Alex · Camera", "Sam · Screen share", "Jordan", "Casey"]
    for i, color in enumerate(colors):
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=15",
             "-f", "lavfi", "-i", f"sine=frequency={220 + i * 110}:sample_rate=48000", "-t", "2",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
             str(directory / f"source-{i}.mp4")])
    manifest = {"width": 640, "height": 360, "fps": 15, "segments": [
        {"duration": 2, "videos": [{"path": f"source-{i}.mp4", "name": names[i]} for i in range(count)],
         "audio": [f"source-{i}.mp4" for i in range(count)]} for count in [1, 2, 4, 3]
    ]}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
    output = render(manifest, directory, directory / "demo.mp4")
    print(f"Created {output}")
    if upload:
        from shared.storage import Storage
        metadata = {"id": uuid.uuid4().hex, "channel_name": "Grid demo", "guild_id": "demo", "channel_id": "demo",
                    "started_at": datetime.now(timezone.utc).isoformat(), "participants": names,
                    "duration_seconds": float(probe(output)["format"]["duration"]), "status": "complete"}
        published = Storage().publish(output, metadata)
        print(f"Published /recordings/{published['id']}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("demo-output"))
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    create_demo(args.output_dir, args.upload)
