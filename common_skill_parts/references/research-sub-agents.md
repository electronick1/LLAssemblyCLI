# Reference: Authoring Research Sub-Agents

This reference guides the **generator agent** (`llassembly-generate-sub-agents`) when the
**worker sub-agent** it is writing is a **research worker** — a read-only worker whose job is to
gather information the rest of the control-flow needs, without changing anything.

Use a research worker when a plan step must *find out* something before later steps can act:
locating where a behavior lives, reading existing contracts, surveying structure, collecting
facts, or answering an open question about the codebase or environment.

## Defining trait: read-only

- A research worker **never mutates state** — no edits, no file creation, no builds, no
  side-effecting commands. It only inspects and reports.
- Because it is read-only it is always safe to re-run, which fits the crash-safe, resumable
  execution loop.
- If a step needs to *change* something based on findings, that belongs to a coding worker on a
  later iteration (see `worker-sub-agents.md`); keep the research worker purely informational.

## Inputs

- Take a precise question or target as input: what to find, where to look (paths, identifiers,
  scope), and the form of answer the downstream worker expects.
- Pass enough context that the worker does not have to re-derive the goal — the question should
  be answerable from the inputs plus inspection.

## Outputs the control-flow can branch on

The control-flow still branches on this worker's `OUTPUT_<X>` values with comparing, so make the
result both human-readable and machine-comparable:

- Report a found/not-found style status the plan can test, e.g. `OUTPUT_1` =
  `found: "true" when the answer was determined, "false" otherwise`. This lets the loop branch
  to an alternative strategy when research comes up empty.
- Report the finding itself in a structured, consumable form, e.g. `OUTPUT_2` = `result: JSON
  with the discovered facts (paths, names, values)` so a later coding or verification worker can
  use it directly.
- Keep findings stable and concrete (paths, symbols, values) rather than narrative prose, so
  downstream comparisons stay deterministic.

## Scope discipline

- One question per research worker. If a step needs several independent facts, prefer distinct
  research workers (or distinct invocations) so each result can be branched on separately.
- Do not pre-empt later work: report what is, not what should change. Recommending edits is the
  job of a diagnosis worker; applying them is the job of a coding worker.
