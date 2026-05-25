#!/usr/bin/env python3
"""Generate proxy images for each weapon action using the Replicate API."""

import csv
import os
import sys
import time
import urllib.request

import replicate

CSV_PATH = "Weapon_Prompts.csv"
OUTPUT_DIR = "generated_pictures"
MODEL = "black-forest-labs/flux-2-pro"


def output_path(group: str, name: str) -> str:
    filename = f"{group}_{name}.png".replace(" ", "_")
    return os.path.join(OUTPUT_DIR, filename)


def generate_image(prompt: str) -> bytes:
    output = replicate.run(
        MODEL,
        input={
            "prompt": prompt,
            "output_format": "png",
            "aspect_ratio": "custom",
            "width": 640,
            "height": 890,
        },
    )
    # output is a FileOutput or URL string depending on client version
    if hasattr(output, "read"):
        return output.read()
    url = str(output)
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def main():
    api_key = os.environ.get("REPLICATE_API_TOKEN")
    if not api_key:
        print("Error: REPLICATE_API_TOKEN environment variable not set.")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    for i, row in enumerate(rows, 1):
        group = row["Group"].strip()
        name = row["Name"].strip()
        prompt = row["Prompt"].strip()
        dest = output_path(group, name)

        if os.path.exists(dest):
            print(f"[{i}/{total}] Skipping {dest} (already exists)")
            continue

        print(f"[{i}/{total}] Generating {group}/{name} ...", end=" ", flush=True)
        try:
            data = generate_image(prompt)
            with open(dest, "wb") as out:
                out.write(data)
            print(f"saved ({len(data)//1024} KB)")
            if i < total:
                time.sleep(10)  # 6 requests/min rate limit
        except Exception as exc:
            print(f"FAILED: {exc}")
            time.sleep(10)


if __name__ == "__main__":
    main()
