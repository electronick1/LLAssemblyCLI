"""Execute a mermaid control-flow diagram as an LLAssembly plan.

The diagram is already a control-flow graph, so it is walked directly rather than compiled to
an intermediate language. ``get_next_instruction.py`` drives this module through the same small
contract every variant implements: :class:`Emulator` with ``from_code``, ``get_sub_agents``,
``iter_tool_calls`` and ``is_finished``.

Free text is never interpreted -- only node shapes, node ids and a closed set of edge labels are
read, while objectives and questions are carried through to the sub-agent verbatim. Termination
is structural: the walk is capped at :data:`MAX_STEPS` node visits, so a diagram cannot loop
forever however its edges are drawn.

The walk must stay deterministic. The driver builds a fresh emulator on every invocation and
replays the runtime log through ``iter_tool_calls`` to resync, so the same diagram and the same
recorded answers must always reach the same node.

"""

import re
from collections import defaultdict
from collections.abc import Callable, Container, Generator, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self

PLAN_EXTENSION = "mmd"

MAX_STEPS = 200
MAX_OUTPUTS = 10


class NodeKind(StrEnum):
    START = "start"
    EXIT = "exit"
    SUB_AGENT = "sub_agent"
    DECISION = "decision"
    QUESTION = "question"


class EdgeLabel(StrEnum):
    TRUE = "true"
    FALSE = "false"
    ERROR = "error"


class TerminalRole(StrEnum):
    START = "start"
    DONE = "done"
    ABORT = "abort"


AGENT_KINDS = frozenset({NodeKind.SUB_AGENT, NodeKind.QUESTION})
DECISION_KINDS = frozenset({NodeKind.DECISION, NodeKind.QUESTION})

EDGE_LABELS = (EdgeLabel.TRUE, EdgeLabel.FALSE, EdgeLabel.ERROR)
TERMINAL_ROLES = (TerminalRole.START, TerminalRole.DONE, TerminalRole.ABORT)

PRIMARY_OUTPUT = "output_1"
STATUS_CONTRACT = 'status: "ok" on success or "error" on failure'
BOOLEAN_CONTRACT = '{question} answer "true" or "false"'


def _alias_map[RoleT: StrEnum](synonyms: dict[RoleT, set[str]]) -> dict[str, RoleT]:
    return {
        word: canonical
        for canonical, words in synonyms.items()
        for word in (str(canonical), *words)
    }


EDGE_LABEL_ALIASES = _alias_map(
    {
        EdgeLabel.TRUE: {"yes", "y", "ok", "success", "pass", "passed"},
        EdgeLabel.FALSE: {"no", "n"},
        EdgeLabel.ERROR: {"err", "failure", "exception", "fail", "failed"},
    }
)

DECISION_EDGE_LABEL_ALIASES = EDGE_LABEL_ALIASES | dict.fromkeys(
    ("fail", "failed"), EdgeLabel.FALSE
)

TERMINAL_ROLE_ALIASES = _alias_map(
    {
        TerminalRole.START: {"begin", "entry", "init"},
        TerminalRole.DONE: {
            "end",
            "finish",
            "finished",
            "complete",
            "completed",
            "success",
            "succeeded",
            "ok",
            "pass",
            "passed",
        },
        TerminalRole.ABORT: {
            "aborted",
            "fail",
            "failed",
            "failure",
            "error",
            "stop",
            "stopped",
            "giveup",
            "give_up",
            "cancel",
            "cancelled",
        },
    }
)

IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
HEADER_RE = re.compile(r"^(?:flowchart|graph)\b", re.IGNORECASE)
NODE_RE = re.compile(
    r"^(?P<id>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\(\[(?P<terminal>.*)\]\)|\[\[(?P<sub_agent>.*)\]\]"
    r"|\{\{(?P<question>.*)\}\}|\{(?P<decision>.*)\})?$"
)

LABEL_OPEN, LABEL_CLOSE = "[({", "])}"

# Mermaid draws one link a dozen ways -- `-->`, `--->`, `---`, `--x`, `--o`, `==>`, `-.->` --
LINK_CHARS = "-=."
ARROW_HEADS = "xo"  # as in `--x` / `--o`; `>` is handled on its own


class PlanError(Exception):
    """The diagram could not be read; the message carries the offending line number."""


