---
name: llassembly-control-flow
description: >
    Translate a natural language request into a control-flow written in assembly-like code,
    where every macro invokes a named sub-agent and conditional logic (CMP + jumps)
    branches on sub-agent output. Use when generating execution plans that orchestrate sub-agents.
---

# LLAssembly Sub-Agent Orchestration

Write assembly-like code that represents the control flow required to achieve the goal
defined in the request. Strictly follow the requirements defined in each section below.

## 0. Input and Output

**Input:**
A natural language request to translate into assembly code instructions.

**Output:** Valid assembly code based on the definitions in sections [1. Core
Requirements], [2. Assembly Guidelines], [3. Sub-Agent Definition], and [4. Loops,
Conditionals, and Verification].

## 1. Core Requirements
1.1. The control flow must keep working until the goal is **actually achieved**, not
merely until each step has run once. Section [4. Loops, Conditionals, and Verification]
defines the required patterns.

1.2. Only instructions listed in section [2.1 Allowed Instructions] may be used in the
assembly code.

1.3. Every sub-agent the control flow invokes must be declared as a macro (interface only;
never implement its behavior), following section [3. Sub-Agent Definition].
The plan itself never contains the deliverable's content — no file bodies, no source lines.
It invokes at least one sub-agent, and the sub-agents produce the work.

1.4. Conditional jumps and loops may be used. They must be implemented with `CMP` plus
conditional jumps to labels. Never use a label as a `PUSH` operand; labels are jump
targets only.

1.5. Every line carries a comment annotating that line — `section`, `db`, `%macro`,
`%include`, `%define`, `%endmacro` and every `label:` as much as the mnemonics. Never write
a comment that describes work to be implemented later.

1.6. Never use placeholders, simplified stubs, or demo implementations. Produce full,
complete assembly code that satisfies all defined constraints.

1.7. Put string data in a `section .rodata` at the top of the file, one value per `db`
statement in the format `<db_label> db "<string>"`; the `<string>` may be JSON.
- `<db_label>` must be lowercase — uppercase labels cannot be resolved.
- Never use `;` inside a string — it is stripped as a comment.

1.8. This assembly code runs in a custom emulator, not on real hardware. Only the behavior
described in this document is available.

1.9. The assembly control flow must read as a plan that orchestrates sub-agent execution.
The sub-agents themselves provide the actual implementation.

1.10. The stack is emulator bookkeeping and never reaches a sub-agent — each dispatch is
built from the macro's name, objective and outputs alone. Put everything a sub-agent needs
into its `%define OBJECTIVE`. `POP` only into `R<N>`.

## 2. Assembly Guidelines

### 2.1. Allowed Instructions

- `MOV dst, src`
- `PUSH src`
- `POP dst`
- `ADD dst, src`
- `SUB dst, src`
- `CMP a, b`
- `JMP label`
- `JE label`
- `JNE label`
- `JLT label`
- `JLE label`
- `JGT label`
- `JGE label`
- `RET`
- `label:`
- `%macro`
- `%endmacro`
- `%include`
- `%define`
- `db`
- `section`

### 2.2. Registers

- Use `R0` as a throwaway register.
- Use `R1`..`R100` as general-purpose registers.
- Registers hold either integers or strings.
- `OUTPUT_<X>` names are **scoped to the sub-agent that declares them**: two different
  sub-agents may both declare `OUTPUT_1`, and the loop overwrites the `OUTPUT_<X>` slot
  each time a sub-agent runs. To avoid collisions, immediately copy each `OUTPUT_<X>` you
  need into a distinct `R<N>` register right after the macro invocation that produced it,
  then branch on the register — never on `OUTPUT_<X>` directly. Pick a register number not
  used by another sub-agent's result so values survive across later invocations.


## 3. Sub-Agent Definition

