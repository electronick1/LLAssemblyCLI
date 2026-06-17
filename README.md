# LLAssemblyCLI.

### Reliable, resumable, code-driven orchestration for sub-agents and agentic-loops

 ⚠️  The `stable-monty` branch used only for pre-assembled releases of the skill with a pydantic-monty planner variant.
Actual work happens on the `main` branch.


LLAssemblyCLI is a skill whose defining idea is simple: 
**don't let an LLM improvise the orchestration — compile the orchestration plan into the code and let code drive it.**

1. **LLM compiles** the goal into an explicit control-flow program (`plan.llassembly`) — once.
2. **Runs** that program with a deterministic code driver that dispatches sub-agents one at a
   time, feeds their results back into the program's state, and lets **code** decide the next
   branch, retry, or loop — never an LLM.
3. **Persists** every step to an append-only log so the loop can be paused, inspected, and
   resumed after a crash without losing its place.

LLAssemblyCLI ships with 3 variants of code-driven control-flows:
- Assembly-like based on LLAssembly
- Python with pydantic-monty
- Pure Python

> ⚠️ **Work in progress:** this library is under active development. There may be
> bugs and issues, use it carefully — reports in Issues are appreciated.

## What it is

The common way to orchestrate sub-agents is to put an **orchestrator agent** in
charge: a top-level LLM decides, step by step and in natural language, which
sub-agent to call next, reads the output, then reasons about what to do after
that. The control flow lives inside the model's head. It is flexible, but it is
also **non-deterministic and hard to reproduce** — the same goal can take a
different path on every run, the branching logic is implicit, and long loops
drift as the context window fills with history.

LLAssembly inverts this. Instead of orchestrating sub-agents *with* an
orchestrating agent, the LLM is used **once** to compile the goal into an
explicit **control-flow plan written as code** (`plan.llassembly`). From that
point on, a small, deterministic driver/emulator executes the plan: it advances
to the next sub-agent invocation, asks the harness to run exactly that one
sub-agent, feeds the returned result back into the plan's state, and lets the
**code** — not a model — decide the next branch, retry, or loop. The model only
performs the work at each node; the plan owns the control flow.

This buys two things the orchestrator-agent pattern cannot guarantee:

- **Reliability of the execution plan.** Branches and loops are real code with
  real conditions, not a model's moment-to-moment judgement. The plan is
  written once, reviewed, and then followed faithfully.
- **Reproducible execution.** The same plan plus the same sub-agent results
  produce the same path every time. Execution state is persisted, so a loop can
  be paused, inspected, replayed, or resumed after a crash without losing its
  place.

## Demo

The following shows what happens when you use the skill in Opencode as an example.
The goal in this example is: *"implement the retry logic in `client.py`, run the test suite,
diagnose failures, fix them, and verify all tests pass — up to 5 attempts."*

**1. Install the skill into your OpenCode config dir**

```bash
git clone https://github.com/electronick1/LLAssemblyCLI
cd LLAssemblyCLI
python copy_skill_to.py llassembly ~/.config/opencode/
# ✓ Successfully created skill at: ~/.config/opencode/llassembly-agentic-loop-skill
```

**2. Start an OpenCode session with the skill loaded and state your goal**

```
> Implement retry logic in client.py, run the tests, diagnose and fix failures,
  verify all tests pass — up to 5 attempts.
```

**3. The skill compiles the goal into a control-flow plan**

The orchestrator calls `get_next_instruction.py` and is told to run the
`llassembly-control-flow` sub-agent, which writes `plan.llassembly`:

