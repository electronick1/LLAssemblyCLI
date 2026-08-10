#!/usr/bin/env python3

"""
Script to copy common_skill_parts to a specified directory as llassembly-agentic-workflow-skill
and overlay a chosen variant on top of it.
"""

import argparse
import shutil
from pathlib import Path

SKILL_DIRNAME = "llassembly-agentic-workflow"

VARIANT_BASE_DIRS = {
    "llassembly": ["asm_skill_parts"],
    "python": ["python_skill_parts"],
    "monty": ["monty_skill_parts"],
    "mermaid": ["mermaid_skill_parts"],
}

CONTROL_FLOW_REL = "references/workers/llassembly-control-flow.md"

EXCLUDED_FROM_BUILD = {"README.md", "__pycache__"}
VARIANT_BORROWED_FILES = {
    "monty": [("python_skill_parts", CONTROL_FLOW_REL)],
}


def main():
    parser = argparse.ArgumentParser(
        description="Copy common_skill_parts and overlay a variant to a target directory."
    )
    parser.add_argument(
        "variant",
        choices=VARIANT_BASE_DIRS.keys(),
        help=f"The variant to overlay ({', '.join(VARIANT_BASE_DIRS)})",
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
    variant_base_dirs = [script_dir / name for name in VARIANT_BASE_DIRS[args.variant]]
    target_dir = Path(args.target_directory).resolve()

    # Validate source directories exist
    if not common_skill_parts.is_dir():
        parser.error(f"common_skill_parts directory not found at {common_skill_parts}")

    for variant_base_dir in variant_base_dirs:
        if not variant_base_dir.is_dir():
            parser.error(
                f"{variant_base_dir.name} directory not found at {variant_base_dir}"
            )

    # Resolve borrowed files up front: a missing source must abort before anything is
    # written, not leave a half-built skill with no control-flow spec.
    borrowed_files = []
    for source_name, rel_path in VARIANT_BORROWED_FILES.get(args.variant, []):
        source_file = script_dir / source_name / rel_path
        if not source_file.is_file():
            parser.error(
                f"{args.variant} borrows {rel_path} from {source_name}, "
                f"but it was not found at {source_file}"
            )
        borrowed_files.append((source_name, rel_path, source_file))

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
    shutil.copytree(
        common_skill_parts,
        dest_dir,
        ignore=shutil.ignore_patterns(*EXCLUDED_FROM_BUILD),
    )

    # Overlay each variant dir in order; later dirs win on conflict.
    for variant_base_dir in variant_base_dirs:
        print(f"Overlaying {variant_base_dir.name}...")
        for item in variant_base_dir.rglob("*"):
            relative_path = item.relative_to(variant_base_dir)
            # Matched by name at any depth, so an excluded directory takes its contents with
            # it -- rglob yields those independently of the directory itself.
            if EXCLUDED_FROM_BUILD.intersection(relative_path.parts):
                continue
            dest_item = dest_dir / relative_path
            if item.is_dir():
                dest_item.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                dest_item.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest_item)

    # Copy borrowed files last so they win over anything the overlays put there.
    for source_name, rel_path, source_file in borrowed_files:
        print(f"Borrowing {rel_path} from {source_name}...")
        dest_item = dest_dir / rel_path
        dest_item.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, dest_item)

    print(f"✓ Successfully created skill at: {dest_dir}")
    return 0


if __name__ == "__main__":
    exit(main())
