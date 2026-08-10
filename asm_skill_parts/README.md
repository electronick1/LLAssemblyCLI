# `asm_skill_parts` — the assembly-like plan variant

This directory is the **assembly** variant of the LLAssembly skill: plans are written in a small
assembly-like language and executed by a emulator in  `scripts/emulator.py`. The emulator
has no file, network, or syscall instruction — it can only move values, compare them, jump, and
dispatch sub-agents — so a plan cannot do anything but orchestrate.

## What this directory contributes

`python copy_skill_to.py llassembly <target_dir>` copies `common_skill_parts/` (the driver, SKILL.md
and prompt assets) and overlays these two files:

| File | Role |
|---|---|
| `scripts/emulator.py` | Executes the plan; the only variant-specific code. |
| `references/workers/llassembly-control-flow.md` | The spec the model follows when **writing** a plan. |

## What the emulator is

- **Registers** — `eax`…`ebp`, `eip`, `flags`, plus general-purpose `R0`..`R100`. Any name starting
  with `r` is not a real register: it lives in `storage`, alongside `db` labels and sub-agent
  outputs. Values are ints, floats, or strings.
- **Instructions** — `MOV`, `PUSH`, `POP`, `ADD`, `SUB`, `CMP`, `JMP`, `CALL`, `RET`, `db`, and the
  conditional jumps `JE/JNE/JL/JLT/JS/JLE/JG/JGT/JGE/JNS` (`instruction_map`, `JUMP_CONDITIONS`).
- **Sub-agents** — every `%macro agent_<name>` … `%endmacro` block is parsed into a `SubAgent`
  (include path, objective, `OUTPUT_<X>` contract). Writing the macro name as a bare instruction is
  the dispatch.


## How a plan is built

1. **Stage 2 — plan.** The driver tells the orchestrator to launch the `llassembly-control-flow`
   worker, which writes `plan_llassembly.asm` following
   `references/workers/llassembly-control-flow.md`.
2. **Stage 3 — sub-agents.** The driver calls `get_sub_agents()` on the plan and, for every declared
   macro with no definition file yet, has one generated at `references/agents/<macro_name>.md`.
3. **Stage 5 — execute.** On every invocation the driver rebuilds the emulator from the plan file
   and replays `runtime.log` through `iter_tool_calls()` to reach the current position, then emits
   the next sub-agent. The same plan plus the same recorded answers must always reach the same
   instruction — so keep plans deterministic.

The plan lives at `$LLASSEMBLY_WORKFLOW_PATH/llassembly/<workflow_id>/plan_llassembly.asm`
(base defaults to `/tmp`). A saved skill keeps its copy at `references/plan_llassembly.asm`.

## Anatomy of a plan

Declarations first (interface only — never the sub-agent's behaviour), then the orchestration:
a bounded, verify-driven loop.

```asm
%macro agent_build                 ; Declare the build sub-agent (interface only)
%include "general/agent_build"     ; Where the build sub-agent's specification belongs
%define OBJECTIVE "Build the project artifact from source"  ; One-sentence objective
%define OUTPUT_1 "output_1 represents status: ok on success or error on failure"  ; Status contract
%endmacro                          ; End of declaration

   MOV R20, 0                      ; Initialize retry counter
build_and_verify:                  ; Loop head
   agent_build                     ; Invoke the build sub-agent
   MOV R1, OUTPUT_1                ; Copy the status out before the slot is overwritten
   CMP R1, "ok"                    ; Compare against the success indicator
   JNE handle_failure              ; Branch on the result
   JMP done                        ; Goal reached
handle_failure:                    ; Spend one retry
   ADD R20, 1                      ; Increment the counter
   CMP R20, 3                      ; Bound the loop at three attempts
   JGE done                        ; Give up when the budget is exhausted
   JMP build_and_verify            ; Otherwise retry
done:                              ; Single completion label
   RET                             ; Finish
```

The full grammar and the rules each section must satisfy are in
`references/workers/llassembly-control-flow.md` — read it before making structural changes.


