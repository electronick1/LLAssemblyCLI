from __future__ import annotations

import builtins
import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Protocol, Self

logger = logging.getLogger("llassembly_python")
logger.addHandler(logging.NullHandler())

# Extension the driver uses for this variant's control-flow file (plan_llassembly.<ext>).
PLAN_EXTENSION = "py"


@dataclass
class SubAgent:
    name: str
    include_path: str
    objective: str | None
    outputs_spec: dict[str, str] = field(default_factory=dict)


@dataclass
class SubAgentContext:
    sub_agent: SubAgent
    output_keys: list[str]
    infer_result_hook: Callable[[str, dict[str, Any]], None] | InferResultHook


@dataclass
class _ScriptResult:
    """Mutable holder used to communicate a background script's outcome back to
    its owning :class:`Emulator` without sharing ``self`` with the script thread."""

    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


_ALLOWED_MODULES = frozenset(
    {"json", "math", "re", "string", "datetime"}
)


def _limited_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0 or name.partition(".")[0] not in _ALLOWED_MODULES:
        raise ImportError(f"plans may not import {name!r}")
    return builtins.__import__(name, globals, locals, fromlist, level)


_LIMITED_BUILTIN_NAMES = (
    "bool",
    "dict",
    "float",
    "int",
    "list",
    "set",
    "str",
    "tuple",
    "abs",
    "all",
    "any",
    "enumerate",
    "len",
    "max",
    "min",
    "range",
    "repr",
    "round",
    "sorted",
    "sum",
    "zip",
    "isinstance",
    "AssertionError",
    "Exception",
    "IndexError",
    "KeyError",
    "RuntimeError",
    "TypeError",
    "ValueError",
)

_LIMITED_BUILTINS = {name: getattr(builtins, name) for name in _LIMITED_BUILTIN_NAMES}
_LIMITED_BUILTINS["__import__"] = _limited_import


def _make_script_globals(
    sub_agents: dict[str, SubAgent], ingestion_queue: queue.Queue
) -> dict[str, Any]:
    """Build the globals dict used to exec a plan, with the two runtime functions
    injected directly into the namespace.

    Plans declare a sub-agent with a top-level ``setup_sub_agent(...)`` call and
    dispatch to it with ``run_sub_agent(name)`` inside ``main()``; ``__builtins__``
    is the restricted ``_LIMITED_BUILTINS`` mapping, not the real one.

    Both functions close over the caller's ``sub_agents`` registry and
    ``ingestion_queue``, so each emulator run -- and each declaration scan --
    gets its own pair and they never see each other's state.
    """

    def setup_sub_agent(
        name: str,
        objective: str,
        output_spec: dict[str, str],
        existing: bool = False,
    ) -> None:
        """Register one declared sub-agent. Keyed by name, so a duplicate
        declaration replaces the earlier one."""
        sub_agents[name] = SubAgent(
            name=name,
            include_path="global" if existing else f"general/{name}",
            objective=objective,
            outputs_spec=output_spec,
        )

    def run_sub_agent(name: str) -> dict[str, Any]:
        """Dispatch one sub-agent and return its result.

        Publishes the request onto the ingestion queue the host emulator drains,
        then blocks the calling thread on a threading.Event until the driver
        supplies the values, and returns them keyed by ``output_spec``.
        """
        event = threading.Event()
        result_holder: dict[str, Any] = {}

        def _write_result(values: dict[str, Any]) -> None:
            result_holder.update(values)
            event.set()

        ingestion_queue.put((name, _write_result))
        event.wait()
        return dict(result_holder)

    return {
        "__name__": "__user_script__",
        "__file__": "<string>",
        "__builtins__": _LIMITED_BUILTINS,
        "setup_sub_agent": setup_sub_agent,
        "run_sub_agent": run_sub_agent,
    }


