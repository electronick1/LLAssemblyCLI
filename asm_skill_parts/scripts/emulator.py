import json
import logging
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, Generator, Self

logger = logging.getLogger("llassembly_asm")
logger.addHandler(logging.NullHandler())

PLAN_EXTENSION = "asm"

INITIAL_REGISTERS: dict[str, Any] = {
    "eax": 0,
    "ebx": 0,
    "ecx": 0,
    "edx": 0,
    "esi": 0,
    "edi": 0,
    "esp": 0x1000,
    "ebp": 0,
    "eip": 0,
    "flags": 0,
}

JUMP_CONDITIONS: dict[str, Callable[[bool, bool], bool]] = {
    "je": lambda zero, sign: zero,
    "jne": lambda zero, sign: not zero,
    "jl": lambda zero, sign: sign,
    "jlt": lambda zero, sign: sign,
    "js": lambda zero, sign: sign,
    "jle": lambda zero, sign: zero or sign,
    "jg": lambda zero, sign: not zero and not sign,
    "jgt": lambda zero, sign: not zero and not sign,
    "jge": lambda zero, sign: not sign,
    "jns": lambda zero, sign: not sign,
}


@dataclass
class AsmInstruction:
    """
    Representation of a single assembly instruction.
    """

    origin: str
    command: str
    operands: list[str] = field(default_factory=list)

    @classmethod
    def from_row_line(cls, line: str) -> Self | None:
        origin = line
        line = line.strip().split(";")[0].strip()

        if not line:
            return None

        parts = line.split()
        if len(parts) > 2 and parts[1] == "db":
            return cls(
                origin=origin, command="db", operands=[parts[0], " ".join(parts[2:])]
            )

        parts = " ".join(parts).lower().replace(",", " ").split()
        return cls(origin=origin, command=parts[0], operands=parts[1:])


@dataclass
class SubAgent:
    """Represents a parsed sub-agent definition from assembly source code."""

    name: str
    include_path: str
    objective: str | None
    outputs_spec: dict[str, str] = field(default_factory=dict)
    instructions: list[AsmInstruction] = field(default_factory=list)


@dataclass
class SubAgentContext:
    """Yielded when the emulator encounters a bare agent_<name> instruction."""

    sub_agent: SubAgent
    output_keys: list[str]
    infer_result_hook: Callable[[str, list[str | int]], None]


@dataclass
class ASMEmulatorState:
    registers: dict[str, Any] = field(default_factory=lambda: dict(INITIAL_REGISTERS))
    flags: dict[str, bool] = field(
        default_factory=lambda: {"zero": False, "sign": False, "carry": False}
    )
    eip: int = 0
    stack: list[Any] = field(default_factory=list)
    call_stack: list[int] = field(default_factory=list)
    storage: dict[str, Any] = field(default_factory=dict)
    instruction_count: int = 0
    max_instructions_to_exec: int = 1000


