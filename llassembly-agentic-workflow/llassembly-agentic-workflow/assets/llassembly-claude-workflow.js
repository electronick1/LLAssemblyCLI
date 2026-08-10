/**
 * LLAssembly Claude workflow driver — split orchestration.
 *
 * Install: copy into <project>/.claude/workflows/
 * Invoke:  /llassembly-claude-workflow <goal>
 *          Workflow({ name: "llassembly-claude-workflow", args: { goal, workdir?, workflowId? } })
 *
 */

export const meta = {
  name: "llassembly-claude-workflow",
  description:
    "Run Claude dynamic workflow to drive llassembly-agentic-workflow as agent orchestrator",
  phases: [
    { title: "Stage 1 - setup" },
    { title: "Stage 2 - control flow" },
    { title: "Stage 3 - sub-agents" },
    { title: "Stage 4 - warmup" },
    { title: "Stage 5 - execute" },
  ],
};

const PHASE = {
  "1": "Stage 1 - setup",
  "2": "Stage 2 - control flow",
  "3": "Stage 3 - sub-agents",
  "4": "Stage 4 - warmup",
  "5": "Stage 5 - execute",
};

const A = typeof args === "string" ? { goal: args } : args || {};
const goal = String(A.goal || "").trim();
if (!goal) return { finished: false, error: "no goal - invoke as /llassembly-claude-workflow <goal>" };

const maxIterations = A.maxIterations || 50;

const DRIVER = "python scripts/get_next_instruction.py";
const AGENT = "general-purpose";
const PREP_AGENT = "Explore";

let summaries = [];

const INIT = {
  type: "object",
  additionalProperties: false,
  required: ["workflowId", "driverPath", "skillDir", "python", "workdir"],
  properties: {
    workflowId: {
      type: "string",
      description: "the --llassembly-session id exactly as script printed",
    },
    driverPath: {
      type: "string",
      description: "absolute path of the get_next_instruction.py you just ran",
    },
    skillDir: {
      type: "string",
      description:
        "absolute path of the llassembly-agentic-workflow skill directory - the one that " +
        "holds scripts/ and references/",
    },
    python: {
      type: "string",
      description:
        "the python executable you ran it with, exactly as you typed it - whatever this " +
        "project needs (python, python3, or a uv/venv path). No arguments, no env prefix",
    },
    workdir: {
      type: "string",
      description: "Give the full directory path where user request results should be saved — " +
                   "this is where your working directory is to put artifacts that satisfying the goal, " + 
                   "NEVER the skill directory."
    },
  },
};

const PREP = {
  type: "object",
  additionalProperties: false,
  required: ["finished", "stage", "nextCmd", "tasks"],
  properties: {
    finished: {
      type: "boolean",
      description: "true only if stdout printed a line starting with 'Execution finished'",
    },

    stage: {
      type: "string",
      enum: ["1", "2", "3", "4", "5", "unknown"],
      description:
        "the digit on the line that begins 'Stage ' - it is not always the first line of " +
        "stdout ('Stage 1.1:' is '1'); 'unknown' if no such line was printed"
    },

    nextCmd: {
      type: "string",
      description:
        "How the next run must invoke the driver - always the same absolute path and python " +
        "executable you were given, never relative and never behind a cd, always with the " +
        "--llassembly-session id you were given, plus any argument it asked",
    },

    tasks: {
      type: "array",
      description:
        "One entry per agent the output asked to spawn; Some stages may list several " +
        "sub-agents in one block and each gets its own entry, never merged, never one extra. " +
        "You must specify and tell the task what it needs to do and what it's objective is based on " +
        " - scope of work in the current iteration of the workflow \n " +
        " - every limit the user placed on the work. \n " +
        " If instructions say how to delegate work to an agent follow it providing " +
        " what it requires here.",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["label", "prompt"],
        properties: {
          label: {
            type: "string",
            description: "two to four words naming this unit of work",
          },
          prompt: {
            type: "string",
            description:
              "The Task's whole prompt, ready to call as written; its reader sees " +
              "only this text. Copy every path and the whole JSON schema block character for character " +
              "- and fill every <placeholder>, as well as " +
              "objective, context and scope of work for THIS step alone: what to do, where, and every " +
              "limit the request set. "
          },
        },
      },
    },
  },
};

const EXEC = {
  type: "object",
  additionalProperties: false,
  required: ["summary"],
  properties: {
    summary: {
      type: "string",
      description: "one short paragraph of what you did",
    },
  },
};

const initPrompt =
`Use llassembly-agentic-workflow skill to run this ONCE: ${DRIVER}
Use a python executable appropriate for this project (uv, a venv, or the one on PATH).

The workflow goal is: ${goal}
If that goal asks for this workflow to be saved or pre-generated rather than run, ask the user whether it should be
saved only and under what short name, and if so add --save "<name>" --whoamcli claude to this same first run.

It prints Stage 1.1 naming the --llassembly-session id that keys this whole workflow. Report that id character for
character, the absolute path of the get_next_instruction.py you ran, the absolute path of the skill directory that
holds it, the python executable you ran it with, and the absolute path pwd prints - then stop: do not run the script
again, do not act on anything else its output says, launch nothing, and create, write or edit no file. The driver
writes its own state under /tmp/llassembly; that is expected and is not you.`;

