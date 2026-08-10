---
name: llassembly-control-flow
description: > 
    Translate a natural language request into a control flow written as a mermaid flowchart,
    where each node is a sub-agent invocation and each decision is a yes/no question answered
    by the sub-agent that precedes it. Use when generating execution plans that orchestrate sub-agents.
---

# LLAssembly Sub-Agent Orchestration (mermaid)

Write a mermaid flowchart that represents the control flow required to achieve the goal
defined in the request. The diagram is executed directly by the variant's emulator, which
walks it one node at a time, so it must follow the rules below exactly.

## 0. Input and Output

**Input:** A natural language request to translate into a control-flow diagram.

**Output:** A single mermaid flowchart, valid per the sections below. Nothing else — no
prose around it, no code fences inside the plan file.

## 1. Core Requirements

1.1. The control flow must keep working until the goal is **actually achieved**, not
merely until each step has run once. Prefer branches and retry loops over straight-line
sequences.

1.2. Only the node shapes in section [2.1] and the edge labels in section [2.2] may be
used.

1.3. Every sub-agent is declared by writing it as a node; there is no separate declaration
block.

1.4. Never write placeholders, simplified flows, or demo steps. Produce the complete plan.

1.5. The diagram is the plan. Sub-agents provide the actual implementation when invoked.

1.6. The plan covers the user's work only. Generating sub-agents, checking the plan and
re-running the driver are the driver's own stages — never nodes.

1.7. Keep it small. Add a branch where a sub-agent can genuinely fail and a different
action follows; the two-agent plan in [4.4] is a normal size.

## 2. Diagram Guidelines

### 2.1. Allowed Node Shapes

| Shape | Meaning |
|---|---|
| `start([start])` | The entry point. Exactly one per plan. |
| `done([done])` | Success exit. At least one exit is required. |
| `abort([abort])` | Failure exit. |
| `id[[name]]` | Invoke the sub-agent `name`. |
| `id[[name: objective]]` | Invoke `name`, with a one-sentence objective. |
| `id{"Question?"}` | A decision answered by the **preceding sub-agent** (see section [3]). |
| `id{{"Question?"}}` | A decision answered by its **own** sub-agent (see section [3.4]). |

Node ids must match `[a-z][a-z0-9_]*`. Sub-agent names must too. A node id is not
displayed — give nodes short, descriptive ids (`w_tests`, `d_pass`, `q_ready`).
Name sub-agents after the work, never after this diagram: `write_usage_doc`, not `write_plan`.

In the objective never mention directly "llassembly-agentic-workflow" not matter what user
request, goal is says.

### 2.2. Allowed Edge Labels

Only three labels exist, and every other label is a compile error:

- `-- true -->` and `-- false -->` — the two branches of a decision. Both are required.
- `-- error -->` — optional. Where to go when a sub-agent reports failure. Defaults to
  aborting.
- An unlabelled `-->` — the next step after `start` or a sub-agent.

**Never write a retry count, a limit, or any number on an edge.** Loop bounds are added
automatically (section [4.2]). A label such as `"false max 3"` will be rejected.

Give each decision node its own line. An arrow on the same line as `{…}` or `{{…}}` adds an
extra unlabelled edge, and the plan is rejected.

### 2.3. Structure

- Exactly one `start`; at least one exit.
- Every node must be reachable from `start` and must have a path to an exit.
- A sub-agent has exactly one unlabelled outgoing edge, plus at most one `error` edge.
- A decision has exactly one `true` edge and one `false` edge.
- `{{ }}` follows the decision rule, not the sub-agent rule: one `true` edge, one `false`
  edge, and no unlabelled edge — even though it costs a sub-agent call.
- Every id you mention is given a shape exactly once, exits included: `abort([abort])`,
  `finish([done])`.
- Shape only ids an edge points at. Most plans need no `abort` node: an unrecovered failure
  aborts on its own.
- Exits have no outgoing edges.
- Subgraphs are not supported.

## 3. Decisions and Sub-Agents

Each `[[ ]]` node becomes a sub-agent with `OUTPUT_1` as its status (`"ok"` or `"error"`).

### 3.1. A `{ }` decision is answered by the sub-agent before it — for free

The question is added to that sub-agent's output contract, so the sub-agent answers it
while doing its work. **No extra sub-agent runs, and no extra call is made.** Prefer this
form.

```
w_tests[[run_tests: Run the project test suite]] --> d_pass
d_pass{"Did every test pass?"}
d_pass -- true --> finish
d_pass -- false --> w_fix
w_fix[[fix_tests: Fix the failing tests]] --> w_tests
finish([done])
```

Here `run_tests` is asked for two things: its status, and whether every test passed.

### 3.2. Write questions the sub-agent can actually answer

The question must be answerable **by that sub-agent, at the moment it runs**. "Did every
test pass?" is fine for a sub-agent that runs tests. "Is the customer happy?" is not.

### 3.3. Each decision binds to the nearest sub-agent above it

Walk back from the decision through the diagram; the first sub-agent on every path is the
one that answers it. If two different sub-agents could reach the decision, the plan is
ambiguous and will be rejected — insert a sub-agent, or use the form in section [3.4].

At most nine `{ }` decisions may bind to one sub-agent.

### 3.4. A `{{ }}` decision gets its own sub-agent — costs one call

Use the double-brace form only where one of these actually holds — otherwise the fused form
in [3.1] answers the question for free:

- the decision has no sub-agent above it (for example it comes straight after `start`);
- two different sub-agents could answer it;
- it is a judgement across several earlier results that no single sub-agent can make.

For the second, try rerouting first: aiming a retry edge at the work node rather than at the
decision usually resolves the ambiguity and keeps `{ }`.

```
q_ready{{"Is the release ready to ship?"}}
q_ready -- true --> w_publish
q_ready -- false --> abort
w_publish[[publish: Publish the release]] --> finish
finish([done])
abort([abort])
```

The sub-agent is named after the node id, so give these nodes meaningful ids.

## 4. Loops, Failure, and Verification

4.1. **Branch on results.** Do not assume a sub-agent succeeds. Add an `error` edge where
a specific recovery step makes sense; otherwise failure aborts the plan automatically.

4.2. **Loop by drawing an edge backwards.** To retry, point an edge at an earlier node.
You do not bound the loop: the emulator abandons any plan that has not finished within a
fixed number of steps. Do not write counts anywhere.

```
w_fix[[fix_tests: Fix the failing tests]] --> w_tests
```

4.3. **Verify before finishing.** When the request implies a checkable outcome (tests must
pass, a service must respond, a file must contain something), end the work with a
sub-agent that checks it and a decision that reads the answer. Loop back on `false`; go to
`done` on `true`.

4.4. **Full example.**

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

Two sub-agents, one decision, one bounded retry loop — and the decision costs nothing.

## 5. Before you write the file

- [ ] The first line is `flowchart TD`.
- [ ] Every id that appears anywhere is given a shape exactly once — exits included:
      `start([start])`, `finish([done])`, `abort([abort])`. An id that only ever appears
      on an edge has no shape and the plan will not load.
- [ ] Every node shape is one from [2.1]; every edge label is one from [2.2].
- [ ] Every decision has an outgoing edge for each answer it can give.
- [ ] No `subgraph`, no counts, no styling.

