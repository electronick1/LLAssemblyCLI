# Helper: testing

Helper for the `llassembly-generate-sub-agents` worker. Use it when the sub-agent being
defined runs a test suite, a build, or a lint pass. The rules that apply to every
sub-agent live in `references/workers/llassembly-generate-sub-agents.md` §5; this file
adds only what is specific to this role.

## When this role applies

The objective is to execute an existing suite or toolchain and report what it produced —
as opposed to judging whether the overall goal is met, which is verification's job.

## What the definition must specify

- Run the requested scope (full suite, a subset, a single target) as given by the inputs.
- Distinguish "the suite ran and failed" from "the suite could not run"; both are useful
  but different branches.

## What to report

- Pass/fail: `"true"` if every test passed, else `"false"`.
- Enough detail to drive the next branch: a JSON array of failing test identifiers if
  something failed, else how many tests passed.
