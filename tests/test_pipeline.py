import subprocess

import pytest

from pipeline.composite import grid, probe, render
from pipeline.demo import create_demo


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 9, 16])
def test_grid_tiles_fit_without_overlap(count):
    tiles = grid(count)
    assert len(tiles) == count
    for index, tile in enumerate(tiles):
        x, y, w, h = (tile[key] for key in ("x", "y", "width", "height"))
        assert x >= 0 and y >= 0 and x + w <= 1280 and y + h <= 720
        assert all(value % 2 == 0 for value in [x, y, w, h])
        for other in tiles[index + 1:]:
            assert x + w <= other["x"] or other["x"] + other["width"] <= x or y + h <= other["y"] or other["y"] + other["height"] <= y


def test_render_real_changing_grids_and_audio(tmp_path):
    output = create_demo(tmp_path)
    data = probe(output)
    assert 7.8 <= float(data["format"]["duration"]) <= 8.3
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"], video["codec_name"]) == (640, 360, "h264")
    assert audio["codec_name"] == "aac"
    # Decode every frame: container metadata alone would miss damaged concatenated segments.
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"], check=True)


def test_pipeline_rejects_invalid_manifest(tmp_path):
    for count in [0, 17]:
        with pytest.raises(ValueError):
            grid(count)
    with pytest.raises(ValueError):
        render({"segments": []}, tmp_path, tmp_path / "out.mp4")
