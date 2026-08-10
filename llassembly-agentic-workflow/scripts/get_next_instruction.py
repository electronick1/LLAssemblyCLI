"""Resumable state-machine driver for the LLAssembly agentic workflow.

Each invocation inspects the workflow state (a per-workflow runtime directory keyed by
the ``--llassembly-session`` argument plus its append-only ``runtime.log``) and prints
next action for the orchestrator to perform, then exits. The orchestrator performs that
action and re-runs the driver.

The stages (and the handlers that own them):
  Stage 1  setup      -> InitBootstrapHandler / WorkflowIdHandler / AskWorkdirContextHandler
  Stage 2  plan       -> CreatePlanHandler (asks the control-flow worker to write the plan)
  Stage 3  sub-agents -> GenerateSubAgentsHandler (generate missing sub-agent defs)
  Stage 4  save       -> DoSaveSkillHandler (writes a reusable skill if Stage 1 logged --save)
  Stage 5  execute    -> ExecuteHandler (replay log, emit next sub-agent / finished)

"""

from __future__ import annotations

import abc
import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import re
import secrets
import shutil
import string
import sys
import time
from dataclasses import dataclass
from importlib import util
from pathlib import Path
from typing import Any, NoReturn, Required, Self, TypedDict

# Will be initialized based on the current path of the script
emulator_module: Any = None

# Root of the (built) skill: the directory containing ``scripts/`` and ``assets/``.
SKILL_ROOT = Path(__file__).resolve().parent.parent

LLASSEMBLY_WORKFLOW_PATH = "LLASSEMBLY_WORKFLOW_PATH"
LLASSEMBLY_DEFAULT_RUNTIME = "/tmp/"
LLASSEMBLY_RUNTIME_LOG_NAME = "runtime.log"
LLASSEMBLY_PLAN_STEM = "plan_llassembly"
LLASSEMBLY_SKILL_MD_NAME = "SKILL.md"
LLASSEMBLY_SUB_AGENTS_REL = Path("references") / "agents"
LLASSEMBLY_RUNTIME_RESULT_REL = Path("runtime_result")

CLI_SKILLS_DIRS = {
    "claude": Path(".claude") / "skills",
    "opencode": Path(".opencode") / "skills",
    "codex": Path(".agents") / "skills",
    "pi": Path(".agents") / "skills",
}


class NextInstructionError(Exception):
    pass


class InferResultArgumentsError(Exception):
    pass


class PlanLoadError(Exception):
    """The emulator could not load the plan file. Carries the emulator's own message."""


class EmulatorLoadError(Exception):
    """emulator.py could not be imported at all. Carries the missing dependency's name."""


# ---------------------------------------------------------------------------
# agent_instruction templates
# ---------------------------------------------------------------------------


def _internal_asset(name: str) -> str:
    # Security note: `name` must NOT be from the user or AI input, no path resolution here.
    path = SKILL_ROOT / "assets" / name
    try:
        return path.read_text()
    except OSError as exc:
        raise NextInstructionError(f"Missing skill asset: {path} ({exc})") from exc


PROMPT_REPORT_WORKFLOW_ID = _internal_asset("command_stage_1/workflow_id.txt")
PROMPT_REPORT_WORKFLOW_ID_INIT = _internal_asset("command_stage_1/workflow_id_init.txt")
PROMPT_GET_WORKDIR_CONTEXT = _internal_asset("command_stage_1/get_context.txt")
PROMPT_ASK_TO_CREATE_PLAN = _internal_asset("command_stage_2/create_plan.txt")
PROMPT_GENERATE_SUB_AGENTS = _internal_asset("command_stage_3/generate_sub_agent.txt")
PROMPT_GENERATE_SUB_AGENT_ITEM = _internal_asset(
    "command_stage_3/generate_sub_agent_item.txt"
)
PROMPT_SKILL_SAVE_INVALID = _internal_asset("command_stage_4/save_invalid.txt")
PROMPT_SKILL_SAVE_EXISTS = _internal_asset("command_stage_4/save_exists.txt")
PROMPT_SKILL_SAVED = _internal_asset("command_stage_4/saved.txt")
PROMPT_SKILL_SAVE_INVALID_CLI = _internal_asset("command_stage_4/save_invalid_cli.txt")
PROMPT_EXECUTE_SUB_AGENT = _internal_asset("command_stage_5/execute_sub_agent.txt")
PROMPT_FINISHED = _internal_asset("command_stage_5/finished.txt")
PROMPT_INCORRECT_SUB_AGENT_RESULTS = _internal_asset(
    "command_stage_5/incorrect_sub_agent_results.txt"
)

