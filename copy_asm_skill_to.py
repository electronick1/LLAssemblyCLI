#!/usr/bin/env python3

"""
Script to copy common_skill_parts to a specified directory as llassembly-agentic-loop-skill
and overlay asm_skill_parts on top of it.
"""

import argparse
import shutil
from pathlib import Path

SKILL_DIRNAME = "llassembly-agentic-loop-skill"


def main():
    parser = argparse.ArgumentParser(
        description="Copy common_skill_parts and overlay asm_skill_parts to a target directory."
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
    asm_skill_parts = script_dir / "asm_skill_parts"
    target_dir = Path(args.target_directory).resolve()

    # Validate source directories exist
    if not common_skill_parts.is_dir():
        parser.error(f"common_skill_parts directory not found at {common_skill_parts}")

    if not asm_skill_parts.is_dir():
        parser.error(f"asm_skill_parts directory not found at {asm_skill_parts}")

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

    # Overlay asm_skill_parts
    print("Overlaying asm_skill_parts...")
    for item in asm_skill_parts.rglob("*"):
        dest_item = dest_dir / item.relative_to(asm_skill_parts)
        if item.is_dir():
            dest_item.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_item)

    print(f"✓ Successfully created skill at: {dest_dir}")
    return 0


if __name__ == "__main__":
    exit(main())