```asm
%macro agent_implement
%include "general/implement"
%define OBJECTIVE "Implement retry logic in client.py"
%define OUTPUT_1 "status: \"ok\" or \"error\""
%endmacro

%macro agent_test
%include "general/test"
%define OBJECTIVE "Run the test suite and report results"
%define OUTPUT_1 "passed: \"true\" or \"false\""
%define OUTPUT_2 "failure_summary: brief description of failures if any"
%endmacro

%macro agent_diagnose_and_fix
%include "general/diagnose_and_fix"
%define OBJECTIVE "Diagnose test failures and apply fixes"
%define OUTPUT_1 "status: \"ok\" or \"error\""
%endmacro

    MOV R20, 0                  ; initialize retry counter to zero
loop:                           ; loop head: start of retry cycle
    agent_implement             ; invoke implement sub-agent
    MOV R1, OUTPUT_1            ; copy implement result into R1
    CMP R1, "ok"                ; compare implement status against "ok"
    JNE done_fail               ; if implementation failed, jump to failure exit
    agent_test                  ; invoke test sub-agent
    MOV R2, OUTPUT_1            ; copy test result into R2 (distinct register)
    CMP R2, "true"              ; compare test pass flag against "true"
    JE done_success             ; if all tests passed, jump to success exit
    ADD R20, 1                  ; increment retry counter by one
    CMP R20, 5                  ; compare retry counter against max of 5 attempts
    JGE done_fail               ; if retry budget exhausted, jump to failure exit
    agent_diagnose_and_fix      ; invoke diagnose_and_fix sub-agent
    JMP loop                    ; jump back to loop head to retry
done_success:                   ; success exit label
    MOV R10, 0                  ; set exit code to zero (success)
    JMP done                    ; jump to single completion path
done_fail:                      ; failure exit label
    MOV R10, 1                  ; set exit code to one (failure)
done:                           ; single completion label for all exit paths
    RET                         ; return from main execution
```

**4. The driver generates any missing sub-agent definitions**

Before execution begins, the driver scans the plan for every declared sub-agent and checks
whether a definition file already exists. For each one that is missing it runs the
`llassembly-generate-sub-agents` agent, which writes a ready-to-use `.md` definition
into the loop's `agents/` directory.

```
[driver] → missing agent: implement   → running llassembly-generate-sub-agents...
           ✓ agents/implement.md written
[driver] → missing agent: test        → running llassembly-generate-sub-agents...
           ✓ agents/test.md written
[driver] → missing agent: diagnose_and_fix → running llassembly-generate-sub-agents...
           ✓ agents/diagnose_and_fix.md written
[driver] → all agents present, starting execution
```

**5. The driver executes the plan — one sub-agent at a time**

```
[driver] → run sub-agent: implement   (writes retry logic to client.py)
[driver] → run sub-agent: test        (2 failures found)
[driver] → run sub-agent: diagnose_and_fix  (fixes import error + off-by-one)
[driver] → run sub-agent: test        (all tests pass)
[driver] Execution finished. Goal is achieved.
```

Every step is appended to `/tmp/llassembly/<loop-id>/runtime.log` as JSONL. If the session
crashes between any two steps, re-running the driver replays the log and resumes from exactly
where it left off — no work is repeated.


## The concept

The plan is the program. The LLM compiles the goal into it; a code driver
executes it; sub-agents are dispatched one at a time and their results are fed
back so the *code* decides what happens next.

```mermaid
flowchart TD
    goal["Natural-language goal"]
    compile["LLM compiles the goal<br/>(used once)"]
    plan["Control-flow plan as CODE<br/>(branches, loops, verification)"]
    driver{"Code driver<br/>advances the plan"}
    dispatch["Dispatch exactly ONE<br/>sub-agent for this step"]
    agent["Sub-agent does the work<br/>(LLM performs the node)"]
    feedback["Result fed back<br/>into plan state"]
    done(["Goal verified — done"])

    goal --> compile --> plan --> driver
    driver -->|next step| dispatch --> agent --> feedback --> driver
    driver -->|goal achieved| done

    classDef code fill:#dff,stroke:#0aa;
    classDef llm fill:#ffd,stroke:#aa0;
    class plan,driver,dispatch,feedback code;
    class compile,agent llm;
```

The yellow nodes are where an LLM is used (compile the plan once, perform the
work at each node). Everything in blue — the plan, the driver, dispatch, and
result feedback — is deterministic code. Control flow never leaves the code.

## Installation

No dependencies needed. Git clone -> run a Python script to copy one of the skill variants into the target dir.

```bash
python copy_skill_to.py llassembly <target_dir>   # build the ASM-emulator variant skill
# or
python copy_skill_to.py python   <target_dir>     # build the Python-planner variant skill
# or
python copy_skill_to.py monty    <target_dir>     # build the Monty variant skill (WIP)
```

The variant you build determines which **emulator/planner** executes the plan. There are
three, and they make different trade-offs.

