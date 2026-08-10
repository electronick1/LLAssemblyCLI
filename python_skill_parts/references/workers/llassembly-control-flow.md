---
name: llassembly-control-flow
description: >
    Write a Python script that orchestrates sub-agents to satisfy a natural language request,
    where every sub-agent is registered with setup_sub_agent(...) at top level and invoked with
    run_sub_agent(...) inside a single main() entry point.
---

# Python Sub-Agent Orchestration

Write a small, pure Python script that represents the control flow required to achieve the
goal defined in the request. Strictly follow the requirements defined in each section below.

## 0. Input and Output

**Input:**
A natural language request to translate into an orchestration script.

**Output:** A single valid Python script: nothing but top-level `setup_sub_agent(...)`
registration calls and one `main()` that dispatches to them via `run_sub_agent(...)`, per
sections [1. Core Requirements], [2. Script Guidelines], [3. Sub-Agent Definition], and
[4. Loops, Conditionals, and Verification].

## 1. Core Requirements

1.1. The control flow must keep working until the goal is **actually achieved**, not merely
until each step has run once. Section [4. Loops, Conditionals, and Verification] defines the
required patterns.

1.2. Stay within the constraints in section [2.1].

1.3. Register every sub-agent with `setup_sub_agent(...)` and invoke it with
`run_sub_agent(...)` — interface only, never its behavior (section [3]).
The plan itself never contains the deliverable's content. It invokes at least one sub-agent,
and the sub-agents produce the work.

1.4. Branch with `if`/`elif`/`else`; loop with `while`/`for`.

1.5. Comments may only annotate existing code, never describe work to implement later.

1.6. Never use placeholders, stubs, or demo implementations. Produce a complete script.

1.7. This script runs in a custom emulator, not as a normal Python program. Only the
behavior described in this document is available.

1.8. The script **must** define a single `def main()` holding all orchestration. The
emulator calls it — never call `main()` yourself, or every sub-agent runs twice.

1.9. The script must read as a plan that orchestrates sub-agents; the sub-agents themselves
provide the implementation.

## 2. Script Guidelines

### 2.1. Allowed Constructs

- **Python standard library only** — no third-party packages. In practice a plan needs no
  imports at all: `setup_sub_agent` and `run_sub_agent` are injected, and orchestration is
  assignments, comparisons, branches and loops. Some variants reject imports outright, so
  leave them out.
- **No network calls** and **no file system access** (no reading, writing, opening, or
  deleting files).
- **No concurrency you introduce** (threads, processes, event loops); dispatching
  sub-agents is not concurrency.
- Keep it **minimal, ordinary Python**: assignments, conditionals, loops, comparisons,
  arithmetic, and sub-agent calls. Every line must be syntactically valid Python, never
  pseudocode or prose. Type annotations are optional.

### 2.2. State

- Hold sub-agent results in ordinary local variables. **Every value is a string** — a
  status like `"ok"`, a flag like `"true"`, a number rendered as `"3"`, or a JSON
  document. Compare against string literals (`if r["status"] == "ok":`), and convert
  explicitly before arithmetic (`if int(r["failures"]) > 0:`).
- Each `run_sub_agent(...)` call returns a fresh dict. Capture it into a distinctly named
  variable right after the call, then branch on that variable — a name reused across
  sub-agents overwrites values you still need.
- A captured result is a branch signal, not a value to forward: `run_sub_agent` takes only a
  name, so nothing the plan holds reaches the next sub-agent. Where later work needs earlier
  content, put that in the later sub-agent's objective and let **it** obtain the content.
  The deliverable gets its own sub-agent; never assemble it in the plan.

## 3. Sub-Agent Definition

Register every sub-agent with one top-level `setup_sub_agent(name, objective, output_spec,
existing)` call at module scope — real function calls, never a prose list of names and
objectives. Do **not** implement the sub-agent's behavior; it supplies its own when run.

The runtime injects these two functions into the script's globals before running it:

```
setup_sub_agent(                       # register one agent per call, at top level
    name: str,                         # short, unique identifier
    objective: str,                    # one-sentence goal
    output_spec: dict[str, str],       # output key -> human-readable description
    existing: bool,                    # True only if a runtime-registered agent by this name exists
) -> None

run_sub_agent(name: str)               # invoke from within main()
    -> dict[str, str]                  # one entry per output_spec key; every value is a string
```

