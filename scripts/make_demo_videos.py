"""Generate small synthetic crowd MP4s (browser-playable H.264) into the gallery/ folder.

These are CARTOON people drawn with Pillow, not real footage. They exist so the Camera wall can be exercised
end to end. A real detector such as SAM 3.1 may detect cartoons poorly; use real, licensed footage for any
real evaluation.

Setup (once, inside .venv):   pip install pillow numpy imageio imageio-ffmpeg
Run:                          python scripts/make_demo_videos.py [output_folder]
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw

W, H, FPS, SECONDS = 640, 360, 10, 12
SHIRTS = [(214, 69, 65), (52, 101, 164), (237, 183, 52), (96, 160, 90), (150, 80, 160), (240, 240, 240), (40, 40, 40)]
SKIN = [(241, 205, 170), (198, 143, 102), (141, 94, 66), (94, 62, 44)]


def figure(d: ImageDraw.ImageDraw, x: float, y: float, s: float, shirt, skin, phase: float) -> None:
    """A walking person: head, hair, torso, arms and swinging legs. (x, y) is the point between the feet."""
    leg = 16 * s
    swing = math.sin(phase) * 7 * s
    for side in (-1, 1):
        d.line([(x, y - leg), (x + side * swing, y)], fill=(35, 40, 70), width=max(2, int(5 * s)))
    top = y - leg - 24 * s
    d.rounded_rectangle([x - 8 * s, top, x + 8 * s, y - leg + 2], radius=int(4 * s), fill=shirt)
    for side in (-1, 1):
        d.line([(x + side * 8 * s, top + 4 * s), (x + side * 8 * s - side * swing * 0.6, top + 20 * s)],
               fill=skin, width=max(2, int(4 * s)))
    r = 7 * s
    d.ellipse([x - r, top - 2 * r, x + r, top], fill=skin)
    d.pieslice([x - r, top - 2 * r - 1, x + r, top - 1], 180, 360, fill=(40, 30, 25))


def make(path: Path, entering: bool, density: float, seed: int, label: str) -> None:
    rnd = random.Random(seed)
    people: list[dict] = []
    frames = FPS * SECONDS
    with imageio.get_writer(str(path), fps=FPS, codec="libx264", pixelformat="yuv420p", macro_block_size=1,
                            ffmpeg_params=["-preset", "veryfast", "-crf", "26", "-movflags", "+faststart"]) as out:
        for f in range(frames):
            t = f / FPS
            target = density * (0.6 + 0.4 * math.sin(t / 3))
            if len(people) < target and rnd.random() < 0.5:
                y = rnd.uniform(190, 340)
                people.append({"x": -20.0 if entering else W + 20.0, "y": y, "v": rnd.uniform(30, 60) * (1 if entering else -1),
                               "s": 0.8 + (y - 190) / 150 * 0.9, "shirt": rnd.choice(SHIRTS), "skin": rnd.choice(SKIN),
                               "ph": rnd.uniform(0, 6)})
            for p in people:
                p["x"] += p["v"] / FPS
            people = [p for p in people if -40 < p["x"] < W + 40]
            img = Image.new("RGB", (W, H), (150, 170, 190))
            d = ImageDraw.Draw(img)
            d.rectangle([0, 150, W, H], fill=(120, 120, 125))
            d.rectangle([0, 0, W, 150], fill=(205, 190, 160))
            gx = W - 70 if entering else 0
            d.rectangle([gx, 40, gx + 70, 200], fill=(130, 60, 50))
            for p in sorted(people, key=lambda q: q["y"]):
                figure(d, p["x"], p["y"], p["s"], p["shirt"], p["skin"], t * 9 + p["ph"])
            d.text((8, 8), f"SYNTHETIC CARTOON TEST CLIP - {label}", fill=(20, 20, 20))
            out.append_data(np.asarray(img))


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "gallery"
    folder.mkdir(parents=True, exist_ok=True)
    clips = [("gate_entry_light.mp4", True, 6, 1, "entry, light"), ("gate_entry_busy.mp4", True, 14, 2, "entry, busy"),
             ("gate_exit_light.mp4", False, 6, 3, "exit, light"), ("gate_exit_busy.mp4", False, 14, 4, "exit, busy")]
    for name, entering, density, seed, label in clips:
        make(folder / name, entering, density, seed, label)
        print("wrote", folder / name, (folder / name).stat().st_size // 1024, "KB")