def _check(condition: object, message: str) -> None:
    if not condition:
        raise PlanError(message)


@dataclass
class Edge:
    src: str
    dst: str
    label: EdgeLabel | str | None
    line: int


@dataclass
class Node:
    id: str
    line: int
    kind: NodeKind | None = None
    name: str = "" 
    text: str = ""
    reads: tuple[str, str] | tuple[()] = ()


@dataclass
class SubAgent:
    name: str
    include_path: str
    objective: str
    # output key -> the human-readable contract the sub-agent must satisfy
    outputs_spec: dict[str, str] = field(default_factory=dict)


@dataclass
class SubAgentContext:
    sub_agent: SubAgent
    output_keys: list[str]
    infer_result_hook: Callable[[str, Iterable[str]], None]


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    start: str = ""
    # Adjacency, kept in step with ``edges`` by ``add_edge`` so the walk needs no rescans.
    _out: dict[str, list[Edge]] = field(
        default_factory=lambda: defaultdict(list), repr=False
    )
    _into: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list), repr=False
    )

    def add_edge(self, src: str, dst: str, label: str | None, line: int) -> None:
        edge = Edge(src, dst, label, line)
        self.edges.append(edge)
        self._out[src].append(edge)
        self._into[dst].append(src)

    def out_edges(self, node_id: str) -> list[Edge]:
        return self._out[node_id]

    def predecessors(self, node_id: str) -> list[str]:
        return self._into[node_id]


def _reach(
    sources: Iterable[str],
    neighbours: Callable[[str], Iterable[str]],
    stop: Container[str] = (),
) -> set[str]:
    """Nodes reachable from ``sources``, never expanding past a node in ``stop``."""
    seen, stack = set(sources), [s for s in sources if s not in stop]
    while stack:
        for node_id in neighbours(stack.pop()):
            if node_id not in seen:
                seen.add(node_id)
                if node_id not in stop:
                    stack.append(node_id)
    return seen


def _clean(text: str) -> str:
    text = text.strip()
    while len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return " ".join(text.split())


def _normalise(value: object) -> str:
    return _clean(str(value)).lower()


def _label_names(labels: Iterable[EdgeLabel | str | None]) -> list[str | None]:
    return [None if label is None else str(label) for label in labels]


def _link_run_end(line: str, index: int) -> int | None:
    end = index
    while end < len(line) and line[end] in LINK_CHARS:
        end += 1
    return end if end - index >= 2 else None


def _arrow_head_end(line: str, index: int) -> int | None:
    if index < len(line):
        if line[index] == ">":
            return index + 1
        if line[index] in ARROW_HEADS and (
            index + 1 == len(line) or line[index + 1].isspace()
        ):
            return index + 1
    return None


def _match_link(line: str, index: int) -> tuple[int, str | None] | None:
    """Match a link at ``index``, returning where it ends and the label as written.

    Three shapes, in the order they are ruled out: ``-->|label|`` and a bare ``-->`` both put
    the head straight after the run, while ``-- label -->`` has no head there and instead a
    second run closes it.
    """
    run = _link_run_end(line, index)
    if run is None:
        return None

    head = _arrow_head_end(line, run)
    if head is not None:
        if head < len(line) and line[head] == "|":
            close = line.find("|", head + 1)
            if close != -1:
                return close + 1, line[head + 1 : close]
        return head, None

    # Stop at the first bracket: an inline label never contains one, so a run found past it
    # belongs to the next node's label. Without this, `a --- b[[job: do -- it]]` splits on the
    # `--` inside the objective and the line is mangled beyond recognition.
    for cursor in range(run, len(line)):
        if line[cursor] in LABEL_OPEN:
            break
        closing = _link_run_end(line, cursor)
        if closing is not None:
            end = _arrow_head_end(line, closing)
            return (closing if end is None else end), line[run:cursor]
    return run, None  # `--- next`: an open link carrying neither head nor label


