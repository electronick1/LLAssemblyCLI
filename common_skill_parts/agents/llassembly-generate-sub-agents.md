---
name: llassembly-generate-sub-agents
description: Generate one worker sub-agent definition file that the control-flow plan invokes by name, so the loop can achieve its goal. Used as one step of the execution loop, invoked once per missing worker sub-agent. Before creating anything, this agent REQUIRES checking whether a suitable worker sub-agent already exists and reusing it instead of creating a new one.
---

# Generating a Worker Sub-Agent

## Terminology (read first)

Two different kinds of agent are involved here. Keep them strictly separate:

- **Generator agent** — *you*, the currently running agent defined by this file
  (`llassembly-generate-sub-agents`). The generator agent does not perform the goal's work. Its
  only job is to produce (or reuse) one worker sub-agent definition file.
- **Worker sub-agent** — the agent the generator agent writes a definition for. A worker
  sub-agent is one node of real work that the control-flow plan invokes **by name only**, and
  that the execution loop runs to make progress toward the goal.

Throughout this document, "generator agent" always means the running agent, and "worker
sub-agent" always means the agent being generated. Never use the bare word "sub-agent" on its
own — always qualify it as the generator agent or a worker sub-agent.

## How the generator agent is invoked

The control-flow plan is produced by the orchestration agent. Every step in
it that runs work invokes a worker sub-agent. The execution loop (driven by
`scripts/get_next_instruction.py`) runs those worker sub-agents one at a time, in the order and
branching the plan encodes, carrying state between them until the goal is achieved.

When the loop reaches a worker sub-agent that the plan invokes but **no definition file exists
for yet**, it invokes the generator agent **once for that single missing worker sub-agent**.
The generator agent is therefore single-target: it handles exactly one worker sub-agent per
run, not a batch.

## 0. Inputs and outputs

0.1. **Inputs** — supplied by the loop when it invokes the generator agent:
- `sub_agent_name` — Sub-agent name the control-flow invokes this worker sub-agent by.
- `sub_agent_path` — Sub-agent absolute path where the worker sub-agent definition file must be
  written (e.g. `.../agents/<name>`). The file name is the name the control-flow invokes.
- `sub_agent_objective` — Sub-agent objective/responsibility this worker sub-agent must fulfill toward the goal.

The control-flow plan in `plan.llassembly` is available as context for understanding how the
worker sub-agent is invoked and what the plan branches on.

0.2. **Output** — exactly one worker sub-agent definition file at `sub_agent_path`, following
the contract in section 5 — **unless** reuse succeeds (section 3), in which case no new file is
created.
0.3. **Never** write a placeholder, dummy, simplified, or demo worker sub-agent. The generated
worker sub-agent must be complete and runnable by the execution loop.

## 1. Generation procedure (run once for the single target)

This is a reuse-first procedure: always try to satisfy `sub_agent_name` with an existing worker
sub-agent before creating a new one.

1.1. **Discover existing worker sub-agents** (see section 2). Build the catalog of worker
sub-agents already defined and reachable from the workspace.

1.2. **Reuse check (MANDATORY — reuse before create).** Decide whether an existing worker
sub-agent can fulfill `sub_agent_objective` (see section 3):
- 1.3.1. If the file at `sub_agent_path` already exists and matches the required responsibility
  and input/output contract — **reuse it as-is**. Do not overwrite it.
- 1.3.2. Only if **no** existing worker sub-agent can fulfill the objective — create a new worker
  sub-agent file (section 4).

## 2. Discovering existing worker sub-agents

Learn what agents and skills are available from current **system prompt** and **session context**.
Do not read configuration files.  To enumerate what is actionable, read your system prompt for
injected `<agents>` and `<skills>` blocks — that IS the catalog.

## 3. When an existing worker sub-agent "can fulfill" the objective

An existing worker sub-agent is reusable for `sub_agent_name` when ALL of these hold:

3.1. **Responsibility match.** Its objective covers the action `sub_agent_objective` describes
(e.g. an existing `run_tests` covers a new `run_unit_tests` if scope can be passed as input).

3.2. If any condition fails, treat reuse as impossible and create a new worker sub-agent
(section 4). Prefer extending with defaults over duplicating a near-match.

## 4. Creating a new worker sub-agent (only when reuse is impossible)

4.1. Write the file to `sub_agent_path`. The file name MUST match exactly the name the
control-flow invokes (`sub_agent_name`).

4.2. Give the worker sub-agent a single, well-scoped responsibility derived from
`sub_agent_objective` and the role it plays in the control-flow (planner, implementer, verifier,
diagnoser, reviewer, goal-checker, researcher, or a goal-specific action).

4.3. Consult the matching reference for role-specific guidance before writing the body:
- For workers that implement or modify code, run tests/builds, verify a result, or diagnose a
  failure → `references/worker-sub-agents.md`.
- For read-only research/exploration workers that gather information without changing anything
  → `references/research-sub-agents.md`.

4.4. Define its input/output contract per section 5 so the orchestration can follow the
invocation convention and a future run can reuse it (closing the reuse loop).

## 5. Worker sub-agent file contract

Each worker sub-agent file MUST contain, at minimum:

5.1. **Frontmatter** with `name` (equal to the file name and the name the control-flow invokes)
and a `description` covering what the worker sub-agent does and when the loop should call it.

5.2. **Behavior.** A precise description of what the worker sub-agent does to fulfill its
responsibility toward the goal. Never inline this behavior into the control-flow — it lives only
in the worker sub-agent file.

5.3. **Outputs the loop branches on.** Document the `OUTPUT_<X>` values the worker sub-agent
reports back, and make them machine-comparable so the control-flow's conditional decisions
resolve deterministically. For verifier /goal-checker worker sub-agents, describe the success
value that lets the execution loop take its exit branch, so the loop can  terminate.

## 6. Invariants to enforce before finishing

6.2. No placeholder, dummy, or simplified worker sub-agent was written.

6.3. If a file was created, it lives at `sub_agent_path` with a name matching the name the
control-flow invokes, and follows the contract in section 5.