let workflowId = String(A.workflowId || "").trim();
let driverPath = String(A.driverPath || "").trim();
let skillDir = String(A.skillDir || "").trim();
let python = String(A.python || "python").trim();
let workdir = String(A.workdir || "").trim();

if (!workflowId) {
  const b = await agent(initPrompt, {
    agentType: PREP_AGENT,
    label: "Init",
    phase: PHASE["1"],
    effort: "low",
    schema: INIT,
  });

  if (!b || !b.workflowId) return { finished: false, error: "Stage 1.1 reported no --llassembly-session id" };
  workflowId = String(b.workflowId).trim();
  driverPath = String(b.driverPath || "").trim();
  skillDir = String(b.skillDir || "").trim();
  python = String(b.python || python).trim();
  if (!workdir) workdir = String(b.workdir || "").trim();
}

// The driver command line, as the previous preparer wrote it.
let cmd = `${python} "${driverPath}" --llassembly-session "${workflowId}"`;

const preparePrompt = (st) =>
`
Run the driver once and hand back what it asks for. Perform no work yourself, launch nothing, and
create, write or edit no file - the driver writes its own state under /tmp/llassembly, which is
expected and is not you. Your only job is to run it and return structured output:

1. Seek for the next instruction running and following it's output:
       ${cmd}
   That script is the only program you run. Without the --llassembly-session argument the driver
   starts a new workflow from scratch. Its closing lines tell an orchestrator to re-run it; that
   orchestrator is the process that launched you, so do not run it again this turn - a second run
   registers a second pending unit of work and corrupts the workflow. Report in nextCmd how it must
   be invoked next, reusing that same absolute path and python executable.
2. The output is addressed to an orchestrator. You are not it. Wherever it tells the orchestrator to
   SPAWN A NEW AGENT - a worker or a sub-agent, one or several at once - do NOT spawn it and do NOT do
   that work yourself: return each as an entry in tasks, defined exactly as it would have been
   called. You have no Task tool and no Agent tool; TaskCreate is a to-do list, not a way to run
   work, so never call it. If the output offers to create something inline instead because there is
   no Task/Spawning tool, that offer is void: it is still a task, return it. If it asks for none at all,
   return empty tasks.
3. Read nothing else and invoke no skill. That stdout is your complete and only input - do not open
   references/**, plan_llassembly, runtime.log, or anything under /tmp/llassembly. Those paths
   belong to the tasks you return, not to you.
4. Report finished, stage, nextCmd and tasks exactly as the schema describes them, then stop.
5. Specify where work results for the user request goes, and where (if required by task) JSON schema
report must be saved.

--- Context ---
Workflow goal: ${goal}
${st === "5" ? `Work done so far, each unit of work in its own unverified words:\n${summaries.join("\n") || "(nothing yet)"}` : ""}
`;

const executePrompt = (t) =>
`You are a standalone unit of work running as a worker/sub-agent.

- Workdir where end goal result saved: ${workdir}.
- As a worker you are not allowed to run scripts/get_next_instruction.py, this is not your scope!
- When done, stop and summarise what you did in one short paragraph.
- Your scope of work is:
"""
${t.prompt}
"""

`;


let finished = false;
let iterations = 0;

let stage = "1";

for (let i = 1; i <= maxIterations; i++) {
  iterations = i;

  const p = await agent(preparePrompt(stage), {
    agentType: PREP_AGENT,
    label: `Preparing next step #${i}`,
    phase: PHASE[stage],
    effort: "low",
    schema: PREP,
  });

  if (!p) break;

  if ((finished = p.finished === true)) break;
  const next = PHASE[p.stage] ? String(p.stage) : stage;

  if (stage !== "5" && next === "5") summaries = [];
  stage = next;

  cmd = String(p.nextCmd || "").trim();

  if (!cmd.includes("get_next_instruction.py") || !cmd.includes(workflowId)) break;

  const ts = (Array.isArray(p.tasks) ? p.tasks : []).filter((t) => t && String(t.prompt || "").trim());

  if (!ts.length) continue;
  const out = await parallel(
    ts.map((t, k) => () =>
      agent(executePrompt(t), {
        agentType: AGENT,
        label: `Execute ${i}.${k + 1} ${t.label}`,
        phase: PHASE[stage],
        schema: EXEC,
      })
    )
  );

  summaries.push(...ts.map((t, k) => `- [run ${i}] ${t.label}: ${(out[k] && out[k].summary) || "(no summary)"}`));
}

return { finished, iterations, stage, workflowId, summaries };