# Fallbacks: what the driver prints when it cannot reach a stage at all, because the plan
# will not load or emulator.py will not import.
PROMPT_FALLBACK_PLAN_LOAD_FAILED = _internal_asset(
    "fallback_cases/plan_load_failed.txt"
)
PROMPT_FALLBACK_EMULATOR_UNIMPORTABLE = _internal_asset(
    "fallback_cases/emulator_unimportable.txt"
)


# ---------------------------------------------------------------------------
# Runtime log records
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class RuntimeLogRecord:
    msg: str
    time: float = dataclasses.field(default_factory=time.time)
    agent_instruction: str | None = None
    payload: dict | None = None

    @classmethod
    def load(cls, log_path: Path) -> list[RuntimeLogRecord]:
        if not log_path.exists():
            return []
        records = []
        for log_line in log_path.read_text().strip().splitlines():
            if not (log_line := log_line.strip()):
                continue
            try:
                records.append(RuntimeLogRecord(**json.loads(log_line)))
            except (json.JSONDecodeError, TypeError) as exc:
                raise NextInstructionError(
                    f"Corrupt runtime log entry: {exc!r} — line: {log_line!r}"
                ) from exc
        return records

    @classmethod
    def prompt(cls, text: str) -> RuntimeLogRecord:
        return RuntimeLogRecord(msg="prompt", agent_instruction=text)

    @classmethod
    def payloads(cls, records: list[RuntimeLogRecord]) -> list[dict]:
        return [record.payload or {} for record in records if record.msg == cls.msg]

    @classmethod
    def first_payload(cls, records: list[RuntimeLogRecord]) -> dict | None:
        payloads = cls.payloads(records)
        return payloads[0] if payloads else None

    @classmethod
    def last_payload(cls, records: list[RuntimeLogRecord]) -> dict | None:
        payloads = cls.payloads(records)
        return payloads[-1] if payloads else None

    @staticmethod
    def kinds(records: list[RuntimeLogRecord]) -> set[str]:
        return {record.msg for record in records}


@dataclass(kw_only=True)
class RuntimeLogCreated(RuntimeLogRecord):
    class Payload(TypedDict):
        workdir_path: str | None

    payload: RuntimeLogCreated.Payload
    msg: str = "runtime-log-created"

    @classmethod
    def workdir(cls, records: list[RuntimeLogRecord]) -> str | None:
        return (cls.first_payload(records) or {}).get("workdir_path")


@dataclass(kw_only=True)
class RuntimeLogSubAgentMissing(RuntimeLogRecord):
    msg: str = "sub-agent-missing"


@dataclass(kw_only=True)
class RuntimeLogInferResult(RuntimeLogRecord):
    class Payload(TypedDict):
        iteration: Required[int]
        args: Required[dict[str, Any]]
        result_file: str | None

    payload: RuntimeLogInferResult.Payload
    msg: str = "sub-agent-result"


@dataclass(kw_only=True)
class RuntimeLogExecuteSubAgent(RuntimeLogRecord):
    class Payload(TypedDict):
        agent_name: Required[str]
        iteration: Required[int]
        result_id: Required[str]
        expected_output: Required[dict[str, str]]

    payload: RuntimeLogExecuteSubAgent.Payload
    msg: str = "sub-agent-iteration"


@dataclass(kw_only=True)
class RuntimeLogExecutionFinished(RuntimeLogRecord):
    msg: str = "llassembly-executed"