class Emulator:

    def __init__(
        self,
        asm_instructions: list[AsmInstruction],
        max_instructions_to_exec=1000,
        state: ASMEmulatorState | None = None,
        sub_agents: dict[str, SubAgent] | None = None,
    ):
        self._state = state or ASMEmulatorState(
            max_instructions_to_exec=max_instructions_to_exec
        )
        self._instructions: list[AsmInstruction] = asm_instructions
        self._sub_agents: dict[str, SubAgent] = sub_agents or {}
        self._labels: dict[str, int] = {}
        self.instruction_map: dict[str, Callable] = {
            "mov": self._mov,
            "push": self._push,
            "pop": self._pop,
            "add": partial(self._arith, "ADD", subtract=False),
            "sub": partial(self._arith, "SUB", subtract=True),
            "cmp": self._cmp,
            "call": self._call,
            "ret": self._ret,
            "jmp": self._jmp,
            "db": self._db,
            **{
                name: partial(self._jump_if, condition)
                for name, condition in JUMP_CONDITIONS.items()
            },
        }
        self._parse_labels()

    @classmethod
    def from_code(cls, asm_code: str) -> Self:
        instructions = [
            instruction
            for line in asm_code.strip().splitlines()
            if (instruction := AsmInstruction.from_row_line(line))
        ]

        sub_agent: SubAgent | None = None
        sub_agents: dict[str, SubAgent] = {}
        instructions_without_sub_agents = []
        for inst in instructions:
            if inst.command == "%macro":
                sub_agent = SubAgent(
                    name=inst.operands[0], include_path="", objective=""
                )
            elif inst.command == "%endmacro" and sub_agent:
                sub_agents[sub_agent.name] = sub_agent
                sub_agent = None
            elif inst.command == "%include" and sub_agent:
                sub_agent.include_path = validate_string(inst.operands[0])
            elif inst.command == "%define" and sub_agent:
                if inst.operands[0].lower().strip() == "objective":
                    sub_agent.objective = " ".join(inst.operands[1:])
                else:
                    sub_agent.outputs_spec[validate_string(inst.operands[0])] = (
                        " ".join(inst.operands[1:])
                    )
            elif sub_agent:
                sub_agent.instructions.append(inst)
            else:
                instructions_without_sub_agents.append(inst)

        return cls(instructions_without_sub_agents, sub_agents=sub_agents)

    def get_sub_agents(self) -> dict[str, SubAgent]:
        return self._sub_agents.copy()

    def execute_current_instruction(self) -> SubAgentContext | None:
        if self.is_finished():
            raise RuntimeError("ASM emulator finished execution")
        if self._state.eip < 0:
            raise RuntimeError("ASM stack pointer is less than 0")

        instruction = self._instructions[self._state.eip]
        logger.debug("exec_instruction: %s", str(instruction.origin).strip())

        if instruction.command.endswith(":") and not instruction.operands:
            self._state.eip += 1
            return None

        if instruction.command in self._sub_agents:
            agent = self._sub_agents[instruction.command]
            self._state.eip += 1
            return SubAgentContext(
                sub_agent=agent,
                output_keys=list(agent.outputs_spec.keys()),
                infer_result_hook=self.execute_sub_agent,
            )

        if instruction_handler := self.instruction_map.get(instruction.command):
            instruction_handler(*instruction.operands)
        else:
            self._state.eip += 1

        self._state.instruction_count += 1
        if self._state.instruction_count > self._state.max_instructions_to_exec:
            raise RuntimeError("Execution limit reached")

        return None

    def iter_tool_calls(self) -> Generator[SubAgentContext, None, None]:
        while not self.is_finished():
            instruction_result = self.execute_current_instruction()
            if isinstance(instruction_result, SubAgentContext):
                yield instruction_result

    def execute_sub_agent(
        self, agent_name: str, output_values: list[str | int]
    ) -> None:
        sub_agent = self._sub_agents[agent_name]
        for output_index, output_key in enumerate(sub_agent.outputs_spec):
            self._state.storage[validate_string(output_key)] = validate_string(
                output_values[output_index]
            )

        saved_eip = self._state.eip
        saved_instruction_count = self._state.instruction_count
        self._state.eip = 0

        child_emulator = Emulator(
            asm_instructions=sub_agent.instructions,
            state=self._state,
        )
        for _ in child_emulator.iter_tool_calls():
            pass

        self._state.eip = saved_eip
        self._state.instruction_count = saved_instruction_count

    def is_finished(self) -> bool:
        return self._state.eip >= len(self._instructions)

    def _parse_labels(self):
        for inst_index, inst in enumerate(self._instructions):
            if inst.command.endswith(":") and not inst.operands:
                self._labels[inst.command[:-1]] = inst_index

    def _get_register_value(self, reg_name: str) -> Any:
        if reg_name.startswith("r"):
            return self._state.storage.get(reg_name, 0)
        return self._state.registers.get(reg_name)

    def _set_register_value(self, reg_name: str, value: Any):
        value = maybe_number(value)
        if reg_name.startswith("r"):
            self._state.storage[reg_name] = value
            return
        if reg_name not in self._state.registers:
            raise RuntimeError("Unknown register")
        self._state.registers[reg_name] = value

    def _get_operand_value(self, operand: Any) -> Any:
        operand = operand.replace("[", "").replace("]", "")
        if (result := self._get_register_value(operand)) is not None:
            return result
        if (result := self._state.storage.get(operand)) is not None:
            return result
        try:
            return try_convert_to_numbers(operand)
        except ValueError:
            if operand.startswith('"') and operand.endswith('"'):
                return operand
            raise RuntimeError(
                "Can't find operand value in registers/storage or convert to int"
            )

    def _set_flags(self, *, zero: bool, sign: bool, carry: bool):
        self._state.flags.update(zero=zero, sign=sign, carry=carry)

    def _db(self, key_name: str, values_str: str):
        values_str = values_str.strip()
        if len(comma_separated_values := values_str.split(",")) > 1:
            try:
                if try_convert_to_numbers(comma_separated_values[-1]) == 0:
                    values_str = ",".join(comma_separated_values[:-1]).strip()
            except ValueError:
                pass

        undefined = object()
        value = undefined
        for candidate in (values_str, strip_outer_quotes(values_str)):
            try:
                value = json.loads(f"[{candidate}]")[0]
                break
            except json.JSONDecodeError:
                continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        if value is undefined:
            value = strip_outer_quotes(values_str)

        self._state.storage[key_name] = maybe_number(value)
        self._state.eip += 1

    def _mov(self, dest: str, src: str):
        src_value = self._get_operand_value(src)
        self._set_register_value(dest, src_value)
        self._state.eip += 1

    def _push(self, operand: Any):
        value = self._get_operand_value(operand)
        self._state.stack.append(value)
        if esp_value := self._get_register_value("esp"):
            self._set_register_value("esp", esp_value - 4)
        self._state.eip += 1

    def _pop(self, dest: str):
        if not self._state.stack:
            raise RuntimeError("Stack underflow")

        value = self._state.stack.pop()
        self._set_register_value(dest, value)
        if esp_value := self._get_register_value("esp"):
            self._set_register_value("esp", esp_value + 4)
        self._state.eip += 1

    def _arith(self, name: str, dest: str, src: str, *, subtract: bool):
        src_value = self._get_operand_value(src)
        dest_value = self._get_operand_value(dest)
        try:
            src_value = try_convert_to_numbers(src_value)
            dest_value = try_convert_to_numbers(dest_value)
        except ValueError:
            raise RuntimeError(
                f"Can't apply {name} command for none int/float operands"
            )

        result = dest_value - src_value if subtract else dest_value + src_value

        self._set_flags(
            zero=result == 0, sign=result < 0, carry=subtract and result < 0
        )
        self._set_register_value(dest, result)
        self._state.eip += 1

    def _cmp(self, src1: str, src2: str):
        src1_value = self._get_operand_value(src1)
        src2_value = self._get_operand_value(src2)

        try:
            src1_value = try_convert_to_numbers(src1_value)
            src2_value = try_convert_to_numbers(src2_value)
        except ValueError:
            src1_value = validate_string(src1_value)
            src2_value = validate_string(src2_value)
            self._set_flags(
                zero=src1_value == src2_value,
                sign=src1_value < src2_value,
                carry=src1_value < src2_value,
            )
        else:
            result = src1_value - src2_value
            self._set_flags(
                zero=result == 0, sign=result < 0, carry=src1_value < src2_value
            )
        self._state.eip += 1

    def _call(self, dest: str):
        self._state.eip += 1
        if dest in self._labels:
            self._state.call_stack.append(self._state.eip)
            self._state.eip = self._labels[dest]

    def _ret(self):
        if self._state.call_stack:
            self._state.eip = self._state.call_stack.pop()
            return
        self._state.eip = len(self._instructions)

    def _jmp(self, dest: str):
        if dest not in self._labels:
            raise RuntimeError("Label address not found")
        self._state.eip = self._labels[dest]

    def _jump_if(self, condition: Callable[[bool, bool], bool], dest: str):
        if condition(self._state.flags["zero"], self._state.flags["sign"]):
            self._jmp(dest)
            return
        self._state.eip += 1