Declare every sub-agent that the control flow invokes as a macro whose name is the
sub-agent's name. The macro is an interface/contract only — name, include path, objective
and expected outputs — that wires the sub-agent into the orchestration; the sub-agent
provides its own implementation when run. An **execution loop** runs the sub-agents in the
order and control flow encoded here, carrying state between steps, until the goal is met.
Follow these requirements:

3.1. Determine which sub-agents are needed in the control flow to fulfill the goal.

3.2. For each sub-agent, define a macro using the syntax `%macro agent_<name>` closed with
`%endmacro`.

3.3. Specify the path to the sub-agent definition:
- 3.3.1. Use `%include "global"` **only** when a sub-agent with that exact name is already
  registered and available in the current runtime's own set of defined agents. Do not
  assume a sub-agent exists: treat `global` as valid solely for agents the running tool
  can actually invoke by that name.

- 3.3.2. In every other case — including any sub-agent you are introducing, inferring, or
  that is not confirmed to exist in the current runtime's defined agents — add `%include
  "general/<name>"`. This only references where the sub-agent's specification belongs; it
  does **not** mean you write that specification here. Defining and implementing the
  sub-agent is out of scope for this plan. `<name>` is the macro's own name, verbatim,
  prefix included. The driver locates the definition by macro name, so keep the two
  consistent for the reader's sake.

3.4. Inside the macro, define a one-sentence goal or objective for the sub-agent using
`%define objective "<quote_safe_string>"`. In the objective never mention directly
"llassembly-agentic-workflow" not matter what user request, goal is says.

3.5. When output from a sub-agent is expected, process that output as follows:

- 3.5.1. Inside the macro, declare each output as `%define OUTPUT_<X> "<quote_safe_string
  describing what OUTPUT_X means>"`, where `<X>` is a number from 1 to 10. This
  declaration is the **contract**: the human-readable description of what that output
  means.

- 3.5.2. At run time the execution loop resolves `OUTPUT_<X>` to the sub-agent's actual
  result in that same `OUTPUT_<X>` slot. The name therefore plays
  two roles: a description in the declaration, and the resolved value once the sub-agent
  has run. Never write to `OUTPUT_<X>` — the execution loop sets it; read it once into a
  register (section [3.5.3]) and use that register thereafter. Description of `OUTPUT_<X>`
  must start with "`OUTPUT_<X>` represents ...".

- 3.5.3. `OUTPUT_<X>` names are overwritten on every run, so do **not** compare against
  `OUTPUT_<X>` directly. Instead, **right after each macro invocation in the main control
  flow**, copy every output you need into a distinct general-purpose register with `MOV
  R<N>, OUTPUT_<X>` (where `<N>` is `1..100` and `<X>` is the output number), then branch
  on `R<N>`. Choose an `R<N>` not reused by another sub-agent's result so the value
  survives across later invocations.

3.6. Invoke a sub-agent in the assembly code by referencing its macro name `agent_<name>`
wherever invocation is needed.

3.7. Each macro must be a complete, well-formed **declaration** — a real name, include
path, objective, and accurate output contract — not a placeholder, dummy, or demo.
Completeness here means a faithful interface the execution loop can dispatch to; it does
**not** mean implementing the sub-agent's work.

3.8. Sub-agents that perform real work should expose at least a status output (for example
`OUTPUT_1` = `output_1 represents status: "ok" on success or "error" on failure`) so the
control flow can branch on the result instead of assuming success.

## 4. Loops, Conditionals, and Verification

The execution loop re-invokes sub-agents one at a time, carrying state (registers, flags,
stack, storage).

4.1. **Prefer loops and conditionals over straight-line sequences.** Do not assume a
sub-agent succeeds. After every sub-agent that can fail, `CMP` its status output and
branch.

4.2. **Branch on results.** When a step succeeds, continue; when it fails, either retry
the step or jump to a recovery/fix sub-agent.

4.3. **Add a verification step when the goal needs confirming.** When the request implies
a checkable outcome (code that must run, endpoints that must respond, a file that must
contain something, tests that must pass), declare a dedicated verifier sub-agent macro and
invoke it in the plan. Its declared outputs must report whether the goal is met.

