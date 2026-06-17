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
from typing import Any, Optional, Required, Self, TypedDict, cast

# Will be initialized based on the current path of the script
_emulator_module: Any = None


LLASSEMBLY_LOOP_PATH = "LLASSEMBLY_LOOP_PATH"
LLASSEMBLY_LOOP_ID = "LLASSEMBLY_LOOP_ID"
LLASSEMBLY_DEFAULT_WORKDIR = "/tmp/"
LLASSEMBLY_RUNTIME_LOG_NAME = "runtime.log"
LLASSEMBLY_PLAN_NAME = "plan.llassembly"

# ---------------------------------------------------------------------------
# next_command templates
# ---------------------------------------------------------------------------
# Every instruction this driver prints back to the orchestrator lives here as a
# template. Step functions fill the placeholders and the wording stays
# consistent across phases. Each instruction (except the terminal one)
# telling the caller to re-run this driver, reusing the same loop id, so the
# resumable state machine can advance to the next step.


NEXT_COMMAND_LOOP_ID = textwrap.dedent("""
    Stage 1:

    The following LLASSEMBLY_LOOP_ID={loop_id} must be used for all future calls to `scripts/get_next_instruction.py`. 
    Re-run `LLASSEMBLY_LOOP_ID={loop_id} python scripts/get_next_instruction.py` with no arguments and LLASSEMBLY_LOOP_ID
    """)

NEXT_COMMAND_GET_CONTEXT = textwrap.dedent("""
    Stage 2:

    Give the full directory path where work results should be saved — this is where your workdir/root directory
    is for results of the request, NOT the llassembly skill, agents, or scripts directory.

    Re-run `LLASSEMBLY_LOOP_ID={loop_id} python scripts/get_next_instruction.py "<path>"` write path as 1 argument.
    """)

NEXT_COMMAND_CREATE_PLAN = textwrap.dedent("""
    Stage 3:
    Run the Task tool to launch the `llassembly-control-flow` as a separate/nested sub-agent.

    After `llassembly-control-flow` returns re-run `LLASSEMBLY_LOOP_ID={loop_id} python scripts/get_next_instruction.py`
    with no arguments yourself in the current session to continue.

    Execute the agent defined in `agents/llassembly-control-flow.md` and pass the following instructions
    to `llassembly-control-flow`:
    1. Translate the goal into executable control-flow exactly following `agents/llassembly-control-flow.md` specification and write it to: `{plan_path}`
    2. Your only task is to write that file. As soon as the file is written, 
    STOP and return control to the caller. Do NOT run any scripts, do NOT execute the control-flows
    and do NOT call `scripts/get_next_instruction.py` yourself.

""")

NEXT_COMMAND_GENERATE_SUB_AGENT = textwrap.dedent("""
    Stage 4:
    Run the Task tool to launch the `llassembly-generate-sub-agents` separatly to create required
    for control-flow sub-agent called: `{sub_agent_name}`

    After `llassembly-generate-sub-agents` returns re-run `LLASSEMBLY_LOOP_ID={loop_id} python scripts/get_next_instruction.py`
    with no arguments yourself in the current session to continue.

    Execute the agent defined in `agents/llassembly-generate-sub-agents.md`
    and pass ONLY this 2 instructions to `llassembly-generate-sub-agents` :
    1. Create sub-agent definition:
        - sub-agent name: `{sub_agent_name}`
        - sub-agent path: `{sub_agent_path}`
        - sub-agent objective: `{sub_agent_objective}`
    2. Your only task is to create that file. As soon as the file is written, STOP and return
    control to the caller. Do NOT run any scripts, and do NOT call `scripts/get_next_instruction.py` yourself.

    The control-flow plan that orchestrates generated sub-agents: {plan_path}

""")

NEXT_COMMAND_EXECUTE_SUB_AGENT = textwrap.dedent("""
    Stage 5:
    Run the Task tool to launch a sub-agent `{agent_name}` with the following instructions:
    - Execute the agent defined at: `{agent_path}`
    - Objective: {objective}
    - Expected outputs:
    {output_desc}

    When the sub-agent is done, re-run `LLASSEMBLY_LOOP_ID={loop_id} python scripts/get_next_instruction.py {output_args}`
    in the current session, passing the result of sub-agent as arguments one by one.
""")

# Terminal — the plan ran to completion; the goal is achieved.
NEXT_COMMAND_FINISHED = "Execution finished. Goal is achieved."


INCORRECT_SUB_AGENT_RESULTS = textwrap.dedent("""
    Stage 5:

    Incorrect output was given for already executed sub-agent `{agent_name}`:
    - Expected outputs:
    {output_desc}
    - Given output:
    {arguments}

    Re-run `LLASSEMBLY_LOOP_ID={loop_id} python scripts/get_next_instruction.py {output_args}`
    passing correct arguments 
""")


