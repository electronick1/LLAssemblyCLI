---
name: llassembly-control-flow
description: Translate a natural language request into control-flow plan written in a small, pure Python script representing orchestration logic. Where every sub-agent is declared as a BaseSubAgent subclass and running inside an main() entry poin. Use when generating execution plans that orchestrate sub-agents.
---

# Python Sub-Agent Orchestration

Write a small, pure Python script that represents the control flow required to achieve the goal defined in the request. The script orchestrates sub-agents: each sub-agent is declared as a subclass of `BaseSubAgent` at module (global) scope, instantiated, and run, and the plan branches and loops based on the values they return. All orchestration logic lives inside a single mandatory `def main()` entry point. Strictly follow the requirements defined in each section below.

## 0. Input and Output

**Input:**
A natural language request to translate into an orchestration script.

**Output:**
A valid, complete Python script based on the definitions in sections [1. Core Requirements], [2. Script Guidelines], and [3. Sub-Agent Definition] that represents orchestration control-flow plan.

## 1. Core Requirements

1.1. The control flow must keep working until the goal is **actually achieved**, not merely until each step has run once. Prefer loops and conditionals that retry, branch, and re-check over straight-line, run-once sequences. See section [4. Loops, Conditionals, and Verification] for the required patterns.
1.2. Only the language features listed in section [2.1 Allowed Constructs] may be used in the script.
1.3. Declare and instantiate each sub-agent needed for the control flow as a `BaseSubAgent` subclass (interface only; never implement its behavior). Strictly follow section [3. Sub-Agent Definition] when doing so.
1.4. Branch and loop using plain Python conditionals (`if`/`elif`/`else`) and loops (`while`/`for`).
1.5. Never write comments that describe work to be implemented later. Comments must only annotate existing code.
1.6. Never use placeholders. Never produce simplified or demo implementations. Produce a full, complete script that satisfies all defined constraints.
1.7. This script runs in a custom emulator. The emulator behaves according to the rules defined in this document.
1.8. The script **must** define a single `def main()` function as its mandatory entry point. All orchestration (instantiating and running sub-agents, branching, looping) lives inside `main`. The emulator imports the module and then drives `main` itself; the script itself must **not** call `main()`.
1.9. Every sub-agent instantiated and run in the script must also be declared as described in section [3. Sub-Agent Definition].
1.10. The script must read as a plan that orchestrates sub-agent execution. The sub-agents themselves provide the actual implementation.

## 2. Script Guidelines

### 2.1. Allowed Constructs

- Only the **Python standard library** is allowed. Do not import third-party packages.
- **No network calls** of any kind.
- **No file system access** (no reading, writing, opening, or deleting files).
- **No threading, multiprocessing, or any other form of concurrency you introduce yourself.** You may call sub-agents (using .run()) inside `def main()` (see section [3.4]), but you must not spawn threads, processes, or event loops, and you must not call `main()`  — the emulator starts and drives `main` for you.
- The script must be **pure, simple, script-like logic**: variable assignments, conditionals (`if`/`elif`/`else`), loops (`while`/`for`), comparisons, arithmetic, and running sub-agents. Keep it at the level of a straightforward control-flow script.

### 2.2. State

- Hold sub-agent results in ordinary local variables.
- A sub-agent's result may be a string (including json string) or a number, so compare against the matching type.
- Each time a sub-agent runs it returns a fresh result. Capture the result into a distinct, clearly named variable right after the .run() that produced it, then branch on that variable. Reusing the same variable name across different sub-agents overwrites the earlier value, so choose names that keep the values you still need alive across later invocations.

## 3. Sub-Agent Definition

Declare every sub-agent that the control flow invokes by subclassing `BaseSubAgent` at module (global) scope. This is a **plan-only** task: the subclass is an interface/contract (name, objective, and expected outputs) that wires the sub-agent into the orchestration. Do **not** implement the sub-agent's behavior or author its internal logic — the sub-agent provides its own implementation when run.
The sub-agents are run, in the order and control flow encoded by the orchestration, by an **execution loop** that iterates until the caller's goal is met, carrying state between steps.

The example of sub-agent interface defined as the following abstract class:
```python
from abc import ABC, abstractmethod
from typing import Any, ClassVar


class BaseSubAgent(ABC):
    """Abstract base for all sub-agents declared in the control-flow script.

    Sub-classes supply only the contract attributes (name, objective,
    output_spec, existing); they do **not** implement the agent's behavior
    — the execution loop / external tool invokes and runs them.
    """

    # -- class-level contract attributes (filled by each sub-class) ----------
    name: ClassVar[str]           # short, unique identifier for this agent
    objective: ClassVar[str]      # one-sentence goal of the agent
    output_spec: ClassVar[dict[str, str]]  # output-key → human-readable description
    existing: ClassVar[bool]       # True only if a runtime-registered agent by this name exists

    # -- required abstract method for any concrete runner ---------------------
    @abstractmethod
    def run(self) -> dict[str, Any]:
        """Execute this sub-agent and return its result.

        Returns
        -------
        dict[str, Any] # output-key: value
            Where Any is a string (including JSON-encoded), a number, list or a dict mapping  per `output_spec`.
        """
```

