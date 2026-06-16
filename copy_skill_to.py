#!/usr/bin/env python3

"""
Script to copy common_skill_parts to a specified directory as llassembly-agentic-loop-skill
and overlay a chosen variant on top of it.
"""

import argparse
import shutil
from pathlib import Path

SKILL_DIRNAME = "llassembly-agentic-loop-skill"

VARIANT_BASE_DIR = {
    "llassembly": "asm_skill_parts",
    "python": "python_skill_parts",
    "monty": "monty_skill_parts",
}


def main():
    parser = argparse.ArgumentParser(
        description="Copy common_skill_parts and overlay a variant to a target directory."
    )
    parser.add_argument(
        "variant",
        choices=VARIANT_BASE_DIR.keys(),
        help="The variant to overlay (llassembly, python, monty)",
    )
    parser.add_argument(
        "target_directory",
        type=str,
        help="The directory where the skill will be created",
    )
    args = parser.parse_args()

    # Get script directory and source paths
    script_dir = Path(__file__).parent.resolve()
    common_skill_parts = script_dir / "common_skill_parts"
    variant_base_dir = script_dir / VARIANT_BASE_DIR[args.variant]
    target_dir = Path(args.target_directory).resolve()

    # Validate source directories exist
    if not common_skill_parts.is_dir():
        parser.error(f"common_skill_parts directory not found at {common_skill_parts}")

    if not variant_base_dir.is_dir():
        parser.error(
            f"{VARIANT_BASE_DIR[args.variant]} directory not found at {variant_base_dir}"
        )

    # Validate target directory exists
    if not target_dir.is_dir():
        parser.error(f"Target directory does not exist: {target_dir}")

    # Create the full destination path
    dest_dir = target_dir / SKILL_DIRNAME

    # Check if destination already exists
    if dest_dir.exists():
        response = input(
            f"Destination directory already exists: {dest_dir}. Remove it? (y/n) "
        )
        if response.lower() == "y":
            shutil.rmtree(dest_dir)
        else:
            print("Aborted.")
            return 1

    # Copy common_skill_parts
    print(f"Copying common_skill_parts to {dest_dir}...")
    shutil.copytree(common_skill_parts, dest_dir)

    # Overlay variant
    print(f"Overlaying {VARIANT_BASE_DIR[args.variant]}...")
    for item in variant_base_dir.rglob("*"):
        dest_item = dest_dir / item.relative_to(variant_base_dir)
        if item.is_dir():
            dest_item.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_item)

    print(f"✓ Successfully created skill at: {dest_dir}")
    return 0


if __name__ == "__main__":
    exit(main())
