# Helper: verification

Helper for the `llassembly-generate-sub-agents` worker. Use it when the sub-agent being
defined decides whether the goal has actually been met — the sub-agent the control-flow
branches on to stop iterating. The rules that apply to every sub-agent live in
`references/workers/llassembly-generate-sub-agents.md` §5; this file adds only what is
specific to this role.

## When this role applies

The request implies a checkable outcome: code that must run, an endpoint that must
respond, a file that must contain something, a condition that must hold before the
workflow may exit.

## What the definition must specify

- Check the actual observable outcome implied by the goal (a file's contents, an endpoint
  responding, a value being correct) — not merely that an earlier step "ran".
- Be read-only: a verifier inspects and reports; it must not fix or mutate state.

## What to report

- A single decisive boolean the workflow exits on: `"true"` when every check passed,
  `"false"` otherwise. This is the value that lets the workflow take its exit branch, so it
  must always be set.
- A short reason on failure — a description of the first failing check — so a diagnosis
  sub-agent can act on it; on success, report what was checked instead.