def _scan_line(
    line: str, quote_aware: bool
) -> tuple[list[str], list[str | None], bool]:
    """Split ``line`` at every link outside a node label.

    Returns the node tokens, the label written on the link between each pair (``None`` when it
    carried none), and whether the scan ended balanced.
    """
    tokens: list[str] = []
    labels: list[str | None] = []
    start = index = depth = 0
    quoted = escaped = False
    while index < len(line):
        char = line[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"' and quote_aware and depth:
            quoted = True
        elif char in LABEL_OPEN:
            depth += 1
        elif char in LABEL_CLOSE:
            depth = max(depth - 1, 0)
        elif not depth:
            link = _match_link(line, index)
            if link is not None:
                end, written = link
                tokens.append(line[start:index])
                labels.append(None if written is None else _normalise(written))
                start = index = end
                continue
        index += 1
    tokens.append(line[start:])
    return tokens, labels, depth == 0 and not quoted


def _split_line(line: str) -> tuple[list[str], list[str | None]]:
    """Split one diagram line, degrading rather than committing to an untrustworthy split.

    A scan that ends on an open quote or inside a bracket has lost track of where labels are,
    so it is retried quote-blind, which is strictly more permissive. Every line that parses at
    all is bracket-balanced, so the second pass is the last one needed.
    """
    tokens, labels, balanced = _scan_line(line, quote_aware=True)
    if balanced:
        return tokens, labels
    tokens, labels, _ = _scan_line(line, quote_aware=False)
    return tokens, labels


def _parse_node(token: str, line_no: int, graph: Graph) -> str:
    """Register the node described by ``token`` and return its id."""
    token = token.strip()
    match = NODE_RE.match(token)
    _check(match, f"line {line_no}: cannot parse node {token!r}")
    node = Node(id=match.group("id").lower(), line=line_no)
    _check(
        IDENTIFIER_RE.match(node.id),
        f"line {line_no}: node id {node.id!r} must match [a-z][a-z0-9_]*",
    )

    if (text := match.group("terminal")) is not None:
        written = _normalise(text) or node.id
        node.text = TERMINAL_ROLE_ALIASES.get(written, written)
        _check(
            node.text in TERMINAL_ROLES,
            f"line {line_no}: terminal {node.id!r} must be one "
            f"of {', '.join(TERMINAL_ROLES)}, got {written!r}",
        )
        node.kind = NodeKind.START if node.text == TerminalRole.START else NodeKind.EXIT
    elif (text := match.group("sub_agent")) is not None:
        name, _, objective = _clean(text).partition(":")
        node.name = _clean(name).lower().replace(" ", "_")
        _check(
            IDENTIFIER_RE.match(node.name),
            f"line {line_no}: sub-agent name {node.name!r} must match [a-z][a-z0-9_]*",
        )
        node.kind = NodeKind.SUB_AGENT
        node.text = _clean(objective) or node.name.replace("_", " ")
    elif (text := match.group("question")) is not None:
        node.kind, node.name, node.text = NodeKind.QUESTION, node.id, _clean(text)
        _check(node.text, f"line {line_no}: question agent {node.id!r} has no question")
    elif (text := match.group("decision")) is not None:
        node.kind, node.text = NodeKind.DECISION, _clean(text)
        _check(node.text, f"line {line_no}: decision {node.id!r} has no question")
    else:
        graph.nodes.setdefault(
            node.id, node
        )  # bare reference; the shape may arrive later
        return node.id

    previous = graph.nodes.get(node.id)
    if previous is not None and previous.kind is not None:
        # Saying the same thing twice is a habit, not a mistake; two different shapes is one.
        if (previous.kind, previous.name, previous.text) != (
            node.kind,
            node.name,
            node.text,
        ):
            raise PlanError(
                f"line {line_no}: node {node.id!r} was already "
                f"declared on line {previous.line}"
            )
        return node.id
    graph.nodes[node.id] = node
    return node.id


def _parse(plan_code: str) -> Graph:
    graph, seen_header = Graph(), False
    for line_no, raw in enumerate(plan_code.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if not seen_header:
            _check(
                HEADER_RE.match(line),
                f"line {line_no}: expected a flowchart header — first non-blank line must be "
                f"`flowchart TD`, with no title, prose or ``` fence",
            )
            seen_header = True
            continue
        _check(
            line.lower() != "end" and not line.lower().startswith("subgraph"),
            f"line {line_no}: subgraphs are not supported",
        )

        tokens, labels = _split_line(line)
        ids = [_parse_node(token, line_no, graph) for token in tokens]
        for index, label in enumerate(labels):
            graph.add_edge(ids[index], ids[index + 1], label or None, line_no)

    _check(seen_header, "empty plan: expected a flowchart header")
    return graph


def _resolve_edge_labels(graph: Graph) -> None:
    """Rewrite every written label as the :class:`EdgeLabel` it means.

    Runs once each node's kind is known, because a word can mean either of two things depending
    on what it hangs off: ``fail`` off a decision is the answer "no", off a sub-agent it is a
    failure report.
    """
    for edge in graph.edges:
        if edge.label is None:
            continue
        aliases = (
            DECISION_EDGE_LABEL_ALIASES
            if graph.nodes[edge.src].kind in DECISION_KINDS
            else EDGE_LABEL_ALIASES
        )
        resolved = aliases.get(str(edge.label))
        _check(
            resolved is not None,
            f"line {edge.line}: edge label {edge.label!r} "
            f"must be one of {', '.join(EDGE_LABELS)} (retry limits are never written "
            f"in the diagram)",
        )
        edge.label = resolved


def _exit_ids(graph: Graph) -> list[str]:
    return [node.id for node in graph.nodes.values() if node.kind is NodeKind.EXIT]


def _check_every_node_is_shaped(graph: Graph) -> None:
    for node in graph.nodes.values():
        _check(
            node.kind is not None,
            f"line {node.line}: node {node.id!r} is never given a shape",
        )


def _check_terminals(graph: Graph) -> None:
    """Exactly one entry point and somewhere to end up; records the start on the graph."""
    starts = [node.id for node in graph.nodes.values() if node.kind is NodeKind.START]
    _check(len(starts) == 1, f"expected exactly one start node, found {len(starts)}")
    _check(_exit_ids(graph), "no exit node: expected a terminal such as done([done])")
    graph.start = starts[0]


def _check_edge_fanout(graph: Graph) -> None:
    """Each shape allows its own set of outgoing edges, and nothing else."""
    for node in graph.nodes.values():
        labels = [edge.label for edge in graph.out_edges(node.id)]
        if node.kind is NodeKind.EXIT:
            _check(
                not labels,
                f"line {node.line}: exit {node.id!r} must have no outgoing edge",
            )
        elif node.kind in DECISION_KINDS:
            _check(
                sorted(label or "" for label in labels) == ["false", "true"],
                f"line {node.line}: decision {node.id!r} needs exactly one true and one "
                f"false edge, found {_label_names(labels) or 'none'}",
            )
        else:
            # One step forward, and a sub-agent may add one error edge; start may not.
            allowed_errors = 1 if node.kind is NodeKind.SUB_AGENT else 0
            plain, errors = labels.count(None), labels.count(EdgeLabel.ERROR)
            and_an_error = " plus at most one error edge" if allowed_errors else ""
            _check(
                plain == 1
                and errors <= allowed_errors
                and plain + errors == len(labels),
                f"line {node.line}: {node.kind} {node.id!r} needs one unlabelled edge"
                f"{and_an_error}, found {_label_names(labels)}",
            )


def _validate(graph: Graph) -> None:
    """Reject a malformed diagram up front, cheapest and most fundamental check first."""
    _check_every_node_is_shaped(graph)
    _resolve_edge_labels(graph)
    _check_terminals(graph)
    _check_edge_fanout(graph)


def _declare_agents(graph: Graph) -> dict[str, SubAgent]:
    """One :class:`SubAgent` per distinct name, with its status or boolean contract."""
    agents: dict[str, SubAgent] = {}
    for node in graph.nodes.values():
        if node.kind not in AGENT_KINDS:
            continue
        if node.name in agents:
            _check(
                node.kind is NodeKind.SUB_AGENT,
                f"line {node.line}: question agent {node.id!r} "
                f"collides with another sub-agent named {node.name!r}",
            )
            continue
        is_sub_agent = node.kind is NodeKind.SUB_AGENT
        objective = node.text if is_sub_agent else f"answer this question: {node.text}"
        contract = (
            STATUS_CONTRACT
            if is_sub_agent
            else BOOLEAN_CONTRACT.format(question=node.text)
        )
        agents[node.name] = SubAgent(
            node.name,
            f"general/{node.name}",
            objective,
            {PRIMARY_OUTPUT: contract},
        )
    return agents


def _fuse_decisions(graph: Graph, agents: dict[str, SubAgent]) -> None:
    """Hand each ``{ }`` decision to the nearest sub-agent above it.

    The question becomes an extra field on that sub-agent's contract, so the sub-agent answers
    it while doing its work and the decision costs no sub-agent of its own. Sorting the
    decisions keeps the output slots stable. Sets ``Node.reads`` on each decision.
    """
    sub_agent_ids = {n.id for n in graph.nodes.values() if n.kind is NodeKind.SUB_AGENT}
    decision_ids = sorted(
        n.id for n in graph.nodes.values() if n.kind is NodeKind.DECISION
    )
    for node_id in decision_ids:
        node = graph.nodes[node_id]
        owners = (
            _reach(graph.predecessors(node_id), graph.predecessors, stop=sub_agent_ids)
            & sub_agent_ids
        )
        hint = f"use a question agent instead: {node_id}{{{{...}}}}"
        _check(
            owners,
            f"line {node.line}: decision {node_id!r} has no sub-agent ancestor to "
            f"answer it; {hint}",
        )
        _check(
            len(owners) == 1,
            f"line {node.line}: decision {node_id!r} could be answered by "
            f"any of {sorted(owners)}; insert a sub-agent or {hint}",
        )
        agent = agents[graph.nodes[owners.pop()].name]
        _check(
            len(agent.outputs_spec) < MAX_OUTPUTS,
            f"line {node.line}: sub-agent {agent.name!r} "
            f"would need over {MAX_OUTPUTS} outputs; split it across two sub-agents",
        )
        key = f"output_{len(agent.outputs_spec) + 1}"
        agent.outputs_spec[key] = BOOLEAN_CONTRACT.format(question=node.text)
        node.reads = (agent.name, key)


def _bind(graph: Graph) -> dict[str, SubAgent]:
    agents = _declare_agents(graph)
    _fuse_decisions(graph, agents)
    for node in graph.nodes.values():
        if node.kind is NodeKind.QUESTION:
            node.reads = (node.name, PRIMARY_OUTPUT)  # a question owns its sub-agent
    return agents


class Emulator:
    """Walks the diagram, yielding one sub-agent at a time and branching on what it returns."""

    def __init__(self, graph: Graph, agents: dict[str, SubAgent]):
        self.graph = graph
        self.agents = agents
        self.results: dict[str, dict[str, str]] = {}
        self.exit_code = 1
        self._finished = False

    @classmethod
    def from_code(cls, plan_code: str) -> Self:
        graph = _parse(plan_code)
        _validate(graph)
        return cls(graph, _bind(graph))

    def get_sub_agents(self) -> dict[str, SubAgent]:
        return dict(self.agents)

    def is_finished(self) -> bool:
        return self._finished

    def execute_sub_agent(self, agent_name: str, output_values: Iterable[str]) -> None:
        keys = self.agents[agent_name].outputs_spec
        self.results[agent_name] = dict(zip(keys, map(_normalise, output_values)))

    def _follow(self, node: Node, label: EdgeLabel | None) -> Node | None:
        for edge in self.graph.out_edges(node.id):
            if edge.label == label:
                return self.graph.nodes[edge.dst]
        return None

    def _next(self, node: Node) -> Node | None:
        if node.kind is NodeKind.START:
            return self._follow(node, None)
        if node.kind is NodeKind.SUB_AGENT:
            if self.results[node.name].get(PRIMARY_OUTPUT) == EdgeLabel.ERROR:
                return self._follow(node, EdgeLabel.ERROR)
            return self._follow(node, None)
        agent_name, key = node.reads
        answer = self.results.get(agent_name, {}).get(key)
        return self._follow(
            node, EdgeLabel.TRUE if answer == EdgeLabel.TRUE else EdgeLabel.FALSE
        )

    def iter_tool_calls(self) -> Generator[SubAgentContext, None, None]:
        node, steps = self.graph.nodes[self.graph.start], 0
        while node is not None and node.kind is not NodeKind.EXIT:
            steps += 1
            _check(steps <= MAX_STEPS, f"plan did not finish within {MAX_STEPS} steps")
            if node.kind in AGENT_KINDS:
                agent = self.agents[node.name]
                yield SubAgentContext(
                    agent, list(agent.outputs_spec), self.execute_sub_agent
                )
            node = self._next(node)
        reached_done = node is not None and node.text == TerminalRole.DONE
        self.exit_code = 0 if reached_done else 1
        self._finished = True