| Variant | Plan language | Safety | Best for |
|---------|---------------|--------|----------|
| `llassembly` | Assembly-like instruction set | **High** — sandboxed emulator, no file/syscall/network instructions | Small models, CLI tasks |
| `monty` | Plain Python `def main()` | **Medium** — pydantic-monty (Rust-emulation, WIP) | Complex branching with an added safety layer |
| `python` | Plain Python `def main()` | **Low** — raw `exec()`, sandbox required | Complex branching, structured JSON outputs |

### Assembly-based planner variant

Executes Assembly-like plan emitted by LLM in a lightweight emulator with a deliberately
**limited instruction set** (`MOV`, `PUSH`/`POP`, `ADD`/`SUB`, `CMP`,
conditional jumps, `CALL`/`RET`, and a handful of macro/`db` directives). Because
the plan can only express this tiny, well-defined set of operations:

- **No extra hardening layers required.** The emulator (see emulator.py) can only do what its
  instruction table allows — there are no file, syscall, or internet-related
  instructions; mainly: CMP, jumps, numbers/strings manipulation, that are emulated
  in a limited and controlled way, only to connect sub-agents together,
  see: `asm_skill_parts/agents/llassembly-control-flow.md` for more details.
- **Plans stay stable even on small models.** The narrow grammar is easy
  for weak models to emit correctly and to keep consistent across a long loop.
