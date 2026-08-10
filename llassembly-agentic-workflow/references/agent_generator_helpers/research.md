# Helper: research

Helper for the `llassembly-generate-sub-agents` worker. Use it when the sub-agent being
defined only *gathers information* the rest of the control-flow needs, without changing
anything. The rules that apply to every sub-agent live in
`references/workers/llassembly-generate-sub-agents.md` §5; this file adds only what is
specific to this role.

## When this role applies

A plan step must *find out* something before later steps can act: locating where a
behavior lives, reading existing contracts, surveying structure, collecting facts, or
answering an open question about the codebase or environment.

## Defining trait: read-only

- A research sub-agent **never mutates state** — no edits, no file creation, no builds, no
  side-effecting commands. It only inspects and reports.
- Because it is read-only it is always safe to re-run, which fits the crash-safe,
  resumable execution workflow.
- If a step needs to *change* something based on findings, that belongs to a coding
  sub-agent on a later iteration (see `coding.md`); keep the research sub-agent purely
  informational.

## What the definition must specify

- Take a precise question or target as input: what to find, where to look (paths,
  identifiers, scope), and the form of answer the downstream sub-agent expects.
- Pass enough context that the sub-agent does not have to re-derive the goal — the
  question should be answerable from the inputs plus inspection.
- One question per research sub-agent. If a step needs several independent facts, prefer
  distinct research sub-agents (or distinct invocations) so each result can be branched on
  separately.
- Do not pre-empt later work: report what is, not what should change. Recommending edits
  is the job of a diagnosis sub-agent; applying them is the job of a coding sub-agent.

## What to report

- A found/not-found status the plan can test: `"true"` when the answer was determined,
  `"false"` otherwise. This lets the workflow branch to an alternative strategy when
  research comes up empty.
- The finding itself in a structured, consumable form — JSON with the discovered facts
  (paths, names, values) — so a later coding or verification sub-agent can use it directly.
- Keep findings stable and concrete (paths, symbols, values) rather than narrative prose,
  so downstream comparisons stay deterministic.
