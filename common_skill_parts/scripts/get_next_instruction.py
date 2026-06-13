import argparse
import dataclasses
import fcntl
import json
import os
import secrets
import string
import sys
import textwrap
import time
from dataclasses import dataclass, field
from importlib import util
from pathlib import Path
from typing import Any, Self

# ---------------------------------------------------------------------------
# next_command templates
# ---------------------------------------------------------------------------
# Every instruction this driver prints back to the orchestrator lives here as a
# template. Step functions fill the placeholders and the wording stays
# consistent across phases. Each instruction (except the terminal one)
# telling the caller to re-run this driver, reusing the same loop id, so the
# resumable state machine can advance to the next step.

NEXT_COMMAND_CREATE_PLAN = textwrap.dedent("""
    Stage 3:
    Run the Task tool to launch the `llassembly-control-flow` as a sub-agent.

    After `llassembly-control-flow` returns re-run `LLASSEMBLY_LOOP_ID={loop_id} python get_next_instruction.py`
    with no arguments yourself in the current session to continue.

    Execute the agentd defined in `agents/llassembly-control-flow.md` and
    pass ONLY this 2 instructions to `llassembly-control-flow` :
    1. Compile the goal into an LLAssembly control-flow plan and write it to: `{plan_path}`
    2. Your only task is to write that plan file. As soon as the file is written, 
    STOP and return control to the caller. Do NOT run any scripts, do NOT execute the plan,
    and do NOT call `get_next_instruction.py` yourself.

""")

NEXT_COMMAND_GENERATE_SUB_AGENT = textwrap.dedent("""
    Stage 4:
    Run the Task tool to launch the `llassembly-generate-sub-agents` to create required
    for control-flow sub-agent called: `{sub_agent_name}`

    After `llassembly-generate-sub-agents` returns re-run `LLASSEMBLY_LOOP_ID={loop_id} python get_next_instruction.py`
    with no arguments yourself in the current session to continue.

    Execute the agent defined in `agents/llassembly-generate-sub-agents.md`
    and pass ONLY this 2 instructions to `llassembly-generate-sub-agents` :
    1. Create sub-agent definition:
        - sub-agent name: `{sub_agent_name}`
        - sub-agent path: `{sub_agent_path}`
        - sub-agent objective: `{sub_agent_objective}`
    2. Your only task is to create that file. As soon as the file is written, STOP and return
    control to the caller. Do NOT run any scripts, do NOT execute the plan, and do NOT call
    `get_next_instruction.py` yourself.

    The control-flow plan that orchestrates generated sub-agents: {plan_path}

""")

NEXT_COMMAND_EXECUTE_SUB_AGENT = textwrap.dedent("""
    Stage 5:
    Run the Task tool to launch a sub-agent `{agent_name}` with the following instructions:
    - Execute the agent defined at: `{agent_path}`
    - Objective: {objective}
    - Expected outputs:
    {output_desc}

    When the sub-agent is done, re-run `LLASSEMBLY_LOOP_ID={loop_id} python get_next_instruction.py {output_args}`
    in the current session, passing the result of sub-agent as arguments one by one.
""")

# Terminal — the plan ran to completion; the goal is achieved.
NEXT_COMMAND_FINISHED = "Execution finished. Goal is achieved."


@dataclass
class Config:
    loop_id: str
    workdir_base_path: Path
    workdir_path: Path
    workdir_runtime_log_path: Path
    workdir_plan_path: Path

    @classmethod
    def init_by_loop_id(cls, loop_id: str) -> Self:
        workdir_base_path = os.environ.get("LLASSEMBLY_LOOP_PATH", "/tmp/")
        assert workdir_base_path
        workdir_path = Path(workdir_base_path).resolve() / f"llassembly/{loop_id}"
        return cls(
            loop_id=loop_id,
            workdir_base_path=Path(workdir_base_path).resolve(),
            workdir_path=workdir_path,
            workdir_runtime_log_path=workdir_path / "runtime.log",
            workdir_plan_path=workdir_path / "plan.llassembly",
        )


# ---------------------------------------------------------------------------
# Runtime log helpers
# ---------------------------------------------------------------------------


@dataclass
class RuntimeLogRecord:
    time: float
    msg: str
    next_command: str | None = None
    payload: str | None = None


