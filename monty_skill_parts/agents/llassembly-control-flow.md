---
name: llassembly-control-flow
description: Write a small, pure Python script that orchestrates sub-agents to achieve a natural language request, where every sub-agent is registered with setup_sub_agent(...) at top level and invoked with run_sub_agent(...) inside a single main() entry point.
---

# Python Sub-Agent Orchestration

## Overview

Write a small, pure Python script that represents the control flow required to achieve the goal in the request. Each sub-agent is registered once via a top-level `setup_sub_agent(name, objective, output_spec, existing)` call at module (global) scope, then invoked via `run_sub_agent(name)`; the script branches and loops on the values they return. All orchestration lives inside a single mandatory `def main()`. Follow the requirements in each section below.

## 0. Input and Output

**Input:** A natural language request to translate into an orchestration script.

**Output:** A single Python script (syntactically valid Python) to orchestrate sub-agents, nothing but top-level `setup_sub_agent(...)` registration calls and a single `main()` that only *dispatches* to them via `run_sub_agent(...)` (see the OUTPUT CONTRACT above for the exact format).

## 1. Core Requirements

1.1. The control flow must keep working until the goal is **actually achieved**, not merely until each step has run once. Prefer loops and conditionals that retry, branch, and re-check over straight-line sequences (see section [4]).
1.2. Only the constructs listed in section [2.1] may be used.
1.3. Register every sub-agent the control flow needs with `setup_sub_agent(...)` and invoke it with `run_sub_agent(...)` — interface only, never its behavior (see section [3]).
1.4. Branch and loop using plain Python conditionals (`if`/`elif`/`else`) and loops (`while`/`for`).
1.5. Comments must only annotate existing code. Never write comments describing work to implement later.
1.6. Never use placeholders, simplified, or demo implementations. Never define or import `setup_sub_agent` or `run_sub_agent` — the runtime supplies them (see section [3.2]); defining or importing them shadows the runtime functions and breaks execution.
1.7. The script runs in a custom emulator that drives an **execution loop**, re-invoking the script's sub-agents one at a time and carrying state between steps.
1.8. The script **must** define a single `def main()` as its mandatory entry point; all orchestration lives inside it. The emulator imports the module and drives `main` itself — the script must **not** call `main()`.
1.9. The Python script must read as orchestration logic in executable python syntax that dispatches to sub-agents. When invoked the sub-agents provide the actual work deligated to them.

## 2. Script Guidelines

### 2.1. Allowed Constructs

- Only the **Python standard library** — no third-party packages, no packages outside Python builtins.
- **No network calls.**
- **No file system access** (no reading, writing, opening, or deleting files).
- **No concurrency you introduce** (no threads, processes, or event loops). You may invoke sub-agents via `run_sub_agent(name)` inside `def main()` (see section [3.4]), but per section [1.8] never call `main()` yourself.
- Keep it **minimal, ordinary Python**: variable assignments, conditionals, loops, comparisons, arithmetic, and running sub-agents. Every line must be syntactically valid Python — never pseudocode or a prose description of a step.
- No Python typing required.

### 2.2. State

- Hold sub-agent results in ordinary local variables.
- A result may be a string (including a JSON string) or a number, so compare against the matching type.
- Each `run_sub_agent(...)` call returns a fresh result dict. Capture it into a distinct, clearly named variable right after the call that produced it, then branch on that variable. Reusing one name across different sub-agents overwrites earlier values — name them so values you still need stay alive across later invocations.

## 3. Sub-Agent Definition

Register every sub-agent the control flow invokes with one top-level `setup_sub_agent(name, objective, output_spec, existing)` call at module (global) scope — real Python function calls, never a prose list of names and objectives. The four arguments (`name`, `objective`, `output_spec`, `existing`) wire the sub-agent into the orchestration. Do **not** implement the sub-agent's behavior — it provides its own implementation when run, driven by the emulator execution loop.

The runtime provides the two functions below. **This is documentation of the provided functions, not code to write, import, or redefine** — you only call them:

```
setup_sub_agent(                       # provided in your globals; register one agent per call, at top level
    name: str,                         # short, unique identifier
    objective: str,                    # one-sentence goal
    output_spec: dict[str, str],       # output key -> human-readable description
    existing: bool,                    # True only if a runtime-registered agent by this name exists
) -> None

run_sub_agent(name: str)               # provided in your globals; invoke from within main()
    -> dict[str, str | int]            # one entry per output_spec key; value is str (incl. JSON string) or int
```

