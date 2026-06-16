import builtins
import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Protocol, Self

import pydantic_monty

logger = logging.getLogger("llassembly_python")
logger.addHandler(logging.NullHandler())


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
    its owning :class:`Emulator` without sharing ``self`` with the worker."""

    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


class ScriptRunnner:
    def __init__(self, ingestion_queue: queue.Queue | None):
        self._sub_agents = dict()
        self._ingestion_queue: queue.Queue | None = ingestion_queue

    def setup_sub_agent_hook(
        self, name: str, objective: str, output_spec: dict[str, str], existing: bool
    ):
        include_path = "global" if existing else f"general/{name}"
        self._sub_agents[name] = SubAgent(
            name=name,
            objective=objective,
            outputs_spec=output_spec,
            include_path=include_path,
        )

    def run_sub_agent_hook(self, sub_agent_name: str):
        event = threading.Event()
        result_holder: dict[str, Any] = {}

        def _write_result(values: dict[str, Any]) -> None:
            result_holder.update(values)
            event.set()

        if self._ingestion_queue:
            self._ingestion_queue.put((sub_agent_name, _write_result))
        event.wait()
        return dict(result_holder)

    def run(
        self,
        code: str,
        result: _ScriptResult,
    ) -> None:

        type_definitions = """

        def setup_sub_agent(name: str, objective: str, output_spec: dict[str, str], existing: bool):
            raise NotImplementedError()

        def run_sub_agent(name: str) ->  dict:
            raise NotImplementedError()

        """
        try:
            m = pydantic_monty.Monty(
                f"{code}\n\nmain()",
                inputs=[],
                script_name="agent.py",
                type_check=True,
                type_check_stubs=type_definitions,
            )
            monty_result = m.start()
            while True:
                if isinstance(monty_result, pydantic_monty.MontyComplete):
                    break
                assert isinstance(
                    monty_result, pydantic_monty.FunctionSnapshot
                ), "Unknown external call from monty"
                if monty_result.function_name == "setup_sub_agent":
                    args = list(monty_result.args)
                    for kw in ["name", "objective", "output_spec", "existing"]:
                        if kw in monty_result.kwargs:
                            args.append(monty_result.kwargs[kw])
                    name, objective, output_spec, existing = args
                    self.setup_sub_agent_hook(name, objective, output_spec, existing)
                    monty_result = monty_result.resume({"return_value": None})
                elif monty_result.function_name == "run_sub_agent":
                    (name,) = monty_result.args
                    sub_agent_result = self.run_sub_agent_hook(name)
                    monty_result = monty_result.resume(
                        {"return_value": sub_agent_result}
                    )
                else:
                    raise RuntimeError("Unknown monty external call ")
        except BaseException as exc:  # noqa: BLE001 - surfaced via .error
            result.error = exc
            result.done.set()
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
        # sub-agent subclasses by exec'ing the plan body in isolation.
        self._code = code
        # Per-instance queue so concurrent emulators never share request state.
        self.ingestion_queue: queue.Queue = queue.Queue()

        # Store the runner so iter_tool_calls can look up SubAgent objects
        self._runner = ScriptRunnner(self.ingestion_queue)

        # The entire script -- compilation and execution -- runs in a
        # dedicated background thread and blocks there. The worker reports back
        # through ``_result`` instead of receiving ``self``.
        self._result = _ScriptResult()
        self._thread = threading.Thread(
            target=self._runner.run,
            args=(code, self._result),
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
        # Create a queue and result holder for the scan runner
        scan_queue: queue.Queue = queue.Queue()
        scan_result = _ScriptResult()
        runner = ScriptRunnner(scan_queue)

        # Start the runner in a dedicated thread, just like in __init__
        scan_thread = threading.Thread(
            target=runner.run,
            args=(self._code, scan_result),
            daemon=True,
        )
        scan_thread.start()

        # Wait for the first setup_sub_agent call to populate _sub_agents
        while True:
            try:
                scan_queue.get(timeout=0.1)
                break
            except queue.Empty:
                # Check if the script has finished (no more sub-agents to register)
                if scan_result.done.is_set():
                    break
                continue

        # Convert the collected SubAgent list into a dict keyed by name
        return runner._sub_agents

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
            print(self._runner._sub_agents)
            sub_agent = self._runner._sub_agents[sub_agent_name]
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
