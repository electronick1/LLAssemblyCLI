---
name: llassembly-agentic-workflow
description: >
    Orchestrate agentic workflow to satisfy user request and goal.
    In the current session you run scripts/get_next_instruction.py and precisely follow
    the instructions in its output (stdout). Use for any multi-step task that benefits from
    planned, branching, verifiable orchestration.
---

# LLAssembly agentic workflow

This skill executed in three roles below:

**ORCHESTRATOR** — the session where the user asked you to run this skill. You do not do the
the actual work and you do not write files - you run `scripts/get_next_instruction.py`
— a resumable state machine, called the driver, that prints next instructions to execute.
You perform these instructions, then you run the driver again. Repeat without stopping
until the driver prints that execution is finished.

**WORKER** — an agent the orchestrator spawned at Stage 2 or Stage 3. You write ONE file:
the control-flow plan, or one sub-agent definition file. Then you exit. You never run
`scripts/get_next_instruction.py`.

**SUB-AGENT** — an agent the orchestrator spawned at Stage 5 to perform requested work.
You execute what is requested, write ONE result JSON file based on the JSON schema
specified. Then you exit. You never run `scripts/get_next_instruction.py`.


## Arguments and environment

- `--llassembly-session <id>` — identifies this workflow session; every file and every log
  record is keyed by it. You must type the id the driver gave you on **every** execution,
  written as an argument after the command:
  `python scripts/get_next_instruction.py --llassembly-session <id>`.
- `LLASSEMBLY_WORKFLOW_PATH` — base directory for workflow workspaces, supplied by the
  environment (default `/tmp`). Leave it as you found it; setting it moves the whole
  workflow.


## Execution protocol for worker and sub-agent:

If you are a WORKER or a SUB-AGENT, open the one file your dispatch named, do instructed
scope of work, report / write results and exit. You never perform orchestrator duty.

## Execution protocol for orchestrator:

These stages are the ORCHESTRATOR's protocol. A WORKER or a SUB-AGENT never performs them.

### SPAWN A NEW AGENT

Every instruction given from the driver that says **SPAWN A NEW AGENT** means: use your
agent-spawning tool to start a separate, standalone, independent agent that receives only the
prompt text you write, does the work on its own, and exits  — never you, never a continuation
of an agent that already ran, never a to-do / checklist / task-list entry.

Tool names change. Match that behaviour first, this table second:

| CLI | what to reach for |
| --- | --- |
| Claude Code | the `Agent` tool (older builds named it `Task`); pass a general-purpose subagent type |
| OpenCode | its task/subagent tool; pass the most general subagent type it offers |
| Codex | spawn the agent based on the provided tools |
| pi | spawn the agent based on the provided tools |
| anything else | tool whose own description says it starts a separate agent or subagent |

If you delegated work to a new agent/subagent - you MUST wait for it until it ends.
Never perform any next steps or perform any calls until at least one spawned subagent
is still running.

### Execution

Run `scripts/get_next_instruction.py` (supplying needed arguments) - the driver -
then follow the printed instructions literally and re-run the driver. Never decide
the next step yourself — the driver decides. In case of failure or misbehaviour always
re-run the driver (`scripts/get_next_instruction.py`). **The stages below describe what 
the driver  *may* print — not a checklist to run top-to-bottom.** Always do exactly what
the latest execution printed, then re-run the driver. Stages can repeat, branch, or 
arrive in a different order.

**Stage 1 — setup.** Once, and only once per session, run
`scripts/get_next_instruction.py` from the skill directory with no id; it prints the
`--llassembly-session` id to use. That bare form is the first call of the session and
never appears again. Every later call carries the id:
`python scripts/get_next_instruction.py --llassembly-session <id>`.
A call without the argument never continues a workflow — it creates a new one and
abandons yours. If it asks for anything else, answer in the form it printed.

If the user request asks to save this workflow (e.g. as reusable skill), you must
confirm a short name and pass `--save "<name>" --whoamcli <claude|opencode|codex|pi>` 
on your first run of `scripts/get_next_instruction.py` (with no other arguments). 

After this stage you must type session id yourself on each and every execution of
`scripts/get_next_instruction.py --llassembly-session ...`. Nothing carries it for
you: if you leave it out and the driver starts a new workflow. Copy it character
for character.

**Stage 2 — control-flow.** SPAWN A NEW AGENT that runs the
`references/workers/llassembly-control-flow.md` worker file, so it writes the plan per that
file's guidelines.

Supply the context that worker needs to generate a control-flow, including the prompt from
the driver instructions. Its only job is to write the control-flow plan at the specified
path.