@dataclass(kw_only=True)
class RuntimeLogSaveRequested(RuntimeLogRecord):
    class Payload(TypedDict):
        skill_name: Required[str]
        cli_type: str | None

    payload: RuntimeLogSaveRequested.Payload
    msg: str = "save-requested"


# ---------------------------------------------------------------------------
# Runtime log helpers
# ---------------------------------------------------------------------------


def locked_write_record(ctx: RequestContext, record: dict | RuntimeLogRecord) -> None:
    if isinstance(record, RuntimeLogRecord):
        record = dataclasses.asdict(record)
    ctx.runtime_log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ctx.runtime_log_path, "a") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            fd.write(json.dumps(record) + "\n")
            fd.flush()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def read_part(name: str) -> str:
    return resolved_within(SKILL_ROOT / "references", name).read_text()


def slugify(text: str) -> str:
    return (
        re.compile(r"[^a-z0-9]+")
        .sub("-", (text or "").lower())
        .strip("-")[:48]
        .strip("-")
    )


def resolve_skills_dir(ctx: RequestContext, cli_type: str) -> Path | None:
    workdir = RuntimeLogCreated.workdir(RuntimeLogRecord.load(ctx.runtime_log_path))
    if not workdir:
        return None
    return Path(workdir).resolve() / CLI_SKILLS_DIRS[cli_type]


def output_json_schema(
    name: str, outputs_spec: dict[str, str], indent: str = "    "
) -> str:
    properties = {}
    for key, text in outputs_spec.items():
        text = text.strip("\\'\"")
        properties[key] = {"type": "string", "description": text}
    schema = json.dumps(
        {
            "title": f"{name} outputs",
            "type": "object",
            "properties": properties,
            "required": list(outputs_spec),
            "additionalProperties": False,
        },
        indent=2,
    )
    return "\n".join(indent + line for line in schema.splitlines())


# ---------------------------------------------------------------------------
# Emulator + shared step helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def plan_errors(plan_path: Path):
    """Report anything raised while loading or scanning the plan as PlanLoadError.

    Stage 3 and Stage 5 both build an emulator from the same file and owe the caller the
    same message; this is that message, written once.
    """
    try:
        yield
    except Exception as exc:
        raise PlanLoadError(f"{plan_path}: {type(exc).__name__}: {exc}") from exc


def import_emulator() -> Any:
    global emulator_module
    if emulator_module is not None:
        return emulator_module
    script_dir = Path(__file__).resolve().parent
    spec = util.spec_from_file_location("emulator", script_dir / "emulator.py")
    assert spec and spec.loader
    emulator: Any = util.module_from_spec(spec)
    sys.modules[spec.name] = emulator
    try:
        spec.loader.exec_module(emulator)
    except ImportError as exc:
        raise EmulatorLoadError(exc.name or str(exc)) from exc
    emulator_module = emulator
    return emulator


def plan_filename() -> str:
    return f"{LLASSEMBLY_PLAN_STEM}.{import_emulator().PLAN_EXTENSION}"


def result_is_complete(values: Any, output_keys: list[str]) -> bool:
    if not output_keys:
        return True
    return isinstance(values, dict) and all(key in values for key in output_keys)


def demand_sub_agent_result(ctx: RequestContext, payload: dict) -> NoReturn:
    agent_name = payload["agent_name"]
    print(
        PROMPT_INCORRECT_SUB_AGENT_RESULTS.format(
            agent_name=agent_name,
            agent_path=ctx.sub_agent_path(agent_name),
            result_path=ctx.result_file(payload["result_id"]),
            output_schema=output_json_schema(agent_name, payload["expected_output"]),
            workflow_id=ctx.workflow_id,
        )
    )
    raise InferResultArgumentsError()


def create_runtime_log(ctx: RequestContext, reinit: bool = False) -> bool:
    ctx.runtime_path.mkdir(parents=True, exist_ok=True)
    (ctx.runtime_path / LLASSEMBLY_SUB_AGENTS_REL).mkdir(parents=True, exist_ok=True)
    ctx.result_dir.mkdir(parents=True, exist_ok=True)

    if ctx.runtime_log_path.exists() and not reinit:
        return False

    ctx.runtime_log_path.unlink(missing_ok=True)
    ctx.runtime_log_path.touch()

    return True