def _locked_write_record(config: Config, record: dict | RuntimeLogRecord) -> None:
    if isinstance(record, RuntimeLogRecord):
        record = dataclasses.asdict(record)
    config.workdir_runtime_log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config.workdir_runtime_log_path, "a") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            fd.write(json.dumps(record) + "\n")
            fd.flush()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Custom exception – follows init_agentic_loop.py pattern of defining a
# local error class for callers that import this module.
# ---------------------------------------------------------------------------


class NextInstructionError(Exception):
    """Raised when this module encounters a fatal condition.

    Callers that import this module should catch this exception if they
    wrap ``next_instruction.main()`` in their own logic.
    """


# ---------------------------------------------------------------------------
# Decision functions – one per loop phase, keeping ``main()`` linear
# ---------------------------------------------------------------------------


def _step_print_loop_id():
    loop_id = f"{int(time.time())}_{''.join(secrets.choice(string.ascii_letters) for _ in range(6))}"
    print("Stage 1:")
    print(
        f"The following LLASSEMBLY_LOOP_ID must be used for all future calls to get_next_instruction.py:"
    )
    print(f"LLASSEMBLY_LOOP_ID={loop_id}")
    print(
        f"Re-run `LLASSEMBLY_LOOP_ID={loop_id} python get_next_instruction.py` with no arguments and LLASSEMBLY_LOOP_ID"
    )


def _step_ask_context_state(config: Config):
    print("Stage 2:")
    print(textwrap.dedent("""
            Give the full directory path where work results should be saved — this is where your workdir/root directory
            is (where you executed from), NOT the llassembly skill, agents, or scripts directory.
            """))
    print(
        f'Re-run `LLASSEMBLY_LOOP_ID={config.loop_id} python get_next_instruction.py "<path>"` write path as 1 argument'
    )


def _try_step_create_runtime_log(
    config: Config, reinit: bool = False
) -> RuntimeLogRecord | None:
    """Make sure runtime_log exists in loop_path."""
    config.workdir_path.mkdir(parents=True, exist_ok=True)

    # Already created
    if config.workdir_runtime_log_path.exists() and not reinit:
        return None

    config.workdir_runtime_log_path.unlink(missing_ok=True)
    config.workdir_runtime_log_path.touch()

    next_command = NEXT_COMMAND_CREATE_PLAN.format(
        plan_path=config.workdir_plan_path,
        loop_id=config.loop_id,
    )
    payload = {}
    extra_args = _parse_args().args
    if extra_args:
        payload["workdir_path"] = extra_args[0]
    return RuntimeLogRecord(
        time=time.time(),
        msg="runtime-log-created",
        next_command=next_command,
        payload=json.dumps(payload),
    )


_emulator_module: Any = None


def _import_emulator() -> Any:
    global _emulator_module
    if _emulator_module is not None:
        return _emulator_module
    script_dir = Path(__file__).resolve().parent
    spec = util.spec_from_file_location("emulator", script_dir / "emulator.py")
    em = util.module_from_spec(spec)  # type: ignore
    spec.loader.exec_module(em)  # type: ignore
    _emulator_module = em
    return em


def _try_step_run_init_sub_agents(config: Config) -> RuntimeLogRecord | None:
    plan_path = config.workdir_plan_path

    if not plan_path.exists():
        return _try_step_create_runtime_log(config, reinit=True)

    emulator = _import_emulator().Emulator.from_code(plan_path.read_text())
    for sub_agent in emulator.get_sub_agents().values():
        if sub_agent.include_path.startswith("general/"):
            sub_agent_path = config.workdir_path / "agents" / sub_agent.name
            if not sub_agent_path.exists():
                next_command = NEXT_COMMAND_GENERATE_SUB_AGENT.format(
                    sub_agent_path=sub_agent_path,
                    sub_agent_name=sub_agent.name,
                    sub_agent_objective=sub_agent.objective,
                    loop_id=config.loop_id,
                    plan_path=config.workdir_plan_path,
                )
                return RuntimeLogRecord(
                    time=time.time(), msg="sub-agent-missing", next_command=next_command
                )


def _try_step_infer_sub_agent_result(args: list[str]) -> RuntimeLogRecord | None:
    # If any args - infer them later from log, else None
    if not args:
        return None

    return RuntimeLogRecord(
        time=time.time(),
        msg="sub-agent-result",
        payload=json.dumps(args),
        next_command=None,
    )