As an orchestrator do not verify or execute the generated workflow plan after the
plan generation is finished.

When the spawned subagent has finished, you as the orchestrator (not the worker) re-run the
driver `scripts/get_next_instruction.py --llassembly-session ...` and follow the
next instructions.

**Stage 3 — sub-agents.** Generate all missing sub-agent definition files by SPAWNING ONE
NEW AGENT (worker) per missing definition file, each running the
`references/workers/llassembly-generate-sub-agents.md` worker file, in a parallel batch
(emit all the spawn calls together in one turn).

Supply the context each worker needs to generate its missing definition file,
including the prompt from the driver instructions, and specify that the only job of that
worker is to write the sub-agent definition file at the specified path.

As an orchestrator do not verify, confirm or execute the generated definition files after
the workers are finished.

When all those spawned agents are finished, you as the orchestrator (not a worker) must re-run the
driver `scripts/get_next_instruction.py --llassembly-session ...` in the orchestration session,
and follow the next instructions.

**Stage 4 — save.** If the user requested the workflow to be saved, this stage
automatically writes a new skill in the current project/workdir (as instructed) and
finishes execution; otherwise it proceeds to stage 5.

**Stage 5 — execute.** For each control-flow step the
`scripts/get_next_instruction.py --llassembly-session ...` driver instructs you how to spawn
the sub-agent that does the actual work, follow these instructions as printed.

Supply the information and context that the sub-agent needs to perform the work, taken from
the driver instructions. Include the prompt from the driver instructions, naming the scope
and the definition file that guides the sub-agent toward the defined objective. As its final
step the sub-agent must write a report in JSON format conforming to the JSON schema and path
given in the driver instructions.

On this stage never mention `llassembly-agentic-workflow` in the context you give a
Stage 5 sub-agent — otherwise infinite recursion may happen. 

As an orchestrator do not verify, check or confirm anything, proceed to the next step
by running `scripts/get_next_instruction.py --llassembly-session ...` until it says
`Execution finished. Goal is achieved.`

After the sub-agent has finished you must re-run the driver
`scripts/get_next_instruction.py --llassembly-session ...`, and follow the
next instructions.


## Core rules you must always follow:

1. As an orchestrator, never perform the actual work yourself — always SPAWN A NEW AGENT:
   a worker at Stage 2 and Stage 3, a sub-agent at Stage 5, following the instructions you
   give it.
2. As an orchestrator, do exactly what the latest execution printed; never skip or
   reorder stages.
3. Every driver call except the very first carries `--llassembly-session <id>`. Dropping
   it does not continue your workflow — it creates a new one. Copy the id from the driver
   output; never retype it from memory.
4. As the orchestrator, stop only when the driver prints a line stating `Execution
   finished`. Every turn you take must contain a call for the next instruction; you never
   decide to stop execution without seeking what the next step is.
5. Path in arguments must be copied exactly as printed in instructions from
   `scripts/get_next_instruction.py`.
6. As the orchestrator, `scripts/get_next_instruction.py` is the only program you execute — no
   other shell scripts, no builds, no tests, no deliverable, and never extra or improper
   arguments. The driver creates its own directories.
7. **Results and artifacts belong to the workers.** Sub-agents write their own result JSON;
   workers write the plan and the agent definitions. If one is missing, wrong, or rejected,
   never repair it — re-run the driver and let it re-dispatch. Never open a write or edit tool
   on the plan, an agent definition, or a result JSON. Never mkdir, rm or touch a path the
   driver printed either; the driver creates and owns them.
8. **Never invent an output.** A result value must have been measured by the sub-agent that
   ran — not guessed, not carried over from a sub-agent's prose reply, not decided for a
   verifier.
9. Whenever the driver names a sub-agent it prints the file that defines it. Supply that path
   to the sub-agent you spawn; as the orchestrator you do not need that definition, only
   the path.
10. The skill, its scripts and its dependencies are not yours to repair — never edit
    anything under the skill directory.
11. A Python traceback exception from `scripts/get_next_instruction.py` is not an instruction.
    Re-running will print the same traceback. Do not repair the workflow, do not write
    the deliverable yourself, and do not keep looping: report the traceback and stop.
    This overrides rule 4.
12. Availability of the `python` executable depends on the project setup. The driver
    prints its commands with the word `python` as a stand-in — substitute the
    interpreter that works in this project (from `uv`, the venv, or one the user named)
    and keep using that same one on every call. Rule 5 governs paths, not the interpreter.
13. Running as the orchestrator: if on stage 5 the driver asks you to spawn the next sub-agent,
    you follow the instructions immediately.
