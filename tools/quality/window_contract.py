"""Pure compatibility-policy helpers for the PolarRMWindow test harness.

The historic all-member snapshot remains an independent audit artifact. This
policy checks established public members, explicitly retained private caller
seams, and actual Python call signatures without freezing implementation owners.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import hashlib
import inspect
import json
from pathlib import Path

SCHEMA_VERSION = 1
PYTHON_CALLABLE_KINDS = frozenset({"method", "bound-method", "bound-classmethod"})


@dataclass(frozen=True)
class EnumSignatureDefault:
    value: Enum

    def __repr__(self) -> str:
        kind = type(self.value)
        return f"<{kind.__module__}.{kind.__qualname__}: {self.value.value!r}>"


def callable_signature(value: object) -> str | None:
    """Capture accepted parameters/defaults, excluding annotations and owners."""
    if not (inspect.ismethod(value) or inspect.isfunction(value)):
        return None
    signature = inspect.signature(value)
    parameters = []
    for parameter in signature.parameters.values():
        default = parameter.default
        # Enum repr spellings can vary (notably Qt IntFlags), but converting
        # to a scalar would hide a type-changing default and can break calls.
        # Preserve the enum's full type identity and stable underlying value.
        if isinstance(default, Enum):
            default = EnumSignatureDefault(default)
        parameters.append(parameter.replace(annotation=inspect.Parameter.empty, default=default))
    return str(signature.replace(parameters=parameters, return_annotation=inspect.Signature.empty))


def validate_private_retirements(contract: dict, baseline: dict) -> None:
    """Retirement records are reviewed history, never public API exemptions."""
    retired = contract.get("retired_private_members", {})
    if not isinstance(retired, dict):
        raise ValueError("Private retirement evidence must be an object")
    for name, evidence in retired.items():
        if not isinstance(name, str) or not name.startswith("_") or name not in baseline["members"]:
            raise ValueError(f"Invalid retired private member: {name}")
        if name in contract["private_members"]:
            raise ValueError(f"Retired private member is still protected: {name}")
        if not isinstance(evidence, dict) or not all(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ("retired_at", "reason", "replacement_owner", "implementation_stage")
        ):
            raise ValueError(f"Private retirement lacks review evidence: {name}")
        try:
            date.fromisoformat(evidence["retired_at"])
        except ValueError as exc:
            raise ValueError(f"Invalid private retirement date: {name}") from exc
        for key in ("previous_caller_evidence", "behavior_tests"):
            values = evidence.get(key)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                raise ValueError(f"Private retirement lacks {key}: {name}")


def load_contract(path: Path, baseline: dict) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "source_revision", "policy", "private_members", "python_signatures", "legacy_snapshot_sha256"}
    if not isinstance(contract, dict) or not required <= contract.keys():
        raise ValueError("API contract is incomplete")
    digest = hashlib.sha256(json.dumps(baseline, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    if contract["legacy_snapshot_sha256"] != digest:
        raise ValueError("Historic API snapshot changed; review its contract evidence instead of refreshing it")
    if contract["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported API contract version")
    if not contract["source_revision"] or contract["policy"] != "public-baseline-and-explicit-private-seams":
        raise ValueError("API contract requires an explicit reviewed policy and source revision")
    for name, evidence in contract["private_members"].items():
        if not name.startswith("_") or name not in baseline["members"]:
            raise ValueError(f"Invalid private compatibility member: {name}")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and ":" in item for item in evidence):
            raise ValueError(f"Private member lacks caller evidence: {name}")
    validate_private_retirements(contract, baseline)
    for name, record in contract.get('frontend_implementations', {}).items():
        if name.startswith('_') or name not in baseline['members']:
            raise ValueError(f'Invalid frontend implementation: {name}')
        if not record.get('reason') or not record.get('behavior_test'):
            raise ValueError(f'Frontend implementation lacks behavior evidence: {name}')
        expected = record.get('expected', {})
        if not {'kind', 'type', 'sig'} <= expected.keys() or set(expected)-{'kind','type','sig','call_signature'}:
            raise ValueError(f'Invalid frontend implementation evidence: {name}')
        if expected['kind'] in PYTHON_CALLABLE_KINDS and not expected.get('call_signature'):
            raise ValueError(f'Frontend override lacks call signature: {name}')
    for name, evidence in contract.get("removed_feature_members", {}).items():
        if name not in baseline["members"] or name.startswith("_"):
            raise ValueError(f"Invalid removed feature member: {name}")
        if not isinstance(evidence, dict) or not all(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ("feature", "reason", "removed_at", "behavior_test")
        ):
            raise ValueError(f"Removed feature lacks evidence: {name}")
        date.fromisoformat(evidence["removed_at"])
    expected_python = {name for name in protected_names(baseline, contract)
                       if baseline["members"][name]["kind"] in PYTHON_CALLABLE_KINDS}
    if set(contract["python_signatures"]) != expected_python:
        missing = sorted(expected_python - contract["python_signatures"].keys())
        extra = sorted(contract["python_signatures"].keys() - expected_python)
        raise ValueError(f"Python signature coverage differs: missing={missing}, extra={extra}")
    if not all(isinstance(signature, str) and signature.startswith("(")
               for signature in contract["python_signatures"].values()):
        raise ValueError("Invalid Python call signature")
    return contract


def protected_names(baseline: dict, contract: dict) -> set[str]:
    public = {name for name in baseline["members"] if not name.startswith("_")}
    return (public - set(contract.get("removed_feature_members", {}))) | set(contract["private_members"])


def compatibility_snapshots(baseline: dict, current: dict, contract: dict) -> tuple[dict, dict]:
    """Project immutable legacy evidence and current evidence onto the contract.

    Additive members and unlisted private storage are outside this gate. Removed
    or changed protected members remain failures. Only the recorded baseline can
    classify a value as volatile; a changed current value cannot exempt itself.
    """
    names = protected_names(baseline, contract)
    before, after = {}, {}
    for name in sorted(names):
        if name in baseline["members"]:
            before[name] = dict(baseline["members"][name])
        if name in current["members"]:
            after[name] = dict(current["members"][name])
            implementation = contract.get('frontend_implementations', {}).get(name)
            if implementation and all(after[name].get(key) == value
                                      for key, value in implementation['expected'].items()):
                # Exact reviewed UI additions, not arbitrary type/name or
                # signature exemptions. Historical evidence remains immutable.
                after[name] = dict(before[name])
            if not before.get(name, {}).get("volatile"):
                after[name].pop("volatile", None)
        if name in contract["python_signatures"]:
            before[name]["sig"] = contract["python_signatures"][name]
            if name in after:
                # Missing evidence fails comparison; never fall back to a
                # qualname that says nothing about accepted arguments.
                after[name]["sig"] = after[name].get("call_signature", "<missing call signature>")
                after[name].pop("volatile", None)
    return ({**baseline, "members": before}, {**current, "members": after})
