# Reference: Authoring Worker Sub-Agents

This reference guides the **generator agent** (`llassembly-generate-sub-agents`) when it
writes a **worker sub-agent** definition file. A worker sub-agent is one node of work that the
control-flow  invokes by name and the execution loop runs once per invocation, carrying
state between invocations.

It covers the four worker roles that appear in almost every loop — **coding**,
**verification**, **testing**, **diagnosis** — plus the rules common to all of them. For
read-only exploration workers, see `research-sub-agents.md` instead.

## Common rules for every worker sub-agent

- **One responsibility.** A worker does exactly one job derived from its objective. Do not bundle
  unrelated actions; that is what separate workers and the control-flow are for.
- **Report comparable outputs.** The control-flow branches by comparing on the worker's
  named outputs. Emit stable, machine-comparable values — prefer fixed strings over free-form
  prose.
- **Idempotent and resumable.** The loop is crash-safe and may re-run a worker. Re-running with
  the same inputs must be safe (no duplicated edits, no double-applied side effects).
- **Complete, never demo.** No placeholder, dummy, simplified, or TODO behavior. The worker
  must be fully runnable by the execution loop.
- **Real work lives in the worker, not the control-flow.** The control-flow only orchestrates; the
  worker file is the only place the behavior is described.

## Coding workers (implement / modify code)

Use for workers whose objective is to make a concrete change to the codebase.

- Scope the change to exactly what the inputs describe; do not refactor beyond the task.
- Prefer editing existing files over creating new ones; match the surrounding style.
- Make the change build-aware: leave the project in a compilable/loadable state, since a
  testing or verification worker usually runs next in the loop.
- Be idempotent: detect whether the change is already present before applying it again.
- Report a status output the plan can branch on, e.g. `OUTPUT_1` =
  `status: "ok" when the change applied cleanly, "error" otherwise`. Report what was
  touched (e.g. `OUTPUT_2` = `changed_paths: JSON array of files modified`).

## Verification workers (confirm the goal / a result)

Use for the worker the loop branches on to decide whether to stop iterating.

- Check the actual observable outcome implied by the goal (a file's contents, an endpoint
  responding, a value being correct) — not merely that an earlier step "ran".
- Be read-only: a verifier inspects and reports; it must not fix or mutate state.
- Report a single decisive boolean the loop exits on, e.g. `OUTPUT_1` =
  `all_passed: "true" when every check passed, "false" otherwise`. This is the value that lets
  the loop take its exit branch, so it must always be set.
- Additionally report a short reason on failure (e.g. `OUTPUT_2` = `first_failure: short
  description of the first failing check`) so a diagnosis worker can act on it, or if successed
  report what verification was done (e.g. `OUTPUT_2` = `checked: X rules`)

## Testing workers (run a test/build suite)

Use for workers that execute tests, a build, or a lint pass.

- Run the requested scope (full suite, a subset, a single target) as given by the inputs.
- Distinguish "the suite ran and failed" from "the suite could not run"; both are useful but
  different branches.
- Report pass/fail plus enough detail to drive the next branch, e.g. `OUTPUT_1` =
  `passed: "true" if every test passed else "false"`, `OUTPUT_2` = `failures: JSON array of
  failing test identifiers if something failed else X tests passed`.

## Diagnosis workers (turn a failure into the next step)

Use to convert a verifier's or tester's failure into a concrete instruction for a coding
worker, so the loop's next iteration is targeted rather than blind.

- Take the failing signal as input (the failure list / reason from the previous worker).
- Produce an actionable next step, not just an explanation: which file/area to change and how.
- Be read-only: diagnose and report; let a coding worker apply the fix on the next iteration.
- Report the next action in a form a coding worker can consume directly, e.g. `OUTPUT_1` =
  `next_action: concise instruction describing the fix to apply`, `OUTPUT_2` = `target_path:
  the file the coding worker should edit`.
