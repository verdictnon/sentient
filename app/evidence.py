"""Short annotated evidence clips cut from the source recording."""

from fractions import Fraction
from pathlib import Path

import av
from PIL import ImageDraw, ImageFont

from app.p1.media import available

MAX_WIDTH = 960
MAX_FPS = 15
TRAIL_MS = 6000


def position(samples, ms):
    """Interpolate the track box between the two samples around a frame."""
    before = None
    after = None
    for sample in samples:
        if sample[0] <= ms:
            before = sample
        else:
            after = sample
            break
    if before is None:
        return after[1] if after and after[0] - ms <= 400 else None
    if after is None or after[0] - before[0] > 1200:
        return before[1] if ms - before[0] <= 400 else None
    ratio = (ms - before[0]) / (after[0] - before[0])
    return [a + (b - a) * ratio for a, b in zip(before[1], after[1])]


def render_clip(video, samples, identity, start_ms, end_ms, moment_ms, path):
    samples = sorted(samples, key=lambda s: s[0])
    width, height = video["frame_dims_px"]
    scale = min(1.0, MAX_WIDTH / width)
    size = (int(width * scale) // 2 * 2, int(height * scale) // 2 * 2)
    fonts = Path(__file__).parent / "assets/fonts"
    font = ImageFont.truetype(str(fonts / "DejaVuSans-Bold.ttf"), max(12, size[0] // 64))
    trail = [(s[0], (s[1][0] + s[1][2]) / 2 * scale, s[1][3] * scale) for s in samples if s[2] == "observed"]
    label = "Track " + str(identity).split(":")[-1]
    with av.open(str(available(video))) as source, av.open(str(path), "w", format="mp4") as output:
        input_stream = source.streams.video[0]
        source.seek(int(start_ms / 1000 / input_stream.time_base), stream=input_stream, backward=True, any_frame=False)
        stream = output.add_stream("libx264", rate=MAX_FPS)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.codec_context.max_b_frames = 0
        stream.options = dict(crf="24", preset="veryfast")
        last_written = None
        for frame in source.decode(input_stream):
            if frame.pts is None:
                continue
            ms = float(frame.pts * frame.time_base * 1000)
            if ms < start_ms:
                continue
            if ms > end_ms:
                break
            if last_written is not None and ms - last_written < 1000 / MAX_FPS - 0.5:
                continue
            last_written = ms
            image = frame.to_image().convert("RGB").resize(size)
            draw = ImageDraw.Draw(image, "RGBA")
            path_points = [(x, y) for t, x, y in trail if ms - TRAIL_MS <= t <= ms]
            if len(path_points) > 1:
                draw.line(path_points, fill=(255, 208, 30, 230), width=3)
            box = position(samples, ms)
            if box:
                scaled = [v * scale for v in box]
                colour = (226, 61, 40, 255) if abs(ms - moment_ms) <= 1200 else (255, 208, 30, 255)
                draw.rectangle(scaled, outline=colour, width=3)
                draw.text((scaled[0], max(0, scaled[1] - font.size - 4)), label, font=font, fill=colour, stroke_width=2, stroke_fill=(13, 22, 40, 255))
            caption = f"{label}   {ms / 1000:.1f} s"
            draw.rectangle((0, size[1] - font.size - 12, draw.textlength(caption, font=font) + 20, size[1]), fill=(13, 22, 40, 214))
            draw.text((10, size[1] - font.size - 8), caption, font=font, fill=(255, 255, 255, 255))
            encoded = av.VideoFrame.from_image(image)
            encoded.pts = int(ms - start_ms)
            encoded.time_base = Fraction(1, 1000)
            for packet in stream.encode(encoded):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