def resolved_within(base_path: Path, relative_path: str | Path) -> Path:
    full_path = base_path / relative_path
    if not full_path.resolve().is_relative_to(base_path.resolve()):
        raise NextInstructionError(
            f"Refusing the path outside of {base_path}: {relative_path}"
        )
    return full_path.resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Get the next instruction for the LLAssembly workflow."
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="Optional arguments.",
    )
    parser.add_argument("--llassembly-session", default=None)
    parser.add_argument("--init", default=None)
    parser.add_argument("--save", default=None)
    parser.add_argument("--whoamcli", default=None)
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------


@dataclass
class RequestContext:
    parsed: argparse.Namespace
    workflow_id: str
    runtime_base_path: Path
    runtime_path: Path
    runtime_log_path: Path
    runtime_plan_path: Path
    # True when the orchestrator carried the id forward via --llassembly-session
    adopted: bool = False
    records: list[RuntimeLogRecord] | None = None

    @classmethod
    def create(cls, parsed: argparse.Namespace) -> Self:
        """Resolve the workspace this invocation addresses, from the command line."""
        workflow_id = parsed.llassembly_session or cls.generate_workflow_id()
        base = os.environ.get(LLASSEMBLY_WORKFLOW_PATH, LLASSEMBLY_DEFAULT_RUNTIME)
        assert base
        base_path = Path(base).resolve()
        runtime_path = resolved_within(base_path / "llassembly", workflow_id)
        return cls(
            parsed=parsed,
            workflow_id=workflow_id,
            adopted=bool(parsed.llassembly_session),
            runtime_base_path=base_path,
            runtime_path=runtime_path,
            runtime_log_path=resolved_within(runtime_path, LLASSEMBLY_RUNTIME_LOG_NAME),
            runtime_plan_path=resolved_within(runtime_path, plan_filename()),
        )

    @property
    def result_dir(self) -> Path:
        return self.runtime_path / LLASSEMBLY_RUNTIME_RESULT_REL

    def sub_agent_path(self, agent_name: str) -> Path:
        return resolved_within(
            self.runtime_path / LLASSEMBLY_SUB_AGENTS_REL, f"{agent_name}.md"
        )

    def result_file(self, result_id: str) -> Path:
        return resolved_within(self.result_dir, f"{result_id}.json")

    @staticmethod
    def generate_workflow_id() -> str:
        return f"{int(time.time())}_{RequestContext.random_token()}"

    @staticmethod
    def random_token() -> str:
        return "".join(secrets.choice(string.ascii_letters) for _ in range(6))


# ---------------------------------------------------------------------------
# Stage handlers
# ---------------------------------------------------------------------------


class StageHandler(abc.ABC):
    def __init__(self, ctx: RequestContext):
        self.ctx = ctx

    @abc.abstractmethod
    def handle(self) -> RuntimeLogRecord: ...


class InitBootstrapHandler(StageHandler):
    def handle(self) -> RuntimeLogRecord:
        ctx = self.ctx
        if ctx.parsed.init is None:
            # Not a prebuilt run — fall through to the normal setup chain.
            return WorkflowIdHandler(ctx).handle()

        src = Path(ctx.parsed.init).resolve()
        plan_name = ctx.runtime_plan_path.name
        if not (src / "references" / plan_name).exists():
            return RuntimeLogRecord.prompt(
                f'--init: no prebuilt skill at "{src}" '
                f"(missing references/{plan_name}). Stop execution, report to the user"
            )

        if not ctx.runtime_plan_path.exists():
            self._hydrate(src, plan_name)

        return RuntimeLogRecord.prompt(
            PROMPT_REPORT_WORKFLOW_ID_INIT.format(workflow_id=ctx.workflow_id)
        )

    def _hydrate(self, src: Path, plan_name: str) -> None:
        ctx = self.ctx
        ctx.runtime_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, ctx.runtime_path, dirs_exist_ok=True)

        hydrated_plan = ctx.runtime_path / "references" / plan_name
        if hydrated_plan.exists():
            shutil.move(str(hydrated_plan), str(ctx.runtime_plan_path))

        args = ctx.parsed.args
        locked_write_record(
            ctx,
            RuntimeLogCreated(payload={"workdir_path": args[0] if args else None}),
        )


