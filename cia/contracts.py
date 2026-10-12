"""Contracts of the Business-Intelligence-System, pinned as the submodule `vendor/bis`.

The schemas reference each other by file name, resolved against their `$id`; the registry
below makes both spellings resolvable (same approach as the BIS contract tests)."""

import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACTS_TAG = "contracts-v3-draft"
CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "vendor" / "bis" / "contracts"


class ContractError(ValueError):
    """A payload does not match its contract."""

    def __init__(self, contract: str, errors: list[str]):
        super().__init__(f"{contract}: {'; '.join(errors)}")
        self.contract = contract
        self.errors = errors


@cache
def _schemas() -> dict[str, dict]:
    if not CONTRACTS_DIR.is_dir():
        raise RuntimeError(f"Contracts missing at {CONTRACTS_DIR}: run `git submodule update --init`")
    return {
        path.name.removesuffix(".schema.json"): json.loads(path.read_text(encoding="utf-8"))
        for path in CONTRACTS_DIR.glob("*.schema.json")
    }


@cache
def _registry() -> Registry:
    return Registry().with_resources(
        (key, Resource.from_contents(schema))
        for name, schema in _schemas().items()
        for key in (f"{name}.schema.json", schema["$id"])
    )


def names() -> list[str]:
    return sorted(_schemas())


def errors(contract: str, payload: object) -> list[str]:
    schema = _schemas().get(contract)
    if schema is None:
        raise KeyError(f"Unknown contract {contract!r}")
    validator = Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())
    return [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(payload)]


def validate(contract: str, payload: object) -> None:
    if found := errors(contract, payload):
        raise ContractError(contract, found)
