# `mermaid_skill_parts` — the mermaid flowchart plan variant

This directory is the **mermaid** variant of the LLAssembly skill: the plan is a mermaid flowchart,
and `scripts/emulator.py` walks that graph directly — there is no compilation to an intermediate
language, and free text is never interpreted. Only node shapes, node ids and a closed set of three
edge labels are read; objectives and questions are carried through to the sub-agent verbatim.

## What this directory contributes

`python copy_skill_to.py mermaid <target_dir>` copies `common_skill_parts/` (the driver, SKILL.md and
prompt assets) and overlays these two files:

| File | Role |
|---|---|
| `scripts/emulator.py` | Executes the plan; the only variant-specific code. |
| `references/workers/llassembly-control-flow.md` | The spec the model follows when **writing** a plan. |

## What the emulator is

A parse → validate → bind → walk pipeline:

- **`_parse`** reads the diagram line by line into a `Graph` of `Node`s and `Edge`s. Anything it
  cannot read raises `PlanError` carrying the offending line number.
- **`_validate`** rejects a malformed plan up front: every node must have a shape, exactly one
  `start`, at least one exit, every node reachable from `start` and able to reach an exit, and the
  right edge fan-out for each shape.
- **`_bind`** turns each `[[ ]]` node into a `SubAgent` with an `output_1` status contract, and
  attaches every `{ }` decision to the single sub-agent above it as an extra output key — so a plain
  decision costs no additional sub-agent call.
- **`iter_tool_calls`** walks from `start`, yielding a `SubAgentContext` at each sub-agent or
  question node and choosing the next edge from the answers recorded so far.

## How a plan is built

1. **Stage 2 — plan.** The driver tells the orchestrator to launch the `llassembly-control-flow`
   worker, which writes `plan_llassembly.mmd` following
   `references/workers/llassembly-control-flow.md`. Nothing else in the system writes the plan.
2. **Stage 3 — sub-agents.** `get_sub_agents()` returns everything `_bind` produced; each one with no
   definition file yet gets one generated at `references/agents/<name>.md`.
3. **Stage 5 — execute.** On every invocation the driver rebuilds the emulator from the diagram and
   replays `runtime.log` through `iter_tool_calls()` to reach the current node, then emits the next
   sub-agent. The same diagram plus the same recorded answers must always reach the same node.

The plan lives at `$LLASSEMBLY_WORKFLOW_PATH/llassembly/<workflow_id>/plan_llassembly.mmd`
(base defaults to `/tmp`). A saved skill keeps its copy at `references/plan_llassembly.mmd`.

## Anatomy of a plan

Two sub-agents, one decision, one retry loop:

```
flowchart TD
    start([start]) --> w_tests
    w_tests[[run_tests: Run the project test suite]] --> d_pass
    d_pass{"Did every test pass?"}
    d_pass -- true --> finish
    d_pass -- false --> w_fix
    w_fix[[fix_tests: Fix the failing tests]] --> w_tests
    finish([done])
```

`run_tests` is asked for two things: its status, and whether every test passed — the decision is
answered by the sub-agent that precedes it, at no extra cost.

| Shape | Meaning |
|---|---|
| `start([start])` | Entry point. Exactly one per plan. |
| `done([done])` / `abort([abort])` | Success / failure exits. At least one exit required. |
| `id[[name]]`, `id[[name: objective]]` | Invoke the sub-agent `name`. |
| `id{"Question?"}` | Decision answered by the **preceding** sub-agent — free. |
| `id{{"Question?"}}` | Decision answered by its **own** sub-agent — costs one call. |

Edges: unlabelled `-->` for the next step, `-- true -->` / `-- false -->` for a decision's two
branches, and an optional `-- error -->` off a sub-agent. Nothing else is legal. The full rules are
in `references/workers/llassembly-control-flow.md`.


## What the emulator enforces

- **The first non-blank line must be the `flowchart`/`graph` header** — no title, no prose, no ```
  fence around the diagram in the plan file.
- **Node ids and sub-agent names must match `[a-z][a-z0-9_]*`**, and every id an edge points at must
  be given a shape exactly once — exits included.
- **Never write a number, count or limit on an edge.** `-- "false max 3" -->` is a parse error;
  loop bounds are structural, not written down.
- **Give each decision node its own line.** An arrow on the same line as `{…}` or `{{…}}` adds an
  extra unlabelled edge and the plan is rejected.
- **A decision needs exactly one `true` and one `false` edge**; a sub-agent needs exactly one
  unlabelled edge plus at most one `error` edge; an exit needs none.
- **Each `{ }` decision must have exactly one sub-agent ancestor.** If two could answer it, the plan
  is ambiguous and rejected — reroute the retry edge, or switch to `{{ }}`.
- **At most 10 outputs per sub-agent** (`MAX_OUTPUTS`), i.e. its status plus nine fused decisions.
- **The walk stops after 200 node visits** (`MAX_STEPS`) and the plan is abandoned.
- **A sub-agent reporting `error` with no `error` edge aborts the plan** — that is the intended
  default, so most plans need no `abort` node.
- **Subgraphs are not supported.**
