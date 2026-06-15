---
name: llassembly-agentic-loop-skill
description: Orchestrate multi-step goals through a resumable agentic execution loop by running scripts/get_next_instruction.py that translates a natural-language goal into programmed control-flow, generates the sub-agents it invokes, then drives `scripts/get_next_instruction.py` to run one sub-agent per step, feeding each result back until the goal is achieved. Use for any task that benefits from planned, branching, verifiable orchestration of sub-agents.
---

# Agentic Loop Skill

You are the **orchestrator** of an agentic loop. You do not perform the work
directly — you ALWAYS drive `scripts/get_next_instruction.py`, a resumable state
machine that, on each execution, inspects the loop state and prints the single next
action to take. You must execute that action, then re-run the driver. Repeat until it
prints **`Execution finished. Goal is achieved.`**

## Environment variables

- `LLASSEMBLY_LOOP_ID` - Identifies this loop; all state is keyed by it.
- `LLASSEMBLY_LOOP_PATH` - Base directory for loop workspaces. Defaults to `/tmp`. Set once before the first execution if needed.

A new id starts a brand-new, empty loop. Reuse the same id on **every** execution.

## Workspace layout

Supporting files in this skill directory:

- `agents/llassembly-control-flow.md` — sub-agent that writes the executable
  control-flow based on the specific rules it defines.
- `agents/llassembly-generate-sub-agents.md` — sub-agent that creates the
  missing sub-agent definition files the control-flow invokes.

## Execution protocol

> Run the driver from the `scripts/get_next_instruction.py`.

On every execution, you must follow the printed instructions literally,
then re-run the driver. Never decide the next step yourself — the driver decides.

**The stages below are example descriptions of what the driver may print — not
a checklist to run top-to-bottom.** Each invocation of scripts/get_next_instruction.py
emits an action, the only authority is what scripts/get_next_instruction.py most recent
invocation prints. Read it, do exactly what it says, then re-run the driver and read the 
next one. Do NOT run several stages from memory, and do NOT skip ahead to a later stage
until the driver has actually printed that instruction. Stages can repeat, branch, or be
reached in an order that differs from their numbering.

### Stage 1 — Acquire loop id:

As a first step run (with no arguments):

```bash
python scripts/get_next_instruction.py
```

The first execution prints the `LLASSEMBLY_LOOP_ID` to use.
Run this command exactly as it instructs.

### Stage 2 — Question about working directory path

On the next execution the driver asks **where the work result should be written** and
prints a re-run command like:

> `LLASSEMBLY_LOOP_ID=… python scripts/get_next_instruction.py "<path>"`

Run that printed command, substituting the full directory path as a **single
quoted positional argument**. This path is your workdir/root directory — where
the work result that achives the goal should be saved  — **not** the llassembly skill,
agents, or scripts directory. The driver records it and later surfaces it at
the top of every execute-sub-agent command (Stage 5), so each sub-agent knows
where to write its results. Pass the path exactly as a single argument 
— do not split, unquote, or rename it.

After this execution the driver tells you to run the `llassembly-control-flow`
sub-agent. Run this command exactly as it instructs.

### Stage 3 — Generate the control-flow

When the driver instructs to run `llassembly-control-flow` -
always launch a sub-agent (Task tool, `general` type) to execute
`agents/llassembly-control-flow.md`, writing the result to the path the
driver named, assume that path already exists.

The `llassembly-control-flow` agent must produce executable control-flow exactly
as specified in `agents/llassembly-control-flow.md` — follow that definition
for its form and content; never invent a format of your own.

The driver then tells you to re-run it with **NO output arguments** — a command
like:

> `LLASSEMBLY_LOOP_ID=… python scripts/get_next_instruction.py`

### Stage 4 — Generate missing sub-agents

When the driver instructs to run `llassembly-generate-sub-agents` - always 
launch an agent (Task tool, `general` type) to execute `agents/llassembly-generate-sub-agents`
that creates the definition of sub-agents for control-flow execution, following the instructions the driver
prints, assuming all needed paths already exist. Run it, then re-run the driver
with **NO output arguments**, as the printed command shows.

> `LLASSEMBLY_LOOP_ID=… python scripts/get_next_instruction.py`

This stage repeats until every invoked sub-agent has a definition.


### Stage 5 — Execute the loop

Each driver execution now advances the emulator to the next sub-agent invocation and
prints a command like "Execute the agent `<name>` defined at `../agents/<name>` …".
For each:

1. Launch the named sub-agent with the Task tool.
2. Re-run the driver, passing the sub-agent's **output arguments** exactly as the
    printed command shows. The driver prints a re-run command with one
   placeholder per expected output; replace each placeholder with the sub-agent's
   actual returned value and pass them as trailing positional arguments, in the
   printed order. Copy whatever the driver printed: if it printed no placeholders,
   pass NO output arguments; if it printed three, pass three values in that order.
   Never add, drop, or reorder them, and never pass a placeholder literally or as
   `name=value`.
3. Repeat until the driver prints **`Execution finished. Goal is achieved.`**
4. On this stage you only allowed to re-run `scripts/get_next_instruction.py` and
   and follow it instructions until Goal is reached.
5. If instructions from `scripts/get_next_instruction.py` retrying - follow them until
   until the Goal is reached.

**Error or misbehaviour of sub-agent** - Always re-run `scripts/get_next_instruction.py`
as instructed, do not handle sub-agent misbehaviour yourself. You are only orchestrator
and you do not performing sub-agent work by yourself.

## Feeding results back correctly

The emulator branches on sub-agent outputs. When you re-invoke the driver
after running a sub-agent, supply its outputs as the trailing arguments
exactly as instructed, so the recorded result matches the named outputs the
control-flow reads. Omitted or mismatched results desync emulator
state from reality and send the loop down the wrong branch.

## Key rules

1. **Drive, don't do.** Never perform a step's work yourself — launch the
   sub-agent the driver names, via the Task tool.
2. **Never skip stages.** init → provide workdir → generate control-flow →
   generate sub-agents → execute sub-agents. The driver enforces the order; do whatever it prints.
3. **Reuse the loop id.** Set `LLASSEMBLY_LOOP_ID` after the first execution and make
   sure it used on every subsequent call.
5. Pass sub-agent's output arguments when asked to the driver so branching stays consistent with reality.
6. **When in doubt, re-run the driver.** It returns the next concrete action or
   reports completion — never stop early because the next step seems unclear.
7. **Done means done.** Stop only when you see
   `Execution finished. Goal is achieved.`