class WorkflowIdHandler(StageHandler):
    def handle(self) -> RuntimeLogRecord:
        ctx = self.ctx
        create_runtime_log(ctx)

        if ctx.parsed.save is not None:
            locked_write_record(
                ctx,
                RuntimeLogSaveRequested(
                    payload={
                        "skill_name": ctx.parsed.save,
                        "cli_type": ctx.parsed.whoamcli,
                    },
                ),
            )

        if ctx.adopted:
            return AskWorkdirContextHandler(ctx).handle()
        return RuntimeLogRecord.prompt(
            PROMPT_REPORT_WORKFLOW_ID.format(workflow_id=ctx.workflow_id)
        )


class AskWorkdirContextHandler(StageHandler):
    def handle(self) -> RuntimeLogRecord:
        ctx = self.ctx
        assert ctx.parsed.init is None, "Init must be handled before ctx"
        records = RuntimeLogRecord.load(ctx.runtime_log_path)
        # Log-driven: once the workdir has been captured (recorded), move on.
        if RuntimeLogCreated.msg in RuntimeLogRecord.kinds(records):
            return CreatePlanHandler(ctx).handle()

        if ctx.parsed.args:
            # This run supplied the workdir path — record it, then move on.
            locked_write_record(
                ctx,
                RuntimeLogCreated(payload={"workdir_path": ctx.parsed.args[0]}),
            )
            return CreatePlanHandler(ctx).handle()

        return RuntimeLogRecord.prompt(
            PROMPT_GET_WORKDIR_CONTEXT.format(workflow_id=ctx.workflow_id)
        )


class CreatePlanHandler(StageHandler):
    def handle(self) -> RuntimeLogRecord:
        ctx = self.ctx
        # The plan file is the source of truth: once control-flow has written it, move on.
        if ctx.runtime_plan_path.exists():
            return GenerateSubAgentsHandler(ctx).handle()

        return RuntimeLogRecord.prompt(
            PROMPT_ASK_TO_CREATE_PLAN.format(
                plan_path=ctx.runtime_plan_path,
                workflow_id=ctx.workflow_id,
            )
        )


class GenerateSubAgentsHandler(StageHandler):
    def handle(self) -> RuntimeLogRecord:
        ctx = self.ctx
        plan_path = ctx.runtime_plan_path

        with plan_errors(plan_path):
            emulator = import_emulator().Emulator.from_code(plan_path.read_text())
            declared = emulator.get_sub_agents().values()

        missing = []
        for sub_agent in declared:
            path = ctx.sub_agent_path(sub_agent.name)
            if not path.exists():
                missing.append(
                    PROMPT_GENERATE_SUB_AGENT_ITEM.format(
                        sub_agent_name=sub_agent.name,
                        sub_agent_path=path,
                        sub_agent_objective=sub_agent.objective,
                    ).strip()
                )

        if not missing:
            # All sub-agents present, move on.
            return DoSaveSkillHandler(ctx).handle()

        return RuntimeLogSubAgentMissing(
            agent_instruction=PROMPT_GENERATE_SUB_AGENTS.format(
                count=len(missing),
                sub_agents_block="\n".join(missing),
                workflow_id=ctx.workflow_id,
                plan_path=plan_path,
            ),
        )


