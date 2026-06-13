# Simplified and tuned emulator based on https://github.com/electronick1/LLAssembly project

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generator, Self

logger = logging.getLogger("llassembly_asm")
logger.addHandler(logging.NullHandler())


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
    infer_result_hook: Callable[[str, dict[str, Any]], None]

    def infer_result(self, agent_name: str, result: Any):
        self.infer_result_hook(agent_name, result)


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

        # remove `,` and handle cases like `move eax,ebcx`
        parts = " ".join(parts).lower().replace(",", " ").split()
        operands = [op.strip() for op in parts[1:]]
        return cls(origin=origin, command=parts[0], operands=operands)


class Register(Enum):
    """
    Enumeration of the registers understood by the emulator.
    ``r0`` … ``r100`` are treated as special storage keys.
    """

    EAX = "eax"
    EBX = "ebx"
    ECX = "ecx"
    EDX = "edx"
    ESI = "esi"
    EDI = "edi"
    ESP = "esp"
    EBP = "ebp"
    EIP = "eip"
    FLAGS = "flags"


class Flag(Enum):
    ZERO = "zero"
    SIGN = "sign"
    OVERFLOW = "overflow"
    CARRY = "carry"


class ASMEmulatorState:
    def __init__(self, max_instructions_to_exec=1000):
        self.registers: dict[Register, Any] = {
            Register.EAX: 0,
            Register.EBX: 0,
            Register.ECX: 0,
            Register.EDX: 0,
            Register.ESI: 0,
            Register.EDI: 0,
            Register.ESP: 0x1000,
            Register.EBP: 0,
            Register.FLAGS: 0,
        }
        self.flags: dict[Flag, bool] = {
            Flag.ZERO: False,
            Flag.SIGN: False,
            Flag.CARRY: False,
            Flag.OVERFLOW: False,
        }
        self.eip: int = 0
        self.stack: list[Any] = []
        self.call_stack: list[int] = []
        self.storage: dict[str, Any] = {}
        self.instruction_count: int = 0
        self.max_instructions_to_exec: int = max_instructions_to_exec

    def to_dict(self) -> dict:
        return {
            "registers": {reg.value: val for reg, val in self.registers.items()},
            "flags": {flag.value: val for flag, val in self.flags.items()},
            "eip": self.eip,
            "stack": self.stack,
            "call_stack": self.call_stack,
            "storage": self.storage,
            "instruction_count": self.instruction_count,
            "max_instructions_to_exec": self.max_instructions_to_exec,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Self:
        self = cls.__new__(cls)
        self.registers = {
            Register(reg) if isinstance(reg, str) else reg: val
            for reg, val in d["registers"].items()
        }
        self.flags = {
            Flag(flag) if isinstance(flag, str) else flag: val
            for flag, val in d["flags"].items()
        }
        self.eip = d["eip"]
        self.stack = d["stack"]
        self.call_stack = d["call_stack"]
        self.storage = d["storage"]
        self.instruction_count = d["instruction_count"]
        self.max_instructions_to_exec = d["max_instructions_to_exec"]
        return self


class Emulator:

    def __init__(
        self,
        asm_instructions: list[AsmInstruction],
        max_instructions_to_exec=1000,
        state: ASMEmulatorState | None = None,
        sub_agents: dict[str, SubAgent] | None = None,
    ):
        self._state = state or ASMEmulatorState(max_instructions_to_exec)
        self._instructions: list[AsmInstruction] = asm_instructions
        self._sub_agents: dict[str, SubAgent] = sub_agents or {}
        self._labels: dict[str, int] = {}
        self.instruction_map: dict[str, Callable] = {
            "mov": self._mov,
            "push": self._push,
            "pop": self._pop,
            "add": self._add,
            "sub": self._sub,
            "cmp": self._cmp,
            "call": self._call,
            "ret": self._ret,
            "jmp": self._jmp,
            "je": self._je,
            "jne": self._jne,
            "jl": self._jl,
            "jlt": self._jl,
            "jle": self._jle,
            "jg": self._jg,
            "jgt": self._jg,
            "jge": self._jge,
            "js": self._js,
            "jns": self._jns,
            "db": self._db,
        }
        self._parse_labels()

    @classmethod
    def from_code(cls, asm_code: str) -> Self:
        instructions = []
        sub_agents: dict[str, SubAgent] = {}

        for line in asm_code.strip().splitlines():
            if instruction := AsmInstruction.from_row_line(line):
                instructions.append(instruction)

        sub_agent: SubAgent | None = None
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

    def get_state(self) -> ASMEmulatorState:
        return self._state

    def get_instructions(self) -> list[AsmInstruction]:
        return self._instructions.copy()

    def get_instruction(self, inst_index: int) -> AsmInstruction | None:
        if inst_index < 0 or inst_index >= len(self._instructions):
            return None
        return copy.copy(self._instructions[inst_index])

    def get_current_instruction_index(self) -> int:
        return self._state.eip

    def get_sub_agents(self) -> dict[str, SubAgent]:
        return self._sub_agents.copy()

    def execute_current_instruction(self) -> SubAgentContext | None:
        if self.is_finished():
            raise RuntimeError("ASM emulator finished execution")
        if self._state.eip < 0:
            raise RuntimeError("ASM stack pointer is less than 0")

        instruction = self._instructions[self._state.eip]
        logger.debug("exec_instruction: %s", str(instruction.origin).strip())

        # Skip labels
        if instruction.command.endswith(":") and not instruction.operands:
            self._state.eip += 1
            return None

        # Check if this is a sub-agent invocation (bare agent_<name>)
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
    ) -> Generator[SubAgentContext, None, None]:
        # Populate output values into state storage before child runs
        sub_agent = self._sub_agents[agent_name]
        for output_index, output_key in enumerate(sub_agent.outputs_spec):
            self._state.storage[validate_string(output_key)] = validate_string(
                output_values[output_index]
            )

        child_emulator = Emulator(
            asm_instructions=sub_agent.instructions,
            state=self._state,
        )
        for call in child_emulator.iter_tool_calls():
            pass

    def get_call_jmp_index(self, instruction_index: int) -> list[int] | None:
        if instruction_index < 0 or instruction_index >= len(self._instructions):
            return None

        instruction = self._instructions[instruction_index]
        if instruction.command == "ret":
            label_call_indexes = self.get_instruction_indexes_when_label_called()
            return [index + 1 for index in label_call_indexes] + [
                len(self._instructions)
            ]

        if (
            instruction.command == "call"
            and instruction.operands
            and instruction.operands[0] in self._labels
        ):
            return [self._labels[instruction.operands[0]]]

        return None

    def get_jmp_index(self, instruction_index: int) -> int | None:
        if instruction_index < 0 or instruction_index >= len(self._instructions):
            return None

        instruction = self._instructions[instruction_index]
        if (
            instruction.command
            in {
                "jmp",
                "je",
                "jne",
                "jl",
                "jle",
                "jg",
                "jge",
                "js",
                "jns",
                "jlt",
                "jgt",
            }
            and instruction.operands
        ):
            return self._labels.get(instruction.operands[0])

        return None

    def get_instruction_indexes_when_label_called(self) -> list[int]:
        indexes_when_label_called = []
        for instruction_index, instruction in enumerate(self._instructions):
            if (
                instruction.command == "call"
                and instruction.operands
                and instruction.operands[0] in self._labels
            ):
                indexes_when_label_called.append(instruction_index)
        return indexes_when_label_called

    def is_finished(self) -> bool:
        return self._state.eip >= len(self._instructions)

    def reset_state(
        self,
        new_state: ASMEmulatorState | None = None,
        max_instructions_to_exec: int = 1000,
    ):
        self._state = new_state or ASMEmulatorState(
            max_instructions_to_exec=max_instructions_to_exec
        )

    def _get_register_value(self, reg_name: str) -> Any:
        if reg_name.startswith("r"):
            # R0..R100 - are special registers added in the prompt message
            # to extend storage space and simplify ASM logic.
            return self._state.storage.get(reg_name, 0)
        if reg_name not in Register:
            return None
        reg = Register(reg_name)
        return self._state.registers[reg]

    def _set_register_value(self, reg_name: str, value: Any):
        try:
            value = try_convert_to_numbers(value)
        except ValueError:
            pass
        if reg_name.startswith("r"):
            # R0..R100 - are special registers added in the prompt message
            # to extend storage space and simplify ASM logic.
            self._state.storage[reg_name] = value
            return
        if reg_name not in Register:
            raise RuntimeError("Unknown register")
        reg = Register(reg_name)
        self._state.registers[reg] = value

    def _get_flag_value(self, flag: Flag) -> bool:
        return self._state.flags[flag]

    def _set_flag_value(self, flag: Flag, value: bool):
        self._state.flags[flag] = value

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

    def _set_operand_value(self, operand: str, value: Any):
        self._set_register_value(operand, value)

    def _db(self, key_name: str, values_str: str):
        values_str = values_str.strip()
        # By the prompt definition llm must use one string or json-string like format,
        # but following parsing extends this rules by a bit to cover hallucinations.
        try:
            if (
                (comma_separated_values := values_str.split(","))
                and try_convert_to_numbers(comma_separated_values[-1]) == 0
                and len(comma_separated_values) > 1
            ):
                values_str = ",".join(comma_separated_values[:-1]).strip()
        except ValueError:
            pass

        undefined = object()
        value = undefined
        try:
            # Trying to convert `db` stmt as a list of values
            value = json.loads(f"[{values_str.strip()}]")[0]
        except json.JSONDecodeError:
            pass
        if value is undefined:
            # Trying to convert `db` stmt as a list of values but considering
            # that LLM may add additional quotes
            try:
                strip_quotes = values_str
                while strip_quotes.startswith(('"', "'")) and strip_quotes.endswith(
                    ('"', "'")
                ):
                    strip_quotes = strip_quotes[1:-1]
                value = json.loads(f"[{strip_quotes}]")[0]
            except json.JSONDecodeError:
                pass
        if isinstance(value, str):
            # Try to parse nested json
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        if value is undefined:
            # Can't convert to json
            strip_quotes = values_str
            while strip_quotes.startswith(('"', "'")) and strip_quotes.endswith(
                ('"', "'")
            ):
                strip_quotes = strip_quotes[1:-1]
            value = strip_quotes
        try:
            # Try to convert to numbers by default
            value = try_convert_to_numbers(value)
        except ValueError:
            pass
        self._state.storage[key_name] = value
        self._state.eip += 1

    def _mov(self, dest: str, src: str):
        src_value = self._get_operand_value(src)
        self._set_operand_value(dest, src_value)
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
        self._set_operand_value(dest, value)
        if esp_value := self._get_register_value("esp"):
            self._set_register_value("esp", esp_value + 4)
        self._state.eip += 1

    def _add(self, dest: str, src: str):
        src_value = self._get_operand_value(src)
        dest_value = self._get_operand_value(dest)
        try:
            src_value = try_convert_to_numbers(src_value)
            dest_value = try_convert_to_numbers(dest_value)
        except ValueError:
            raise RuntimeError("Can't apply ADD command for none int/float operands")

        result = dest_value + src_value

        self._set_flag_value(Flag.ZERO, result == 0)
        self._set_flag_value(Flag.SIGN, result < 0)
        self._set_flag_value(Flag.CARRY, False)
        self._set_operand_value(dest, result)
        self._state.eip += 1

    def _sub(self, dest: str, src: str):
        src_value = self._get_operand_value(src)
        dest_value = self._get_operand_value(dest)
        try:
            src_value = try_convert_to_numbers(src_value)
            dest_value = try_convert_to_numbers(dest_value)
        except ValueError:
            raise RuntimeError("Can't apply SUB command for none int/float operands")
        result = dest_value - src_value

        self._set_flag_value(Flag.ZERO, result == 0)
        self._set_flag_value(Flag.SIGN, result < 0)
        self._set_flag_value(Flag.CARRY, result < 0)
        self._set_operand_value(dest, result)
        self._state.eip += 1

    def _cmp(self, src1: str, src2: str):
        src1_value = self._get_operand_value(src1)
        src2_value = self._get_operand_value(src2)

        try:
            src1_value = try_convert_to_numbers(src1_value)
            src2_value = try_convert_to_numbers(src2_value)
            result = src1_value - src2_value
        except ValueError:
            src1_value = validate_string(src1_value)
            src2_value = validate_string(src2_value)
            self._set_flag_value(Flag.ZERO, src1_value == src2_value)
            self._set_flag_value(Flag.SIGN, src1_value < src2_value)
            self._set_flag_value(Flag.CARRY, src1_value < src2_value)
            self._state.eip += 1
            return

        self._set_flag_value(Flag.ZERO, result == 0)
        self._set_flag_value(Flag.SIGN, result < 0)
        self._set_flag_value(Flag.CARRY, src1_value < src2_value)
        self._state.eip += 1

    def _call(self, dest: str):
        self._state.eip += 1
        if dest in self._labels:
            self._state.call_stack.append(self._state.eip)
            self._state.eip = self._labels[dest]
            return None
        return dest

    def _ret(self):
        if self._state.call_stack:
            self._state.eip = self._state.call_stack.pop()
            return
        self._state.eip = len(self._instructions)

    def _jmp(self, dest: str):
        if dest in self._labels:
            self._state.eip = self._labels[dest]
            return
        raise RuntimeError("Label address not found")

    def _je(self, dest: str):
        # Jump if equal instruction.
        if self._get_flag_value(Flag.ZERO):
            self._jmp(dest)
            return
        self._state.eip += 1

    def _jne(self, dest: str):
        # Jump if not equal instruction.
        if not self._get_flag_value(Flag.ZERO):
            self._jmp(dest)
            return
        self._state.eip += 1

    def _jl(self, dest: str):
        # Jump if less instruction.
        sign = self._get_flag_value(Flag.SIGN)
        if sign:
            self._jmp(dest)
            return
        self._state.eip += 1

    def _jle(self, dest: str):
        # Jump if less or equal instruction.
        sign = self._get_flag_value(Flag.SIGN)
        if self._get_flag_value(Flag.ZERO) or sign:
            self._jmp(dest)
            return
        self._state.eip += 1

    def _jg(self, dest: str):
        # Jump if greater instruction.
        sign = self._get_flag_value(Flag.SIGN)
        if not self._get_flag_value(Flag.ZERO) and not sign:
            self._jmp(dest)
            return
        self._state.eip += 1

    def _jge(self, dest: str):
        # Jump if greater or equal instruction.
        sign = self._get_flag_value(Flag.SIGN)
        if not sign:
            self._jmp(dest)
            return
        self._state.eip += 1

    def _js(self, dest: str):
        # Jump if sign instruction.
        if self._get_flag_value(Flag.SIGN):
            self._jmp(dest)
            return
        self._state.eip += 1

    def _jns(self, dest: str):
        # Jump if not sign instruction.
        if not self._get_flag_value(Flag.SIGN):
            self._jmp(dest)
            return
        self._state.eip += 1

    def _parse_labels(self):
        for inst_index, inst in enumerate(self._instructions):
            if inst.command.endswith(":") and not inst.operands:
                self._labels[inst.command[:-1]] = inst_index


def try_convert_to_numbers(operand: Any) -> float | int:
    if isinstance(operand, (int, float)):
        return operand

    operand = str(operand).lower()
    if operand in ("nan", "+nan", "-nan"):
        return float("nan")
    if operand in ("inf", "+inf", "infinity", "+infinity"):
        return float("inf")
    if operand in ("-inf", "-infinity"):
        return float("-inf")

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


def validate_string(operand: Any) -> str:
    operand = str(operand).strip().lower()
    remove_pref_suf = ("'", '"')
    for pref_suf in remove_pref_suf:
        if operand.startswith(pref_suf):
            operand = operand[len(pref_suf) :]
        if operand.endswith(pref_suf):
            operand = operand[: -len(pref_suf)]
    return operand