3.1. Determine which sub-agents the control flow needs.
    3.1.1. Learn what agents and skills are available from your **system prompt** and **session context** — e.g in `<agents>` and `<skills>` blocks. Do not read configuration files.
    3.1.2. This catalog sets the `existing` argument (section [3.3]): a sub-agent whose exact name appears there is `existing=True`; anything you introduce or infer is `existing=False`. A wrongly-`True` agent leaves the loop pointing at a definition that was never generated; an `existing=False` agent triggers generation of its definition file.

3.2. **The runtime adds `setup_sub_agent` and `run_sub_agent` into the script's global namespace before running the script** — there is nothing to import. Simply reference and call them. Do not write `def setup_sub_agent(...)`, `def run_sub_agent(...)`, or any `import` for them or their parts.

3.3. **Register each sub-agent with one top-level `setup_sub_agent(...)` call before `def main()`** (never inside `main` or another function). Pass:

- `name` (str): the sub-agent's short name.
- `objective` (str): a one-sentence goal.
- `output_spec` (dict[str, str]): the **output contract** — each output key mapped to a human-readable description so the control flow can branch on it. At most ten outputs.
- `existing` (bool): `True` **only** when a sub-agent of that exact name is already registered and invocable in the current runtime; `False` in every other case, including anything you introduce or infer. `existing=False` only references where the sub-agent's specification belongs — defining it is out of scope for this script.

3.4. **Run a sub-agent** by calling `run_sub_agent(name)` from within `main()`, passing the same `name` it was registered with. The call returns the sub-agent's actual result dict keyed per its `output_spec`:

```python
setup_sub_agent(
    name="build",
    objective="Build the project artifact from source",
    output_spec={"status": 'status: "ok" on success or "error" on failure'},
    existing=False,
)

def main():
    build_result = run_sub_agent("build")   # invoke and capture the result dict
```

3.5. Capture each result per section [2.2], then branch on it.
3.6. Each declaration must be a complete, well-formed interface — a real name, objective, and accurate output contract — not a placeholder or demo. Completeness means a faithful interface the loop can dispatch to, not implemented work.
3.7. Sub-agents that perform real work should expose at least a status output so the control flow can branch instead of assuming success.
3.8. Consult the matching reference before declaring a worker:
- Workers that implement/modify code, run tests/builds, verify, or diagnose → `references/worker-sub-agents.md`.
- Read-only research/exploration workers → `references/research-sub-agents.md`.
3.9. Declare outputs in `output_spec` that actually used during orchestration.

## 4. Loops, Conditionals, and Verification

The execution loop re-invokes sub-agents one at a time, carrying local-variable state between steps.

4.1. **Prefer loops and conditionals over straight-line sequences.** Do not assume success — after every sub-agent that can fail, check its status output and branch.
4.2. **Branch on results.** On success continue; on failure retry the step or run a recovery/fix sub-agent.
4.3. **Add a verification step when the goal needs confirming.** When the request implies a checkable outcome (code that must run, endpoints that must respond, tests that must pass), declare a dedicated verifier sub-agent whose outputs report whether the goal is met.
4.4. **Loop until verified, not until run-once.** After the work sub-agents run, run the verifier: if it reports success, finish; if failure, loop back to re-run the work (optionally a targeted fix sub-agent) and verify again.
4.5. **Bound the loop.** Every retry loop needs a counter (e.g. `attempts = 0`): initialize it before the loop, increment each iteration, and compare against a maximum to break out to an abort path. Use one counter per loop; give genuinely independent (e.g. nested) loops their own named counters. This guarantees termination and respects the emulator's execution limit.

Example pattern — register sub-agents at global scope, then a `def main()` that captures each result into a clearly named variable right after its call:

```python
setup_sub_agent(
    name="build",
    objective="Build the project artifact from source",
    output_spec={"status": 'status: "ok" on success or "error" on failure'},
    existing=False,
)


setup_sub_agent(
    name="verify",
    objective="Verify the built artifact passes all checks",
    output_spec={"all_passed": 'all_passed: "true" if every check passed, else "false"'},
    existing=False,
)


def main():
    attempts = 0
    max_attempts = 3
    succeeded = False

    while attempts < max_attempts:        # loop until verified or budget exhausted
        attempts += 1
        build_status = run_sub_agent("build")["status"]   # capture build status
        if build_status != "ok":
            continue                                  # retry the build
        verify_passed = run_sub_agent("verify")["all_passed"]  # capture verifier flag
        if verify_passed == "true":
            succeeded = True
            break                                     # goal achieved
        # verification failed; loop back to rebuild and verify

    return 0 if succeeded else 1          # zero on success, one when budget ran out
```