def _run_script(
    code: str,
    sub_agents: dict[str, SubAgent],
    ingestion_queue: queue.Queue,
    result: _ScriptResult,
) -> None:
    """Compile and execute user code in a dedicated thread.

    Compilation happens here (not in the main thread) so the whole lifecycle of
    a run lives in one place. The plan defines a mandatory ``def main()``
    entry point; this function execs the plan body (which only declares agents
    and defines ``main``) and then calls ``main`` directly.
    """
    script_globals = _make_script_globals(sub_agents, ingestion_queue)
    try:
        exec(compile(code, "<string>", "exec"), script_globals)
        main = script_globals.get("main")
        if main is None or not callable(main):
            raise RuntimeError("plan must define an entry point `main`")
        main()
    except BaseException as exc:  # noqa: BLE001 - surfaced via .error
        result.error = exc
        raise
    finally:
        result.done.set()


class InferResultHook:
    def __init__(self, sub_agent: SubAgent, hook: Callable):
        self.sub_agent = sub_agent
        self.hook = hook

    def __call__(self, agent_name: str, output_values: list[str | int]):
        output = dict()
        for output_index, output_key in enumerate(self.sub_agent.outputs_spec):
            output[output_key] = output_values[output_index]
        self.hook(output)


class Emulator:
    def __init__(self, code: str):
        # Retained so ``get_sub_agents`` can statically collect the declared
        # sub-agents by exec'ing the plan body in isolation.
        self._code = code
        # Per-instance queue so concurrent emulators never share request state.
        self.ingestion_queue: queue.Queue = queue.Queue()
        # Per-instance registry the plan's ``setup_sub_agent`` calls populate;
        # ``iter_tool_calls`` looks the declared SubAgent up by name when the
        # plan dispatches one.
        self._sub_agents: dict[str, SubAgent] = {}

        # The entire script -- compilation and execution -- runs in a
        # dedicated background thread and blocks there. That thread reports back
        # through ``_result`` instead of receiving ``self``.
        self._result = _ScriptResult()
        self._thread = threading.Thread(
            target=_run_script,
            args=(code, self._sub_agents, self.ingestion_queue, self._result),
            daemon=True,
        )
        self._thread.start()

    def shutdown(self) -> None:
        """Join the background script thread.

        Safe to call multiple times. The thread is a daemon, so even if the
        user script is still blocked it will not keep the process alive.
        """
        self._thread.join(timeout=1)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.shutdown()

    @classmethod
    def from_code(cls, code: str) -> Self:
        return cls(code)

    def get_sub_agents(self) -> dict[str, SubAgent]:
        """Statically collect every sub-agent the plan declares.

        Executes the plan's module body in an isolated namespace with a fresh
        registry and a throwaway queue. Running the body is enough for every
        global-scope ``setup_sub_agent(...)`` call to register itself into that
        registry.

        This is a pure declaration scan. The plan body only declares agents and
        defines ``main``; ``main`` is never called here, so exec'ing the body
        dispatches no sub-agent -- which is why the throwaway queue is never
        drained.
        """
        sub_agents: dict[str, SubAgent] = {}
        exec(
            compile(self._code, "<string>", "exec"),
            _make_script_globals(sub_agents, queue.Queue()),
        )
        return sub_agents

    def iter_tool_calls(self) -> Generator[SubAgentContext, None, None]:
        """Yield a context for every sub-agent the user script requests."""
        while True:
            try:
                item = self.ingestion_queue.get(timeout=0.1)
            except queue.Empty:
                # No pending request. Stop only once the script has finished and
                # drained any outstanding requests.
                if self.is_finished():
                    break
                continue

            sub_agent_name, hook = item
            sub_agent = self._sub_agents[sub_agent_name]
            sub_agent_context = SubAgentContext(
                sub_agent,
                list(sub_agent.outputs_spec.keys()),
                InferResultHook(sub_agent, hook),
            )

            yield sub_agent_context

    def is_finished(self) -> bool:
        return self._result.done.is_set()

    @property
    def error(self) -> BaseException | None:
        """The exception raised by the user script, if any."""
        return self._result.error
