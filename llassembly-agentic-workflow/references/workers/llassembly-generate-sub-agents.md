---
name: llassembly-generate-sub-agents
description: >
    Generate one sub-agent definition file that the control-flow plan invokes by name,
    so the workflow can achieve its goal. Used as one step of the execution workflow,
    invoked once per missing sub-agent.
---

## How the generator worker is invoked

This is part of the workflow machinery. It does not perform the actual work towards the
goal; its only job is to write the **sub-agent** definition explaining how the work needs
to be done.

You are a worker in a workflow that is already running. Write your one file and stop.
Never run `scripts/get_next_instruction.py` and never touch workflow state.

The control-flow plan is produced by the `references/workers/llassembly-control-flow.md`
worker. Every step in
it that runs work invokes a sub-agent. The execution workflow (driven by
`scripts/get_next_instruction.py`) runs those sub-agents one at a time, in the order and
branching the plan encodes, carrying state between them until the goal is achieved.

You must never, in any circumstances, name the skill that orchestrates this workflow inside
the content of the generated sub-agent. Otherwise that skill will be called in the infinite
recursion that breaks the process. Its name is in the paths you were given; filter it out of
any output before writing.

Agent definition you write must be portable - do not include any system path into it.

## 0. Inputs and outputs

0.1. **Inputs** — supplied by the workflow when it invokes the generator worker:
- `sub_agent_name` — the sub-agent's name in the control-flow.
- `sub_agent_path` — Absolute path where the sub-agent definition file must be written
  (e.g. `.../references/agents/<name>.md`). The file name without `.md` must equal
  `sub_agent_name`.
- `sub_agent_objective` — Objective/responsibility this sub-agent must fulfill toward the
  goal.

The control-flow plan in `plan_llassembly.<ext>` is available as context for understanding
how each sub-agent is invoked and what the plan branches on.

0.2. **Output** — **always** exactly one sub-agent definition file at `sub_agent_path`.
Reuse of an existing skill is expressed *inside* the agent file, as a short stub that
delegates to that skill (section 4). The workflow supplies the output JSON schema, the
result path and the directory deliverables go under at dispatch, so a definition must never
hard-code output keys or an output location — write the job, not the contract.

0.3. **Never** write a placeholder, dummy, simplified, or demo sub-agent. The generated
sub-agent must be complete and runnable by the execution workflow.

0.4. `sub_agent_name`, the file name without `.md`, and the `name:` in the front-matter are
the same string. Do not slugify, hyphenate or re-case it.

## 1. Generation procedure

1.1. **List the available skills and agents** (section 2). Enumerate the catalog of
available agents and skills explicitly.

1.2. **Decide whether one of them covers `sub_agent_objective`** (section 3).

1.3. **Write exactly one file** at `sub_agent_path`:
- a skill covers the objective → write the **delegating stub** (section 4);
- nothing covers it → write a **full definition** (section 5), consulting the role helper
  listed in 5.1.

## 2. Listing the available skills/agents

Learn what agents and skills are available from your current **system prompt** and
**session context**. To enumerate what is actionable, read your system prompt for injected
`<agents>` and `<skills>` blocks — that IS the catalog. Do not read configuration files
and do not scan the filesystem.

Write the candidate names down before step 1.2 so the decision is made against the real
catalog rather than an assumption about what probably exists.

## 3. When an existing skill/agent "covers" the objective

3.1. **Responsibility match.** Its stated purpose covers the action `sub_agent_objective`
describes. That is the whole test — do not require an exact input/output match, since the
stub supplies the contract itself.

3.2. **Partial match still delegates.** If a skill does most of the job, name it in the
stub and add only the steps it does not cover. Prefer this over duplicating a near-match
as a full definition.

3.3. **No match means a full definition.** If nothing in the catalog fits, write a full
definition (section 5).

## 4. The delegating stub (a skill covers the objective)

Keep it short. The skill already knows how to do the work; this file only says *which*
skill to use, *what* to apply it to, and *what to report back*.

```markdown
---
name: <sub_agent_name>
description: <one line — what it does and when the workflow calls it>
---

Do <objective, one sentence> using the `<skill-name>` skill.

<optional: 1–3 lines of steering (scope, limits, procedure)>
```

4.1. Do not restate what the skill already knows, and never copy its internals into the
stub. Extend it only where the skill genuinely needs steering for this objective.

4.2. The file name MUST match exactly the name the control-flow invokes
(`sub_agent_name`).

## 5. Writing a full definition (no skill covers the objective)

5.1. Write the definition of the sub-agent to satisfy its role and goal. Use this shape:

```markdown
---
name: <sub_agent_name>
description: <one line — what it does and when the workflow calls it>
---

## Objective
<one sentence, from sub_agent_objective>

## Procedure
<the concrete steps, safe to re-run>
```

Never add `## Outputs` and never name a result key or any path the
sub-agent writes to — the dispatch states the result path and where deliverables go.

5.2. Consult the closest matching helper in `references/agent_generator_helpers/` for
role-specific guidance before writing the body:
- produces or alters a file, code or prose → `references/agent_generator_helpers/coding.md`
- decides whether the goal is met → `references/agent_generator_helpers/verification.md`
- runs a test suite, build, or lint pass → `references/agent_generator_helpers/testing.md`
- turns a failure into the next concrete step →
  `references/agent_generator_helpers/diagnosis.md`
- gathers information read-only, changing nothing →
  `references/agent_generator_helpers/research.md`

5.3. Give the sub-agent a single, well-scoped responsibility derived from
`sub_agent_objective` and the role it plays in the control-flow. The file name MUST match
`sub_agent_name`.

5.4. Do not name the skill that orchestrates this workflow in the context of the generated
sub-agent, otherwise recursion may be introduced.