Follow these requirements:

3.1. Determine which sub-agents are needed in the control flow to fulfill the goal.
    3.1.1 Learn what agents and skills are available from current **system prompt** and **session context**.
          Do not read configuration files.  To enumerate what is actionable, read your system prompt for
          injected `<agents>` and `<skills>` blocks — that IS the catalog.

3.2. **Import `BaseSubAgent`** from the `sub_agents` module. This is the only symbol you import from `sub_agents`:

```python
from sub_agents import BaseSubAgent
```

3.3. **Declare each sub-agent as a subclass of `BaseSubAgent` at global scope** (never inside `main` or any other function). The class name must start with `Agent` followed by a CamelCase descriptive name. The contract is supplied as **class attributes**:

- `name` (str): the sub-agent's short name.
- `objective` (str): a one-sentence goal or objective for the sub-agent.
- `output_spec` (dict[str, str]): the **output contract** — a mapping from each output key to a human-readable description of what that output means. Use at most ten outputs. This describes what the sub-agent is expected to return so the control flow can branch on it.
- `existing` (bool): set to `True` **only** when a sub-agent with that exact name is already registered and available in the current runtime's own set of defined agents — that is, an agent the running tool can actually invoke by that name. Set to `False` in every other case, including any sub-agent you are introducing, inferring, or that is not confirmed to exist in the current runtime's defined agents. `existing=False` only references where the sub-agent's specification belongs; it does **not** mean you write that specification here. Defining and implementing the sub-agent is out of scope for this plan.

Example declaration (interface only — no behavior implemented):

```python
class AgentBuild(BaseSubAgent):
    name = "build"
    objective = "Build the project artifact from source"
    output_spec = {"status": 'status: "ok" on success or "error" on failure'}
    existing = False
```

3.4. **Run the sub-agent** by instantiating its class with **no arguments** and call it's  `run` method. `run` must be called from within the `def main()` entry point. The call returns the sub-agent's actual result (a string, a number, or a mapping, per its `output_spec`):

```python
from sub_agents import BaseSubAgent

class AgentBuild(BaseSubAgent):
    name = "build"
    objective = "Build the project artifact from source"
    output_spec = {"status": 'status: "ok" on success or "error" on failure'}
    existing = False

def main():
    result = AgentBuild().run()   # invoke the sub-agent and capture its result
```

The emulator imports the module and drives `main`; do not call `main()` yourself, and do not introduce threads, processes, or event loops (see section [2.1]).

3.5. Capture each result you need into a distinct, clearly named local variable right after the .run() that produced it, then branch on that variable (see section [2.2]).

3.6. Each declaration must be a complete, well-formed **interface** — a real name, objective, and accurate output contract — not a placeholder, dummy, or demo. Completeness here means a faithful interface the execution loop can dispatch to; it does **not** mean implementing the sub-agent's work.

3.7. Sub-agents that perform real work should expose at least a status output  so the control flow can branch on the result instead of assuming success.

3.8. Consult the matching reference for role-specific guidance:
- For workers that implement or modify code, run tests/builds, verify a result, or diagnose a
  failure → `references/worker-sub-agents.md`.
- For read-only research/exploration workers that gather information without changing anything
  → `references/research-sub-agents.md`.

## 4. Loops, Conditionals, and Verification

The execution loop re-invokes sub-agents one at a time, carrying state (local variables) between steps.

4.1. **Prefer loops and conditionals over straight-line sequences.** Do not assume a sub-agent succeeds. After every sub-agent that can fail, check its status output and branch.

4.2. **Branch on results.** When a step succeeds, continue; when it fails, either retry the step or run a recovery/fix sub-agent.

4.3. **Add a verification step when the goal needs confirming.** When the request implies a checkable outcome (code that must run, endpoints that must respond, a file that must contain something, tests that must pass), declare a dedicated verifier sub-agent and run it in the plan. Its declared outputs must report whether the goal is met.

4.4. **Loop until verified, not until run-once.** Structure the plan so that after the work sub-agents run, the verifier runs, and:
- if the verifier reports success, finish;
- if it reports failure, loop back to re-run the work (optionally a targeted fix sub-agent) and verify again.

4.5. **Bound the loop.** Initialize a retry counter (for example `attempts = 0`) per sub-agent or/and per group of sub-agents, increment it on each iteration, and compare it against a maximum so the loop breaks out to an abort/give-up path. This guarantees the plan terminates even when the goal cannot be reached, and respects the emulator's execution limit.

Example loop pattern (declare the sub-agent subclasses at global scope first, then the `def main()` orchestration that captures each result into a clearly named variable right after the call that produced it):

```python
from sub_agents import BaseSubAgent


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

    while attempts < max_attempts:        # loop until verified or budget exhausted
        attempts += 1                     # spend one retry on this iteration
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
