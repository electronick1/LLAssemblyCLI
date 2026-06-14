# AGENTS.md

## What this repo is

This repo is **not** a deployable skill — it is a **build kit that assembles one**.
It produces the `llassembly-agentic-loop-skill`, an OpenCode skill that orchestrates
sub-agents via a resumable state machine. There is no app, server, or package; the
only artifact is a skill directory copied into a target location.

## Build / deploy (the only "commands")

```bash
python copy_asm_skill_to.py <target_dir>      # builds the ASM-emulator variant
python copy_python_skill_to.py <target_dir>   # builds the Python-emulator variant
```

Each script copies `common_skill_parts/` to `<target_dir>/llassembly-agentic-loop-skill`,
then **overlays** one variant dir on top (files in the variant win). `<target_dir>` must
already exist; if the destination skill dir exists the script prompts to delete it.

## Layout = composition, not packages

The three source dirs are **fragments merged at build time**, not independent modules:

- `common_skill_parts/` — shared: `SKILL.md`, `scripts/get_next_instruction.py`,
  `agents/llassembly-generate-sub-agents.md`, `references/`.
- `asm_skill_parts/` and `python_skill_parts/` — each supplies the **variant-specific**
  `scripts/emulator.py` and `agents/llassembly-control-flow.md`.

Key consequences for editing:
- `common_skill_parts/` has **no** `emulator.py` and **no** `control-flow.md` — they only
  exist after overlay. Do not "fix" their absence.
- Change shared behavior (the driver, generator agent, references) in `common_skill_parts/`.
- Change plan-language or emulator semantics in the matching `*_skill_parts/` dir — and
  usually in **both** variants if the change is conceptual (they intentionally mirror each
  other: ASM control flow vs. pure-Python `def main()` control flow).

## How the runtime fits together (not obvious from filenames)

- `get_next_instruction.py` is the orchestrator-driven state machine. It dynamically loads
  `emulator.py` **from its own directory** (`_import_emulator` via `importlib`), which is why
  both files must land as siblings in `scripts/` after the overlay copy.
- Both `emulator.py` variants expose the same contract consumed by the driver:
  `Emulator.from_code(text)`, `.get_sub_agents()`, `.iter_tool_calls()`, `.is_finished()`.
  Preserve this interface when editing either variant.
- The plan file is always `plan.llassembly` (`Config.workdir_plan_path`) regardless of
  variant; the ASM variant fills it with assembly, the Python variant with a Python script.
- Loop state lives at `${LLASSEMBLY_LOOP_PATH:-/tmp}/llassembly/<LLASSEMBLY_LOOP_ID>/`
  (`runtime.log` + `plan.llassembly`). `runtime.log` is append-only JSONL under an flock.

## Verifying changes

There is no test suite, linter, or CI. Each `emulator.py` has a `__main__` self-test:

```bash
python python_skill_parts/scripts/emulator.py    # prints declared agents + a sub-agent round-trip
python asm_skill_parts/scripts/emulator.py
```

Run these after touching an emulator. Targets Python 3.14 (uses PEP 695 generics / `Self`;
forward-referenced annotations rely on deferred evaluation). After a real edit, also build to a
temp dir and confirm the overlay produced both `scripts/emulator.py` and
`scripts/get_next_instruction.py`.

## Conventions / gotchas

- Pure standard library only. No third-party deps anywhere, including in any generated plans
  (the Python `control-flow.md` forbids non-stdlib imports, file I/O, network, and self-started
  concurrency).
- `copy_asm_skill_to.py` and `copy_python_skill_to.py` are near-identical; keep them in sync
  when changing copy logic.