class DoSaveSkillHandler(StageHandler):
    def handle(self) -> RuntimeLogRecord:
        ctx = self.ctx
        # The most recent recorded save intent, or None if none was ever recorded.
        request = RuntimeLogSaveRequested.last_payload(
            RuntimeLogRecord.load(ctx.runtime_log_path)
        )
        if request is None:
            return InferResultFromPreviousAgentHandler(ctx).handle()

        skill_name = slugify(request.get("skill_name") or "")
        if not skill_name:
            return RuntimeLogRecord.prompt(PROMPT_SKILL_SAVE_INVALID)

        cli_type = (request.get("cli_type") or "").lower()
        if cli_type not in CLI_SKILLS_DIRS:
            return RuntimeLogRecord.prompt(
                PROMPT_SKILL_SAVE_INVALID_CLI.format(
                    cli_types=", ".join(sorted(CLI_SKILLS_DIRS)),
                    workflow_id=ctx.workflow_id,
                )
            )

        skills_dir = resolve_skills_dir(ctx, cli_type)
        if skills_dir is None:
            # No project workdir recorded — skip the save, continue as a temporary workflow.
            return InferResultFromPreviousAgentHandler(ctx).handle()

        dest = resolved_within(skills_dir, skill_name)
        if (dest / LLASSEMBLY_SKILL_MD_NAME).exists():
            return RuntimeLogRecord.prompt(
                PROMPT_SKILL_SAVE_EXISTS.format(skill_name=skill_name, skill_dir=dest)
            )

        dest_sub_agents = dest / LLASSEMBLY_SUB_AGENTS_REL
        dest_sub_agents.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            ctx.runtime_plan_path,
            dest / "references" / ctx.runtime_plan_path.name,
        )
        src_sub_agents = ctx.runtime_path / LLASSEMBLY_SUB_AGENTS_REL
        if src_sub_agents.is_dir():
            for sub_agent_file in src_sub_agents.glob("*.md"):
                shutil.copy2(
                    sub_agent_file,
                    resolved_within(dest_sub_agents, sub_agent_file.name),
                )

        (dest / LLASSEMBLY_SKILL_MD_NAME).write_text(
            read_part("prebuilt-skill.md").replace("@@SKILL_NAME@@", skill_name)
        )
        return RuntimeLogRecord.prompt(
            PROMPT_SKILL_SAVED.format(skill_name=skill_name, skill_dir=dest)
        )


class InferResultFromPreviousAgentHandler(StageHandler):
    def handle(self) -> RuntimeLogRecord:
        ctx = self.ctx
        records = RuntimeLogRecord.load(ctx.runtime_log_path)
        dispatches = RuntimeLogExecuteSubAgent.payloads(records)
        answered = {
            payload.get("iteration")
            for payload in RuntimeLogInferResult.payloads(records)
        }
        for payload in dispatches:
            assert "result_id" in payload
            assert "iteration" in payload
            assert "expected_output" in payload

        for payload in dispatches:
            if payload["iteration"] in answered:
                continue
            if not payload["expected_output"]:
                continue
            path = ctx.result_file(payload["result_id"])
            try:
                values = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                demand_sub_agent_result(ctx, payload)
            if not result_is_complete(values, list(payload["expected_output"])):
                demand_sub_agent_result(ctx, payload)
            locked_write_record(
                ctx,
                RuntimeLogInferResult(
                    payload={
                        "iteration": payload["iteration"],
                        "args": values,
                        "result_file": path.name,
                    },
                ),
            )
        return ExecuteHandler(ctx).handle()


