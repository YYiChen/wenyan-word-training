"""Generate the Windows icon used by the Wenyan quiz application."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "wenyan-word-training.ico"
FONT = Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf")
SCALE = 4
CANVAS = 256 * SCALE


def scaled_box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return tuple(value * SCALE for value in values)  # type: ignore[return-value]


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        scaled_box((8, 8, 248, 248)),
        radius=54 * SCALE,
        fill="#105963",
        outline="#0a3d47",
        width=3 * SCALE,
    )
    draw.rounded_rectangle(
        scaled_box((20, 20, 236, 236)),
        radius=44 * SCALE,
        outline="#3f8587",
        width=2 * SCALE,
    )
    draw.ellipse(scaled_box((178, 28, 220, 70)), fill="#e7b35a")

    font_path = FONT if FONT.exists() else Path(r"C:\Windows\Fonts\msyhbd.ttc")
    font = ImageFont.truetype(str(font_path), 154 * SCALE)
    character = "文"
    bounds = draw.textbbox((0, 0), character, font=font, stroke_width=1 * SCALE)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    text_x = (CANVAS - text_width) // 2 - bounds[0]
    text_y = 105 * SCALE - text_height // 2 - bounds[1]
    draw.text(
        (text_x, text_y),
        character,
        font=font,
        fill="#fff8e8",
        stroke_width=1 * SCALE,
        stroke_fill="#f6e4b8",
    )

    # A small open-book mark keeps the icon identifiable at small sizes.
    draw.line(
        [
            (62 * SCALE, 205 * SCALE),
            (128 * SCALE, 193 * SCALE),
            (194 * SCALE, 205 * SCALE),
        ],
        fill="#e7b35a",
        width=8 * SCALE,
        joint="curve",
    )
    draw.line(
        [(128 * SCALE, 193 * SCALE), (128 * SCALE, 218 * SCALE)],
        fill="#e7b35a",
        width=6 * SCALE,
    )

    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(OUTPUT, format="ICO", sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
    print(OUTPUT)


if __name__ == "__main__":
    main()
