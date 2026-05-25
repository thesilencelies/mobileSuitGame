import cv2
import numpy as np
import argparse
from pathlib import Path


def remove_white_background(input_path: str, threshold: int = 240) -> str:
    img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Could not open: {input_path}")

    if img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)

    b, g, r, a = cv2.split(img)
    white_mask = (r >= threshold) & (g >= threshold) & (b >= threshold)
    a[white_mask] = 0

    result = cv2.merge([b, g, r, a])

    p = Path(input_path)
    output_path = str(p.parent / (p.stem + "_t.png"))
    cv2.imwrite(output_path, result)
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set white background pixels to transparent.")
    parser.add_argument("input", help="Input PNG file or folder of PNG files")
    parser.add_argument("--threshold", type=int, default=240,
                        help="Pixel brightness threshold (0-255, default 240)")
    args = parser.parse_args()

    p = Path(args.input)
    if p.is_dir():
        files = sorted(p.glob("*.png"))
        if not files:
            print("No PNG files found in folder.")
        for f in files:
            if f.stem.endswith("_t"):
                continue
            output = remove_white_background(str(f), args.threshold)
            print(f"Saved: {output}")
    else:
        output = remove_white_background(args.input, args.threshold)
        print(f"Saved: {output}")
