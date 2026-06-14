import builtins
import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Protocol, Self

logger = logging.getLogger("llassembly_python")
logger.addHandler(logging.NullHandler())


@dataclass
class SubAgent:
    name: str
    include_path: str
    objective: str | None
    outputs_spec: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_base_sub_agent(cls, base_sub_agent_class: type[BaseSubAgent]) -> Self:
        return cls(
            name=base_sub_agent_class.name,
            include_path=(
                "global"
                if base_sub_agent_class.existing
                else f"general/{base_sub_agent_class.name}"
            ),
            objective=base_sub_agent_class.objective,
            outputs_spec=base_sub_agent_class.output_spec,
        )


@dataclass
class SubAgentContext:
    sub_agent: SubAgent
    output_keys: list[str]
    infer_result_hook: Callable[[str, dict[str, Any]], None] | InferResultHook


class BaseSubAgent:
    """Base class plans subclass to declare a sub-agent.

    The emulator injects this class directly into the plan's global namespace
    (see :func:`_make_script_globals`), so plans never import it. Plans subclass
    it at *global scope*, supplying ``name`` / ``objective`` / ``output_spec`` /
    ``existing`` as class attributes::

        class AgentBuild(BaseSubAgent):
            name = "build"
            objective = "Build the project artifact from source"
            output_spec = {"status": '"ok" on success or "error" on failure'}
            existing = False

    Every subclass is collected via :meth:`__init_subclass__` into the
    per-emulator ``_registry`` so :meth:`Emulator.get_sub_agents` can enumerate
    every declared agent without instantiating or running it.
    """

    # The ingestion queue this agent publishes requests to. Bound per-emulator
    # so that concurrent ``Emulator`` instances never share state.
    _ingestion_queue: queue.Queue | None = None
    # The per-emulator collection of declared subclasses. A fresh list is bound
    # onto the queue-bound base created per run (see ``_make_run_base``), so
    # concurrent emulators never see each other's agents.
    _registry: list[type] | None = None

    # Metadata supplied by plan subclasses as class attributes. Declared here as
    # defaults so the contract is explicit and instances need no constructor
    # arguments (``AgentBuild()``).
    name: str = ""
    objective: str = ""
    output_spec: dict[str, str] = {}
    existing: bool = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Register this subclass into the nearest ancestor that owns a registry.
        # The queue-bound base created per emulator run *defines* ``_registry``
        # in its own ``__dict__`` and must NOT register itself; only plan
        # subclasses, which merely *inherit* the registry, are collected.
        registry = cls._registry
        if registry is not None and "_registry" not in cls.__dict__:
            registry.append(cls)

    def run(self) -> dict[str, Any]:
        """Execute this sub-agent and return its result.

        Blocks the calling thread until the host emulator fulfils the request
        via a threading.Event, then returns the result dict keyed by
        ``output_spec``.
        """
        if self._ingestion_queue is None:
            raise RuntimeError("sub-agent is not bound to an emulator ingestion queue")
        event = threading.Event()
        result_holder: dict[str, Any] = {}

        def _write_result(values: dict[str, Any]) -> None:
            result_holder.update(values)
            event.set()

        self._ingestion_queue.put(
            (SubAgent.from_base_sub_agent(self.__class__), _write_result)
        )
        event.wait()
        return dict(result_holder)


def _make_run_base(
    ingestion_queue: queue.Queue,
) -> tuple[type[BaseSubAgent], list[type[BaseSubAgent]]]:
    """Build a fresh, queue-bound :class:`BaseSubAgent` subclass for one run.

    A distinct base is created per emulator run and injected into the plan's
    globals (see :func:`_make_script_globals`). Subclassing this (not the shared
    module-level ``BaseSubAgent``) is what binds plan agents to *this* run's
    ingestion queue and registry.

    The returned ``registry`` is the per-run collection of declared subclasses.
    The queue-bound base owns it; ``BaseSubAgent.__init_subclass__`` appends to
    it. Concurrent emulators get distinct lists, so their agents never mix.
    """
    registry: list[type[BaseSubAgent]] = []
    run_base = type(
        "BaseSubAgent",
        (BaseSubAgent,),
        {
            "_ingestion_queue": ingestion_queue,
            "_registry": registry,
        },
    )
    return run_base, registry


@dataclass
class _ScriptResult:
    """Mutable holder used to communicate a background script's outcome back to
    its owning :class:`Emulator` without sharing ``self`` with the worker."""

    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


def _make_script_globals(run_base: type[BaseSubAgent]) -> dict[str, Any]:
    """Build the globals dict used to exec a plan, with ``BaseSubAgent``
    injected directly into the namespace.

    The per-run, queue-bound base (see :func:`_make_run_base`) is placed into
    the plan's globals under the name ``BaseSubAgent``. Plans subclass it at
    global scope without importing anything; ``__builtins__`` is the unmodified
    real builtins.
    """
    return {
        "__name__": "__user_script__",
        "__file__": "<string>",
        "__builtins__": vars(builtins),
        "BaseSubAgent": run_base,
    }


def _run_script(
    code: str,
    ingestion_queue: queue.Queue,
    result: _ScriptResult,
) -> None:
    """Compile and execute user code in a dedicated thread.

    Compilation happens here (not in the main thread) so the whole lifecycle of
    a run lives in one place. The plan defines a mandatory ``def main()``
    entry point; this function execs the plan body (which only declares agents
    and defines ``main``) and then calls ``main`` directly.
    """
    run_base, _registry = _make_run_base(ingestion_queue)
    script_globals = _make_script_globals(run_base)
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
        # sub-agent subclasses by exec'ing the plan body in isolation.
        self._code = code
        # Per-instance queue so concurrent emulators never share request state.
        self.ingestion_queue: queue.Queue = queue.Queue()

        # The entire script -- compilation and execution -- runs in a
        # dedicated background thread and blocks there. The worker reports back
        # through ``_result`` instead of receiving ``self``.
        self._result = _ScriptResult()
        self._thread = threading.Thread(
            target=_run_script,
            args=(code, self.ingestion_queue, self._result),
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

        Executes the plan's module body in an isolated namespace with a fresh,
        throwaway queue-bound base injected as ``BaseSubAgent``. Running the body
        is enough to define every global-scope ``class AgentX(BaseSubAgent)``;
        each definition registers itself via
        :meth:`BaseSubAgent.__init_subclass__`. We then convert each collected
        subclass into a :class:`SubAgent`.

        This is a pure declaration scan. The plan body only declares agent
        classes and defines ``main``; ``main`` is never called here,
        so exec'ing the body invokes no sub-agent.
        """
        # A throwaway queue: we never dispatch from this scan, so the queue is
        # unused, but the bound base still needs one.
        run_base, registry = _make_run_base(queue.Queue())
        script_globals = _make_script_globals(run_base)
        exec(compile(self._code, "<string>", "exec"), script_globals)

        sub_agents: dict[str, SubAgent] = {}
        for agent_cls in registry:
            sub_agent = SubAgent.from_base_sub_agent(agent_cls)
            sub_agents[sub_agent.name] = sub_agent
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

            emulator_agent, hook = item
            sub_agent_context = SubAgentContext(
                emulator_agent,
                list(emulator_agent.outputs_spec.keys()),
                InferResultHook(emulator_agent, hook),
            )

            yield sub_agent_context

    def is_finished(self) -> bool:
        return self._result.done.is_set()

    @property
    def error(self) -> BaseException | None:
        """The exception raised by the user script, if any."""
        return self._result.error