- Emulator is based on [LLAssembly](https://github.com/electronick1/LLAssembly) project.

This plan type shines when you want to run CLI based skills without extra infrastructure,
and it performs quite well even on very small models like qwen3.6:35b.

### Monty-based planner variant

Uses the same Python plan language as the Python variant, but replaces raw
`exec()` with [`pydantic_monty`](https://github.com/pydantic/monty) as
the execution engine.


- `pydantic-monty` does not guarantee 100% safety, since full Python emulation in
  Rust may contain security issues in its implementation and **pydantic-monty project is itself
  a work-in-progress**.
- `pydantic-monty` is a non-stdlib dependency. Before running this variant, ensure `pydantic_monty`
  is available in your Python environment. You can also specify the runtime in your prompt — for example:
  "Use `uv run` to execute Python".

This plan type is well suited when the orchestration logic is complex and
benefits from a full programming language, with an additional layer of
security compared to the YOLO Python variant.

### YOLO Python-based planner variant

Executes **raw Python emitted by the LLM**: the plan is a small script that
declares sub-agents and drives them from a `def main()` entry point,
branching with ordinary `if`/`while` and returning rich values.

- ⚠️ **WARNING!** The Python-based planner runs LLM generated Python code,
  **it must run in a safe sandboxed environment.** Treat the python-based planner code as untrusted code! 
  Running LLM generated code is never safe — and you are solely responsible for securing the
  environment in which it executes.
- **Good for control flows with many branches and JSON outputs.** Full Python
  expressiveness makes complex branching and structured (JSON) sub-agent
  results natural to handle.

This plan type is well suited when the orchestration logic is complex and
benefits from a real programming language, but extra secure infrastructure is
required.


### Configuration

The plan file (`plan.llassembly`) and the append-only `runtime.log` are written
to a llassembly workspace under a base directory: `/tmp/llassembly` by default

To save it elsewhere, set the `LLASSEMBLY_LOOP_PATH` environment variable before
the first execution (e.g. `export LLASSEMBLY_LOOP_PATH=~/.cache`).

## What `plan.llassembly` looks like

Both variants express the **same** kind of plan — declare the sub-agents the
control flow invokes, then orchestrate them with a bounded, verify-driven loop —
in their own language. The plan file is always `plan.llassembly`; the ASM variant
fills it with assembly, the Python variant with a Python script.

### Assembly variant

```asm
%macro agent_build                 ; Declare the build sub-agent (interface only)
%include "general/build"           ; Where the build sub-agent's specification belongs
%define OBJECTIVE "Build the project artifact from source"  ; One-sentence objective
%define OUTPUT_1 "status: \"ok\" on success or \"error\" on failure"  ; Build status contract
%endmacro                          ; End of build sub-agent declaration

%macro agent_verify                ; Declare the verifier sub-agent (interface only)
%include "general/verify"          ; Where the verifier sub-agent's specification belongs
%define OBJECTIVE "Verify the built artifact passes all checks"  ; One-sentence objective
%define OUTPUT_1 "all_passed: \"true\" if every check passed, else \"false\""  ; Verify contract
%endmacro                          ; End of verifier sub-agent declaration

   MOV R20, 0                      ; Initialize retry counter to zero
build_and_verify:                  ; Loop head: build the artifact then verify it
   agent_build                     ; Invoke the build sub-agent
   MOV R1, OUTPUT_1                ; Copy build status into R1 before it is overwritten
   CMP R1, "ok"                    ; Compare build status output against success indicator
   JNE handle_failure              ; If build failed, go to the failure/retry handler
   agent_verify                    ; Invoke the verifier sub-agent
   MOV R2, OUTPUT_1                ; Copy verifier result into R2 (distinct register)
   CMP R2, "true"                  ; Compare verifier pass flag against "true"
   JE done_success                 ; If verification passed, the goal is achieved
handle_failure:                    ; Failure handler: spend one retry if budget allows
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


### Python variant

```python
# Declare the build sub-agent (interface only — no behavior implemented).
class AgentBuild(BaseSubAgent):
    name = "build"
    objective = "Build the project artifact from source"
    output_spec = {"status": 'status: "ok" on success or "error" on failure'}
    existing = False


# Declare the verifier sub-agent (interface only — no behavior implemented).
class AgentVerify(BaseSubAgent):
    name = "verify"
    objective = "Verify the built artifact passes all checks"
    output_spec = {"all_passed": 'all_passed: "true" if every check passed, else "false"'}
    existing = False


def main():
    attempts = 0          # initialize the retry counter
    max_attempts = 3      # bound the loop so the plan always terminates
    succeeded = False     # track whether the goal was achieved

    while attempts < max_attempts:                # loop until verified or budget exhausted
        attempts += 1                             # spend one retry on this iteration
        build_result = AgentBuild().run()   # invoke the build sub-agent
        build_status = build_result["status"]     # capture build status before reuse
        if build_status != "ok":                  # build failed
            continue                              # retry the build on the next iteration
        verify_result = AgentVerify().run() # invoke the verifier sub-agent
        verify_passed = verify_result["all_passed"]  # capture verifier flag distinctly
        if verify_passed == "true":               # verification confirmed the goal
            succeeded = True                      # record success
            break                                 # the goal is achieved; stop looping
        # verification failed; loop back to rebuild and verify again

    exit_code = 0 if succeeded else 1     # zero on success, one when the budget ran out
    return exit_code
```


## Theory: agent logs, agent loops  — and where LLAssembly wins

Agent logs, loops, and harnesses are the three things any serious agentic system
has to get right. They are also exactly the things the orchestrator-agent
pattern handles weakly, because all three depend on a control flow that is
stable, inspectable, and repeatable. LLAssemblyCLI is built around making each of
them a first-class, code-owned property rather than a side effect of model
reasoning.

### Agent logs

**Where LLAssemblyCLI is good at it.** Every step the loop takes is appended to a
durable, append-only **runtime log** (JSONL, written under a file lock) that
records what was asked, which sub-agent ran, and what it returned. Because the
control flow is *code* and every sub-agent result is logged, the log is not a
narrative — it is a faithful, replayable trace. Re-applying the logged results
to the plan reconstructs the *exact same state*, so a run can be audited
step-by-step, replayed deterministically, or resumed after a crash without
starting over and drifting onto a different path. With an orchestrator agent the
"log" is just chat history, and replaying it does not reproduce the same
decisions; LLAssemblyCLI turns the log into a source of truth precisely because the
plan that consumes it is deterministic.

### Agent loops


**Where LLAssemblyCLI is good at it.** The loop shape is encoded directly in the
plan as ordinary code conditions rather than left to a model's judgement:

- **Verify, don't assume.** After work that can fail, the plan checks a status
  output and branches; a dedicated verifier sub-agent decides whether the goal
  is met.
- **Loop until verified, not run-once.** On failure the plan loops back to redo
  the work and re-verify, instead of marching straight through and hoping.
- **Bounded by construction.** A retry counter compared against a maximum
  guarantees termination even when the goal cannot be reached, and respects the
  emulator's instruction limit.

