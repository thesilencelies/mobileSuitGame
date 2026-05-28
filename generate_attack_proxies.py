#!/usr/bin/env python3
"""Generate proxy images for each weapon action using the Replicate API."""

import argparse
import csv
import os
import sys
import time
import urllib.request

import replicate

CSV_PATH = "Weapon_Prompts.csv"
OUTPUT_DIR = "generated_pictures"
MODEL = "black-forest-labs/flux-2-flex"


def output_path(group: str, name: str, output_dir: str) -> str:
    filename = f"{group}_{name}.png".replace(" ", "_")
    return os.path.join(output_dir, filename)


MAX_RETRIES = 3
RETRY_DELAY = 15  # seconds between retries


def _run_replicate(inputs: dict, reference_image: str | None) -> object:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if reference_image:
                with open(reference_image, "rb") as f:
                    inputs["input_images"] = [f]
                    return replicate.run(MODEL, input=inputs)
            return replicate.run(MODEL, input=inputs)
        except Exception as exc:
            if attempt == MAX_RETRIES:
                raise
            print(f"(attempt {attempt} failed: {exc}, retrying in {RETRY_DELAY}s)", flush=True)
            time.sleep(RETRY_DELAY)


def generate_image(prompt: str, reference_image: str | None = None) -> bytes:
    inputs = {
        "prompt": prompt,
        "output_format": "png",
        "aspect_ratio": "custom",
        "width": 640,
        "height": 890,
    }

    output = _run_replicate(inputs, reference_image)
    # output is a FileOutput or URL string depending on client version
    if hasattr(output, "read"):
        return output.read()
    url = str(output)
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def assign_references_by_group(rows: list[dict], refs: list[str]) -> dict[str, str]:
    """Map each unique group (in first-seen order) to a reference image."""
    seen: list[str] = []
    for row in rows:
        g = row["Group"].strip()
        if g not in seen:
            seen.append(g)
    return {g: refs[i % len(refs)] for i, g in enumerate(seen)}


def main():
    parser = argparse.ArgumentParser(description="Generate proxy images via Replicate.")
    parser.add_argument("--csv", default=CSV_PATH, help="Path to prompts CSV")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Output directory")
    parser.add_argument(
        "--reference-images",
        nargs="+",
        metavar="IMAGE",
        help="Reference image paths; cycled over cards/groups",
    )
    parser.add_argument(
        "--ref-mode",
        choices=["group", "card"],
        default="group",
        help="Cycle reference images per group (default) or per card",
    )
    args = parser.parse_args()

    api_key = os.environ.get("REPLICATE_API_TOKEN")
    if not api_key:
        print("Error: REPLICATE_API_TOKEN environment variable not set.")
        sys.exit(1)

    refs = args.reference_images or []
    if refs:
        missing = [r for r in refs if not os.path.exists(r)]
        if missing:
            print(f"Error: reference image(s) not found: {', '.join(missing)}")
            sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    group_ref: dict[str, str] = {}
    if refs and args.ref_mode == "group":
        group_ref = assign_references_by_group(rows, refs)

    total = len(rows)
    for i, row in enumerate(rows, 1):
        group = row["Group"].strip()
        name = row["Name"].strip()
        prompt = row["Prompt"].strip()
        dest = output_path(group, name, args.output_dir)

        if refs:
            if args.ref_mode == "group":
                ref = group_ref[group]
            else:
                ref = refs[(i - 1) % len(refs)]
        else:
            ref = None

        if os.path.exists(dest):
            print(f"[{i}/{total}] Skipping {dest} (already exists)")
            continue

        ref_label = f" (ref: {os.path.basename(ref)})" if ref else ""
        print(f"[{i}/{total}] Generating {group}/{name}{ref_label} ...", end=" ", flush=True)
        try:
            data = generate_image(prompt, ref)
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