NAMED_FLOATS: dict[str, float] = {
    "nan": float("nan"),
    "+nan": float("nan"),
    "-nan": float("nan"),
    "inf": float("inf"),
    "+inf": float("inf"),
    "infinity": float("inf"),
    "+infinity": float("inf"),
    "-inf": float("-inf"),
    "-infinity": float("-inf"),
}


def try_convert_to_numbers(operand: Any) -> float | int:
    if isinstance(operand, (int, float)):
        return operand

    operand = str(operand).lower()
    if (named := NAMED_FLOATS.get(operand)) is not None:
        return named

    if operand.endswith("h"):
        return int(operand[:-1], 16)
    if operand.endswith(("o", "q")):
        return int(operand[:-1], 8)
    if operand.endswith("b") or operand.startswith("0b"):
        return int(operand[:-1], 2)

    try:
        return int(operand, 0)
    except ValueError:
        pass
    return float(operand)


def maybe_number(value: Any) -> Any:
    """``try_convert_to_numbers`` but leaving non-numeric values untouched."""
    try:
        return try_convert_to_numbers(value)
    except ValueError:
        return value


def strip_outer_quotes(text: str) -> str:
    """Peel repeated wrapping quotes, e.g. the doubled quotes LLMs sometimes emit."""
    while text.startswith(('"', "'")) and text.endswith(('"', "'")):
        text = text[1:-1]
    return text


def validate_string(operand: Any) -> str:
    operand = str(operand).strip().lower()
    for quote in ("'", '"'):
        operand = operand.removeprefix(quote).removesuffix(quote)
    return operand