@dataclass
class Config:
    loop_id: str
    workdir_base_path: Path
    workdir_path: Path
    workdir_runtime_log_path: Path
    workdir_plan_path: Path

    @classmethod
    def init_by_loop_id(cls, loop_id: str) -> Self:
        workdir_base_path = os.environ.get(
            LLASSEMBLY_LOOP_PATH, LLASSEMBLY_DEFAULT_WORKDIR
        )
        assert workdir_base_path
        workdir_path = Path(workdir_base_path).resolve() / f"llassembly/{loop_id}"

        return cls(
            loop_id=loop_id,
            workdir_base_path=Path(workdir_base_path).resolve(),
            workdir_path=workdir_path,
            workdir_runtime_log_path=workdir_path / LLASSEMBLY_RUNTIME_LOG_NAME,
            workdir_plan_path=workdir_path / LLASSEMBLY_PLAN_NAME,
        )


# ---------------------------------------------------------------------------
# Runtime log helpers
# ---------------------------------------------------------------------------


@dataclass
class RuntimeLogRecord:
    time: float
    msg: str
    next_command: str | None = None
    payload: dict | None = None


@dataclass
class RuntimeLogCreated(RuntimeLogRecord):
    class Payload(TypedDict):
        workdir_path: Optional[str]

    payload: RuntimeLogCreated.Payload
    msg: str = "runtime-log-created"


@dataclass
class RuntimeLogSubAgentMissing(RuntimeLogRecord):
    msg: str = "sub-agent-missing"


@dataclass
class RuntimeLogInferResult(RuntimeLogRecord):
    class Payload(TypedDict):
        args: Required[list[str]]

    payload: RuntimeLogInferResult.Payload
    msg: str = "sub-agent-result"


@dataclass
class RuntimeLogExecuteSubAgent(RuntimeLogRecord):
    class Payload(TypedDict):
        agent_name: Required[str]

    payload: RuntimeLogExecuteSubAgent.Payload
    msg: str = "sub-agent-iteration"


@dataclass
class RuntimeLogExecutionFinished(RuntimeLogRecord):
    msg: str = "llassembly-executed"


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


def get_log_records(config: Config) -> list[RuntimeLogRecord]:
    log_records = []
    for log_line in config.workdir_runtime_log_path.read_text().strip().splitlines():
        if not (log_line := log_line.strip()):
            continue
        try:
            log_records.append(RuntimeLogRecord(**json.loads(log_line)))
        except (json.JSONDecodeError, TypeError) as exc:
            raise NextInstructionError(
                f"Corrupt runtime log entry: {exc!r} — line: {log_line!r}"
            ) from exc
    return log_records


# ---------------------------------------------------------------------------
# Custom exception – follows init_agentic_loop.py pattern of defining a
# local error class for callers that import this module.
# ---------------------------------------------------------------------------


class NextInstructionError(Exception):
    pass


class InferResultArgumentsError(Exception):
    pass


# ---------------------------------------------------------------------------
# Emulator state
# ---------------------------------------------------------------------------


def infer_sub_agent_result(loop_id, last_sub_agent_context, args):
    if len(last_sub_agent_context.output_keys) != len(args):
        output_desc = "\n".join(
            [
                f"{k}: {v}"
                for k, v in last_sub_agent_context.sub_agent.outputs_spec.items()
            ]
        )
        output_keys_str = " ".join(
            [f'"<{key}>"' for key in last_sub_agent_context.output_keys or []]
        )
        print(
            INCORRECT_SUB_AGENT_RESULTS.format(
                agent_name=last_sub_agent_context.sub_agent.name,
                arguments=" ".join(args),
                output_desc=output_desc,
                output_args=output_keys_str,
                loop_id=loop_id,
            )
        )
        raise InferResultArgumentsError()
    else:
        last_sub_agent_context.infer_result_hook(
            last_sub_agent_context.sub_agent.name,
            args,
        )


# ---------------------------------------------------------------------------
# Decision functions – one per loop phase, keeping ``main()`` linear
# ---------------------------------------------------------------------------


def _step_print_loop_id() -> str:
    loop_id = f"{int(time.time())}_{''.join(secrets.choice(string.ascii_letters) for _ in range(6))}"
    return NEXT_COMMAND_LOOP_ID.format(loop_id=loop_id)


def _step_ask_context_state(config: Config) -> str:
    return NEXT_COMMAND_GET_CONTEXT.format(loop_id=config.loop_id)


def _try_step_create_runtime_log(
    config: Config, reinit: bool = False
) -> RuntimeLogCreated | None:
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

    extra_args = _parse_args().args
    return RuntimeLogCreated(
        time=time.time(),
        next_command=next_command,
        payload={"workdir_path": extra_args[0] if extra_args else None},
    )


