---
name: @@SKILL_NAME@@
description: Companion skill for llassembly-agentic-workflow. A compiled control-flow plan with pre-generated agents, executed by the llassembly-agentic-workflow skill. Use when directly asked for this skill.
---

# @@SKILL_NAME@@

Prebuilt llassembly workflow. The control-flow plan (`references/plan_llassembly.<ext>`)
and its sub-agents (`references/agents/`) are already generated. This skill is executed by
the **llassembly-agentic-workflow** skill's driver — it does not run on its own.

`references/agents/` holds the sub-agent definitions. `--init` copies them into the workflow
directory, and the driver prints each one's path beside its name when it is time to run it.

## How to run

Switch to llassembly-agentic-workflow skill, drive the workflow in the current session
with this skill's path:

    python scripts/get_next_instruction.py --init "<path_to_this_skill>"

where `<path_to_this_skill>` is a folder path where @@SKILL_NAME@@ is located, then follow
the instructions it prints until it prints `Execution finished. Goal is achieved.`. Follow
the printed instructions literally, and re-run the llassembly-agentic-workflow
`scripts/get_next_instruction.py` driver. Never decide the next step yourself — the driver
decides. In case of failure or misbehaviour always re-run the driver. Always do exactly
what the latest execution printed, then re-run the driver. Stages can repeat, branch, or
arrive in a different order.

