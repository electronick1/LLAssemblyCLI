# Helper: coding

Helper for the `llassembly-generate-sub-agents` worker. Use it when the sub-agent being
defined must make a concrete change to the codebase. The rules that apply to every
sub-agent live in `references/workers/llassembly-generate-sub-agents.md` §5; this file
adds only what is specific to this role.

## When this role applies

The objective describes producing or altering something in the project: writing code,
editing configuration, adding a file, applying a fix a diagnosis sub-agent identified.

## What the definition must specify

- Scope the change to exactly what the inputs describe; do not refactor beyond the task.
- Prefer editing existing files over creating new ones; match the surrounding style.
- Make the change build-aware: write code that would compile/load cleanly. That is a property
  of what you write, not a step you add — never put a compile, import, run or test command in
  the definition.
- Be idempotent: detect whether the change is already present before applying it again — by
  inspecting the file, not by executing it.

## What to report

- Whether the change applied cleanly, so the plan can branch on success or failure.
- Which files it touched.
