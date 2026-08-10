# Helper: diagnosis

Helper for the `llassembly-generate-sub-agents` worker. Use it when the sub-agent being
defined turns a failure into the next concrete step, so the workflow's next iteration is
targeted rather than blind. The rules that apply to every sub-agent live in
`references/workers/llassembly-generate-sub-agents.md` §5; this file adds only what is
specific to this role.

## When this role applies

A verification or testing sub-agent can fail, and the plan should react to *why* it failed
instead of simply retrying the same work.

## What the definition must specify

- Take the failing signal as input (the failure list / reason from the previous
  sub-agent).
- Produce an actionable next step, not just an explanation: which file/area to change and
  how.
- Be read-only: diagnose and report; let a coding sub-agent apply the fix on the next
  iteration.

## What to report

- A concise instruction describing the fix to apply.
- The file the coding sub-agent should edit.