class ExecuteHandler(StageHandler):
    def handle(self) -> RuntimeLogRecord:
        ctx = self.ctx
        if not ctx.runtime_plan_path.exists():
            raise FileNotFoundError(f"Plan file not found: {ctx.runtime_plan_path}")

        records = (
            ctx.records
            if ctx.records is not None
            else RuntimeLogRecord.load(ctx.runtime_log_path)
        )
        module = import_emulator()
        with plan_errors(ctx.runtime_plan_path):
            emulator = module.Emulator.from_code(ctx.runtime_plan_path.read_text())
        calls_iter = emulator.iter_tool_calls()

        finished, last_sub_agent_context, workdir = self.replay_records(
            records, calls_iter
        )
        if finished is not None:
            next_stage = finished
        else:
            if last_sub_agent_context is not None:
                agent = last_sub_agent_context.sub_agent
                print(
                    f"No report results found for `{agent.name}` agent, make "
                    "sure agent finished execution, and wrote report results "
                    "based on the JSON schema and report result path"
                )
                raise InferResultArgumentsError()
            next_stage = self.emit_execute_or_finished(
                ctx, emulator, module, calls_iter, len(records), workdir
            )

        return next_stage

    @staticmethod
    def replay_records(
        records: list[RuntimeLogRecord], calls_iter
    ) -> tuple[RuntimeLogExecutionFinished | None, Any, str | None]:
        last_sub_agent_context = None
        workdir = None
        results = {
            payload.get("iteration"): payload.get("args")
            for payload in RuntimeLogInferResult.payloads(records)
        }
        for record in records:
            if record.msg == RuntimeLogCreated.msg:
                workdir = (record.payload or {}).get("workdir_path")

            if record.msg == RuntimeLogExecuteSubAgent.msg:
                payload = record.payload or {}
                agent_name: str | None = payload.get("agent_name")
                assert agent_name, "Inconsistent runtime log, no agent_name found"

                try:
                    call = next(calls_iter)
                except StopIteration:
                    return (
                        RuntimeLogExecutionFinished(agent_instruction=PROMPT_FINISHED),
                        last_sub_agent_context,
                        workdir,
                    )

                if call.sub_agent.name != agent_name:
                    raise RuntimeError(
                        f"Expected sub-agent '{agent_name}' but got '{call.sub_agent.name}'"
                    )

                values = results.get(payload.get("iteration"))
                if result_is_complete(values, call.output_keys):
                    call.infer_result_hook(
                        call.sub_agent.name,
                        [values[key] for key in call.output_keys],
                    )
                    last_sub_agent_context = None
                else:
                    # log a result short of a declared key, so a replayed one always fits.
                    last_sub_agent_context = call

        return None, last_sub_agent_context, workdir

    @staticmethod
    def emit_execute_or_finished(
        ctx: RequestContext,
        emulator,
        module,
        calls_iter,
        iteration: int,
        workdir: str | None,
    ) -> RuntimeLogExecuteSubAgent | RuntimeLogExecutionFinished:
        result_id = RequestContext.random_token()
        result_path = ctx.result_dir / f"{result_id}.json"
        while not emulator.is_finished():
            try:
                next_result = next(calls_iter)
                if isinstance(next_result, module.SubAgentContext):
                    agent = next_result.sub_agent

                    instruction = PROMPT_EXECUTE_SUB_AGENT.format(
                        agent_name=agent.name,
                        agent_path=ctx.sub_agent_path(agent.name),
                        output_schema=output_json_schema(
                            agent.name, agent.outputs_spec, indent="> "
                        ),
                        result_path=result_path,
                        workflow_id=ctx.workflow_id,
                        workdir=workdir if workdir else "current project.",
                    )
                    return RuntimeLogExecuteSubAgent(
                        agent_instruction=instruction,
                        payload={
                            "agent_name": agent.name,
                            "iteration": iteration,
                            "result_id": result_id,
                            "expected_output": dict(agent.outputs_spec),
                        },
                    )
            except StopIteration:
                pass

        return RuntimeLogExecutionFinished(agent_instruction=PROMPT_FINISHED)


def main():
    parsed = parse_args()
    ctx = None
    try:
        ctx = RequestContext.create(parsed)
        record = InitBootstrapHandler(ctx).handle()
    except InferResultArgumentsError:
        return
    except PlanLoadError as exc:
        print(PROMPT_FALLBACK_PLAN_LOAD_FAILED.format(error=exc))
        if ctx:
            print(
                PROMPT_ASK_TO_CREATE_PLAN.format(
                    plan_path=ctx.runtime_plan_path,
                    workflow_id=ctx.workflow_id,
                )
            )
        return
    except EmulatorLoadError as exc:
        print(
            PROMPT_FALLBACK_EMULATOR_UNIMPORTABLE.format(
                module_name=exc, executable=sys.executable
            )
        )
        return
    locked_write_record(ctx, record)
    print(record.agent_instruction)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
