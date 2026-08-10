# `python_skill_parts` — the raw-Python plan variant

This directory is the **python** variant of the LLAssembly skill: a plan is an ordinary Python
script that declares its sub-agents at top level and drives them from a single `def main()`. It is
executed by `scripts/emulator.py` with a plain `exec()` on a background thread.

> Treat every python plan as untrusted code and run this variant only in an environment you have
> secured yourself. 

## What this directory contributes

`python copy_skill_to.py python <target_dir>` copies `common_skill_parts/` (the driver, SKILL.md and
prompt assets) and overlays these two files:

| File | Role |
|---|---|
| `scripts/emulator.py` | Executes the plan; the only variant-specific code. |
| `references/workers/llassembly-control-flow.md` | The spec the model follows when **writing** a plan. |

## How a plan is built

1. **Stage 2 — plan.** The driver tells the orchestrator to launch the `llassembly-control-flow`
   worker, which writes `plan_llassembly.py` following
   `references/workers/llassembly-control-flow.md`. Nothing else in the system writes the plan.
2. **Stage 3 — sub-agents.** `get_sub_agents()` re-execs **only the module body** in an isolated
   namespace with a throwaway queue; every top-level `setup_sub_agent(...)` call registers itself.
   `main()` is never called, so no sub-agent is dispatched by the scan. Each declared sub-agent with
   no definition file yet gets one generated at `references/agents/<name>.md`.
3. **Stage 5 — execute.** On every invocation the driver rebuilds the emulator and replays
   `runtime.log` through `iter_tool_calls()` to reach the current position, then emits the next
   sub-agent. The same plan plus the same recorded answers must always reach the same call — so keep
   plans deterministic.

The plan lives at `$LLASSEMBLY_WORKFLOW_PATH/llassembly/<workflow_id>/plan_llassembly.py`
(base defaults to `/tmp`). A saved skill keeps its copy at `references/plan_llassembly.py`.

## Anatomy of a plan

Registrations at module scope (interface only — never the sub-agent's behaviour), then one `main()`
holding a bounded, verify-driven loop.

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
    attempts, max_attempts, succeeded = 0, 3, False
    while attempts < max_attempts:               # bounded: the loop always terminates
        attempts += 1
        build_result = run_sub_agent("build")    # capture each result distinctly
        if build_result["status"] != "ok":
            continue                             # retry the work
        verify_result = run_sub_agent("verify")
        if verify_result["all_passed"] == "true":
            succeeded = True
            break                                # goal achieved
    return 0 if succeeded else 1
```

The full rules are in `references/workers/llassembly-control-flow.md` — read it before making
structural changes.