def _import_emulator() -> Any:
    global _emulator_module
    if _emulator_module is not None:
        return _emulator_module
    script_dir = Path(__file__).resolve().parent
    spec = util.spec_from_file_location("emulator", script_dir / "emulator.py")
    em: Any = util.module_from_spec(spec)  # type: ignore
    spec.loader.exec_module(em)  # type: ignore
    _emulator_module = em
    return em


def _try_step_run_init_sub_agents(
    config: Config,
) -> RuntimeLogSubAgentMissing | RuntimeLogRecord | None:
    plan_path = config.workdir_plan_path

    if not plan_path.exists():
        return _try_step_create_runtime_log(config, reinit=True)

    emulator = _import_emulator().Emulator.from_code(plan_path.read_text())
    for sub_agent in emulator.get_sub_agents().values():
        if sub_agent.include_path.startswith("general/"):
            sub_agent_path = (
                config.workdir_path / "agents" / Path(f"{sub_agent.name}.md")
            )
            if not sub_agent_path.exists():
                next_command = NEXT_COMMAND_GENERATE_SUB_AGENT.format(
                    sub_agent_path=sub_agent_path,
                    sub_agent_name=sub_agent.name,
                    sub_agent_objective=sub_agent.objective,
                    loop_id=config.loop_id,
                    plan_path=config.workdir_plan_path,
                )
                return RuntimeLogSubAgentMissing(
                    time=time.time(), next_command=next_command
                )

    return None


def _try_step_infer_sub_agent_result(args: list[str]) -> RuntimeLogInferResult | None:
    # If any args - infer them later from log, else None
    if not args:
        return None

    return RuntimeLogInferResult(time=time.time(), payload={"args": args})


def _step_execute_llassembly(
    config: Config, records: list[RuntimeLogRecord]
) -> RuntimeLogExecuteSubAgent | RuntimeLogExecutionFinished | None:
    if not config.workdir_plan_path.exists():
        raise FileNotFoundError(f"Plan file not found: {config.workdir_plan_path}")

    emulator_module = _import_emulator()
    emulator = emulator_module.Emulator.from_code(config.workdir_plan_path.read_text())
    calls_iter = emulator.iter_tool_calls()

    last_sub_agent_context = None
    workdir = None
    for record in records:
        if last_sub_agent_context:
            if record.msg.startswith(RuntimeLogInferResult.msg):
                args = (record.payload or {}).get("args", [])
                infer_sub_agent_result(config.loop_id, last_sub_agent_context, args)
            else:
                infer_sub_agent_result(config.loop_id, last_sub_agent_context, [])
            last_sub_agent_context = None
            continue

        if record.msg.startswith(RuntimeLogCreated.msg):
            workdir = (record.payload or dict()).get("workdir_path")

        if record.msg.startswith(RuntimeLogExecuteSubAgent.msg):
            agent_name: str | None = (record.payload or dict()).get("agent_name")
            assert agent_name, "Inconsitent runtime log, no agent_name found"

            try:
                call = next(calls_iter)
            except StopIteration:
                return RuntimeLogExecutionFinished(
                    time=time.time(),
                    next_command=NEXT_COMMAND_FINISHED,
                )

            if call.sub_agent.name != agent_name:
                raise RuntimeError(
                    f"Expected sub-agent '{agent_name}' but got '{call.sub_agent.name}'"
                )
            last_sub_agent_context = call

    if last_sub_agent_context:
        infer_sub_agent_result(config.loop_id, last_sub_agent_context, [])

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
                output_keys_str = " ".join([f'"<{key}>"' for key in output_keys or []])

                next_command_text = NEXT_COMMAND_EXECUTE_SUB_AGENT.format(
                    agent_name=agent.name,
                    agent_path=config.workdir_path
                    / "agents"
                    / Path(f"{agent.name}.md"),
                    objective=objective,
                    output_desc=output_desc,
                    output_args=output_keys_str,
                    loop_id=config.loop_id,
                )
                if workdir:
                    next_command_text = (
                        f"Save result to: {workdir}\n{next_command_text}"
                    )
                return RuntimeLogExecuteSubAgent(
                    time=time.time(),
                    next_command=next_command_text,
                    payload={"agent_name": agent.name},
                )
        except StopIteration:
            pass

    return RuntimeLogExecutionFinished(
        time=time.time(), next_command=NEXT_COMMAND_FINISHED
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

    loop_id = os.environ.get(LLASSEMBLY_LOOP_ID)

    if not loop_id:
        print(_step_print_loop_id())
        return

    config = Config.init_by_loop_id(loop_id)

    if not config.workdir_runtime_log_path.exists() and not extra_args:
        print(_step_ask_context_state(config))
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

    if record := _step_execute_llassembly(config, get_log_records(config)):
        _locked_write_record(config, record)
        print(record.next_command)
        return


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