def _step_execute_llassembly(
    config: Config, records: list[RuntimeLogRecord]
) -> RuntimeLogRecord | None:
    if not config.workdir_plan_path.exists():
        raise FileNotFoundError(f"Plan file not found: {config.workdir_plan_path}")

    emulator_module = _import_emulator()
    emulator = emulator_module.Emulator.from_code(config.workdir_plan_path.read_text())
    calls_iter = emulator.iter_tool_calls()
    last_agent_to_infer = None
    last_infer_hook = None
    workdir = None
    for record in records:
        if record.msg.startswith("runtime-log-created"):
            workdir = (json.loads(record.payload | "{}")).get("workdir_path")

        if record.msg.startswith("sub-agent-iteration:"):
            agent_name = record.msg[len("sub-agent-iteration:") :].strip()
            try:
                call = next(calls_iter)
            except StopIteration:
                return RuntimeLogRecord(
                    time=time.time(),
                    msg="llassembly executed",
                    next_command=NEXT_COMMAND_FINISHED,
                )

            if call.sub_agent.name != agent_name:
                raise RuntimeError(
                    f"Expected sub-agent '{agent_name}' but got '{call.sub_agent.name}'"
                )
            last_agent_to_infer = agent_name
            last_infer_hook = call.infer_result

        if record.msg.startswith("sub-agent-result"):
            if last_agent_to_infer is None or last_infer_hook is None:
                continue
            last_infer_hook(
                last_agent_to_infer,
                json.loads(record.payload) if record.payload else {},
            )
            last_agent_to_infer = None
            last_infer_hook = None

    if last_agent_to_infer is not None and last_infer_hook is not None:
        last_infer_hook(last_agent_to_infer, {})

    while not emulator.is_finished():
        try:
            next_result = next(calls_iter)
            if isinstance(next_result, emulator_module.SubAgentContext):
                agent = next_result.sub_agent
                output_keys = next_result.output_keys
                objective = agent.objective or "No description available"
                output_desc = "\n".join(
                    [f"{k}: {v}" for k, v in next_result.sub_agent.outputs_spec.items()]
                )
                output_keys_str = (
                    " ".join([f'"<{key}>"' for key in output_keys])
                    if output_keys
                    else ""
                )

                next_command_text = NEXT_COMMAND_EXECUTE_SUB_AGENT.format(
                    agent_name=agent.name,
                    agent_path=config.workdir_path / "agents" / agent.name,
                    objective=objective,
                    output_desc=output_desc,
                    output_args=output_keys_str,
                    loop_id=config.loop_id,
                )
                if workdir:
                    next_command_text = (
                        f"Save result to: {workdir}\n{next_command_text}"
                    )
                return RuntimeLogRecord(
                    time=time.time(),
                    msg=f"sub-agent-iteration:{agent.name}",
                    next_command=next_command_text,
                )
        except StopIteration:
            pass

    return RuntimeLogRecord(
        time=time.time(), msg="llassembly executed", next_command=NEXT_COMMAND_FINISHED
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Get the next instruction for the LLAssembly loop."
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="Optional arguments: a workdir path or sub-agent output values.",
    )
    return parser.parse_args()


def main():
    parsed = _parse_args()
    extra_args = parsed.args

    loop_id = os.environ.get("LLASSEMBLY_LOOP_ID")

    if not loop_id:
        _step_print_loop_id()
        return

    config = Config.init_by_loop_id(loop_id)

    if not config.workdir_runtime_log_path.exists() and not extra_args:
        _step_ask_context_state(config)
        return

    if record := _try_step_create_runtime_log(config):
        print(record.next_command)
        return

    if record := _try_step_run_init_sub_agents(config):
        print(record.next_command)
        return

    if record := _try_step_infer_sub_agent_result(extra_args):
        # Only writes log, no actions for the agent
        _locked_write_record(config, record)

    log_records = []
    for log_line in config.workdir_runtime_log_path.read_text().strip().splitlines():
        log_line = log_line.strip()
        if not log_line:
            continue
        try:
            log_records.append(RuntimeLogRecord(**json.loads(log_line)))
        except (json.JSONDecodeError, TypeError) as exc:
            raise NextInstructionError(
                f"Corrupt runtime log entry: {exc!r} — line: {log_line!r}"
            ) from exc

    if record := _step_execute_llassembly(config, log_records):
        _locked_write_record(config, record)
        print(record.next_command)
        return


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