4.4. **Loop until verified, not until run-once.** Structure the plan so that after the
work sub-agents run, the verifier runs, and:
- if the verifier reports success, jump forward to completion;
- if it reports failure, jump back to re-run the work (optionally a targeted fix
  sub-agent) and verify again.

Jumping back to the verifier alone re-asks the same question of unchanged state, and
falling through to the give-up label makes verification fatal instead of corrective. In the
example below the verifier's failure path is the fall-through into `handle_failure`, which
loops to `build_and_verify` — the work label.

4.5. **Bound loop.** Initialize a retry-counter register (for example `MOV R20, 0`),
increment it with `ADD R20, 1` on each iteration, and `CMP` it against a maximum with
`JGE` to break out to an abort/giveup label. This guarantees the plan terminates even when
the goal cannot be reached, and respects the emulator's instruction limit.

Example loop pattern (macro declarations first, then the orchestration that copies each
`OUTPUT_<X>` into a register right after the invocation that produced it):

```
%macro agent_build                 ; Declare the build sub-agent (interface only)
%include "general/build"           ; Where the build sub-agent's specification belongs
%define OBJECTIVE "Build the project artifact from source"  ; One-sentence objective
%define OUTPUT_1 "output_1 represents status: \"ok\" on success or \"error\" on failure"  ; Build status contract
%endmacro                          ; End of build sub-agent declaration

%macro agent_verify                ; Declare the verifier sub-agent (interface only)
%include "general/verify"          ; Where the verifier sub-agent's specification belongs
%define OBJECTIVE "Verify the built artifact passes all checks"  ; One-sentence objective
%define OUTPUT_1 "output_1 represents all_passed: \"true\" if every check passed, else \"false\""  ; Verify result contract
%endmacro                          ; End of verifier sub-agent declaration

   MOV R20, 0                      ; Initialize retry counter to zero
build_and_verify:                  ; Loop head: build the artifact then verify it
   agent_build                     ; Invoke the build sub-agent
   MOV R1, OUTPUT_1                ; Copy build status into R1 before it is overwritten
   CMP R1, "ok"                    ; Compare build status output against success indicator
   JNE handle_failure              ; If build failed, go to the failure/retry handler
   agent_verify                    ; Invoke the verifier sub-agent
   MOV R2, OUTPUT_1                ; Copy verifier result into R2 (distinct register, no collision)
   CMP R2, "true"                  ; Compare verifier pass flag against "true"
   JE done_success                 ; If verification passed, the goal is achieved
                                   ; Otherwise fall through: a failed verify retries the WORK
handle_failure:                    ; Shared handler for build and verify failure: spend one retry
   ADD R20, 1                      ; Increment the retry counter
   CMP R20, 3                      ; Compare retry counter against the maximum of 3 attempts
   JGE done_giveup                 ; If budget exhausted, stop retrying and give up
   JMP build_and_verify            ; Otherwise loop back and rebuild then re-verify
done_success:                      ; Success exit: verifier confirmed the goal
   MOV R10, 0                      ; Set exit code to zero indicating success
   JMP done                        ; Jump to the single completion path
done_giveup:                       ; Give-up exit: retry budget exhausted
   MOV R10, 1                      ; Set exit code to one indicating failure
done:                              ; Single completion label for all exit paths
   RET                             ; Return from main execution reaching completion
```

## 5. Before you write the file

- [ ] Every label named by a `JMP`/`JE`/`JNE`/`JGE` exists in the file, spelled identically.
- [ ] Every exit path reaches the single `done:` label, which ends in `RET`.
- [ ] Every `%macro` a step invokes is declared above it with OBJECTIVE and OUTPUT_n.
- [ ] Every value you `CMP` was first `MOV`ed out of `OUTPUT_n` into a register.
- [ ] No `.rodata` label that no instruction references — a downstream worker will read
      a stray constant as a directive.