3.1. **Never write `def setup_sub_agent(...)`, `def run_sub_agent(...)`, or any `import`
for them** — there is nothing to import. A script that defines them registers and
dispatches nothing at all, silently; one that imports them fails outright.

3.2. Determine which sub-agents the control flow needs. Learn which agents and skills
already exist from your **system prompt** and **session context** (e.g. `<agents>` and
`<skills>` blocks); do not read configuration files.

3.3. **Register each sub-agent with one top-level `setup_sub_agent(...)` call before `def
main()`**, never inside a function — a registration made inside `main` is invisible when
sub-agent definitions are generated, so the run dispatches to a sub-agent that has none.
Pass:

- `name` (str): the sub-agent's short name.
- `objective` (str): a one-sentence goal.In the objective never mention directly
"llassembly-agentic-workflow" not matter what user request, goal is says.
- `output_spec` (dict[str, str]): the **output contract** — each output key mapped to a
  human-readable description so the control flow can branch on it. At most ten outputs.
- `existing` (bool): `True` **only** when a sub-agent of that exact name appeared in the
  catalog from [3.2] and is invocable now; `False` in every other case, including anything
  you introduce or infer. The catalog is usually empty, so `False` is usually right. Writing
  the definition itself is out of scope for this script.

Always pass all four arguments explicitly, so each contract is readable at a glance.

3.4. **Run a sub-agent** with `run_sub_agent(name)` inside `main()`, using the same `name`
it was registered with. It returns the sub-agent's result dict keyed by `output_spec`;
capture it per [2.2], then branch on it.

3.5. Each declaration must be a complete, well-formed interface — a real name, objective,
and accurate output contract, not a placeholder or demo. Complete means a faithful
interface, not implemented work.

3.6. Sub-agents that perform real work should expose at least a status output, so the
control flow can branch instead of assuming success.

3.7. Consult the matching helper in `references/agent_generator_helpers/` before declaring
a sub-agent:
- implements/modifies code → `coding.md`; decides whether the goal is met →
  `verification.md`
- runs tests/builds → `testing.md`; turns a failure into the next step → `diagnosis.md`
- gathers information read-only → `research.md`

3.8. Only declare outputs in `output_spec` that the orchestration actually reads.

3.9. And read only outputs you declared. `run_sub_agent` returns exactly the keys in that
sub-agent's `output_spec`; anything else is absent, and `.get()` on it quietly takes your
failure branch on every pass. A research sub-agent that declares only a findings key has no
`status` to test — declare one per [3.6], or branch on what it does report.


## 4. Loops, Conditionals, and Verification

The execution loop re-invokes sub-agents one at a time, carrying local state between steps.

4.1. **Prefer loops and conditionals over straight-line sequences.** Do not assume success
— after every sub-agent that can fail, check its status output and branch.

4.2. **Branch on results.** On success continue; on failure retry or run a fix sub-agent.

4.3. **Declare a verifier and loop until it passes, not until run-once.** When the request
implies a checkable outcome (code that must run, endpoints that must respond, tests that
must pass), declare a verifier sub-agent whose outputs report whether the goal is met, and
run it after the work sub-agents: on success finish; on failure loop back, re-run the work
(optionally via a fix sub-agent), and verify again.

4.4. **Bound every loop.** Initialize a counter before the loop (e.g. `attempts = 0`),
increment it each iteration, and compare it against a maximum to break out to an abort
path. Give each independent loop (e.g. nested loops) its own named counter. An unbounded
loop never terminates — the run hangs with no error.

Example pattern — register sub-agents at global scope, then a `def main()` that captures
each result into a clearly named variable right after its call:

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
        build_result = run_sub_agent("build")     # capture the build result dict
        if build_result["status"] != "ok":
            continue                              # retry the build
        verify_result = run_sub_agent("verify")   # capture the verifier result dict
        if verify_result["all_passed"] == "true":
            succeeded = True
            break                                 # goal achieved
        # verification failed; loop back to rebuild and verify

    return 0 if succeeded else 1          # zero on success, one when budget ran out
```
