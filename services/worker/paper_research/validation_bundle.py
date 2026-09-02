from __future__ import annotations

import ast
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass

from .experiment_models import safe_repository_path, specification_hash
from .models import PilotSpecification

VALIDATION_BUNDLE_VERSION = 1


class ValidationBundleError(ValueError):
    """A formal-validation input bundle failed its frozen manifest checks."""


@dataclass(frozen=True, slots=True)
class ValidationInput:
    path: str
    content: bytes


_SAFE_EVALUATOR_IMPORTS = {
    "collections",
    "csv",
    "dataclasses",
    "decimal",
    "fractions",
    "functools",
    "itertools",
    "json",
    "math",
    "pathlib",
    "statistics",
    "typing",
    "pytest",
}
_DYNAMIC_CODE_NAMES = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "breakpoint",
}
_UNSAFE_ATTRIBUTE_NAMES = {
    "system",
    "popen",
    "spawn",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "fork",
    "forkpty",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
}


def validate_frozen_evaluator_sources(specification: PilotSpecification) -> None:
    """Reject evaluator features that can interpret raw data as executable code."""
    local_modules = {
        item.path.removesuffix(".py").replace("/", ".")
        for item in specification.evaluator_files
        if item.path.endswith(".py")
    }
    for item in specification.evaluator_files:
        if not item.path.endswith(".py"):
            raise ValidationBundleError(
                "Formal validation evaluator files must be auditable Python source"
            )
        try:
            tree = ast.parse(item.content, filename=item.path)
        except (SyntaxError, ValueError) as error:
            raise ValidationBundleError(
                f"Frozen evaluator source is invalid: {item.path}"
            ) from error
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for module in modules:
                    root = module.split(".", 1)[0]
                    if (
                        root not in _SAFE_EVALUATOR_IMPORTS
                        and module not in local_modules
                        and not any(name.startswith(f"{module}.") for name in local_modules)
                    ):
                        raise ValidationBundleError(
                            f"Frozen evaluator imports an unsafe module: {module or '<relative>'}"
                        )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in _DYNAMIC_CODE_NAMES:
                    raise ValidationBundleError(
                        "Frozen evaluator cannot execute dynamically supplied code"
                    )
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("__"):
                    raise ValidationBundleError(
                        "Frozen evaluator cannot traverse Python runtime internals"
                    )
                if node.attr in _UNSAFE_ATTRIBUTE_NAMES:
                    raise ValidationBundleError(
                        "Frozen evaluator cannot invoke operating-system processes"
                    )


def _canonical_json(content: bytes, path: str) -> bytes:
    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationBundleError(
                    f"Formal validation JSON contains a duplicate key: {path}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValidationBundleError(
            f"Formal validation JSON contains a non-finite number: {value}"
        )

    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except ValidationBundleError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValidationBundleError(
            f"Formal validation input is not strict UTF-8 JSON: {path}"
        ) from error
    if not isinstance(payload, (dict, list)):
        raise ValidationBundleError(
            f"Formal validation JSON must contain an object or array: {path}"
        )
    stack: list[tuple[object, int]] = [(payload, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > 250_000 or depth > 64:
            raise ValidationBundleError(
                f"Formal validation JSON exceeds structural limits: {path}"
            )
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValidationBundleError(
            f"Formal validation JSON cannot be canonicalized: {path}"
        ) from error


def validation_input_paths(specification: PilotSpecification) -> list[str]:
    """Return the exact raw artifacts an isolated evaluator may observe.

    The editable repository's final metrics file is deliberately excluded: it
    is an output of the frozen evaluator, never evidence supplied by the
    experimental subject. Repository archives are excluded for the same
    reason; only individually declared, bounded regular files cross the trust
    boundary.
    """

    metrics_path = safe_repository_path(specification.metrics_output_path)
    if not metrics_path.startswith("artifacts/"):
        raise ValidationBundleError(
            "Formal validation metrics output must be isolated under artifacts/"
        )
    if not any(
        item.path == metrics_path and item.kind == "metrics"
        for item in specification.artifacts
    ):
        raise ValidationBundleError(
            "Formal validation metrics output must be declared as a metrics artifact"
        )
    paths = [
        safe_repository_path(item.path)
        for item in specification.artifacts
        if item.path != specification.metrics_output_path and item.kind == "table"
    ]
    if not paths:
        raise ValidationBundleError(
            "Formal validation requires at least one declared raw evaluator input"
        )
    if len(paths) != len(set(paths)):
        raise ValidationBundleError(
            "Formal validation evaluator inputs must use unique paths"
        )
    if any(not path.startswith("artifacts/") for path in paths):
        raise ValidationBundleError(
            "Formal validation evaluator inputs must be isolated under artifacts/"
        )
    if any(not path.casefold().endswith(".json") for path in paths):
        raise ValidationBundleError(
            "Formal validation evaluator inputs must use canonical JSON files"
        )
    validate_frozen_evaluator_sources(specification)
    return paths


def build_validation_bundle(
    specification: PilotSpecification,
    inputs: list[ValidationInput],
    *,
    max_file_bytes: int,
    max_total_bytes: int,
) -> bytes:
    expected_paths = validation_input_paths(specification)
    supplied = {item.path: item.content for item in inputs}
    if len(supplied) != len(inputs) or set(supplied) != set(expected_paths):
        raise ValidationBundleError(
            "Formal validation inputs do not match the frozen artifact manifest"
        )

    total = 0
    manifest_files: list[dict[str, str | int]] = []
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, path in enumerate(expected_paths):
            if len(supplied[path]) > max_file_bytes:
                raise ValidationBundleError(
                    f"Formal validation input exceeds its file limit: {path}"
                )
            content = _canonical_json(supplied[path], path)
            if len(content) > max_file_bytes:
                raise ValidationBundleError(
                    f"Formal validation input exceeds its file limit: {path}"
                )
            total += len(content)
            if total > max_total_bytes:
                raise ValidationBundleError(
                    "Formal validation inputs exceed their aggregate limit"
                )
            member = f"payload/{index:03d}"
            digest = hashlib.sha256(content).hexdigest()
            archive.writestr(member, content)
            manifest_files.append(
                {
                    "path": path,
                    "member": member,
                    "byte_size": len(content),
                    "sha256": digest,
                }
            )
        manifest = {
            "version": VALIDATION_BUNDLE_VERSION,
            "specification_hash": specification_hash(specification),
            "files": manifest_files,
        }
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
        )
    payload = output.getvalue()
    if len(payload) > max_total_bytes:
        raise ValidationBundleError(
            "Formal validation input bundle exceeds its archive limit"
        )
    return payload


def parse_validation_bundle(
    specification: PilotSpecification,
    payload: bytes,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
) -> list[ValidationInput]:
    if len(payload) > max_total_bytes:
        raise ValidationBundleError(
            "Formal validation input bundle exceeds its archive limit"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise ValidationBundleError("Formal validation input bundle is invalid") from error

    with archive:
        names = archive.namelist()
        if len(names) > 32 or len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValidationBundleError("Formal validation bundle inventory is invalid")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationBundleError(
                "Formal validation bundle manifest is invalid"
            ) from error
        if (
            not isinstance(manifest, dict)
            or manifest.get("version") != VALIDATION_BUNDLE_VERSION
            or manifest.get("specification_hash")
            != specification_hash(specification)
            or not isinstance(manifest.get("files"), list)
        ):
            raise ValidationBundleError(
                "Formal validation bundle does not match the frozen specification"
            )

        expected_paths = validation_input_paths(specification)
        records = manifest["files"]
        if [item.get("path") for item in records if isinstance(item, dict)] != expected_paths:
            raise ValidationBundleError(
                "Formal validation bundle paths do not match the frozen manifest"
            )
        expected_members = {"manifest.json"}
        result: list[ValidationInput] = []
        total = 0
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValidationBundleError("Formal validation bundle record is invalid")
            member = f"payload/{index:03d}"
            if record.get("member") != member:
                raise ValidationBundleError("Formal validation bundle member is invalid")
            expected_members.add(member)
            info = archive.getinfo(member)
            declared_size = record.get("byte_size")
            if (
                not isinstance(declared_size, int)
                or declared_size < 0
                or declared_size != info.file_size
                or info.file_size > max_file_bytes
            ):
                raise ValidationBundleError(
                    "Formal validation bundle file size is invalid"
                )
            total += info.file_size
            if total > max_total_bytes:
                raise ValidationBundleError(
                    "Formal validation inputs exceed their aggregate limit"
                )
            content = archive.read(member)
            if hashlib.sha256(content).hexdigest() != record.get("sha256"):
                raise ValidationBundleError(
                    "Formal validation bundle content hash is invalid"
                )
            result.append(ValidationInput(path=expected_paths[index], content=content))
        if set(names) != expected_members:
            raise ValidationBundleError(
                "Formal validation bundle contains undeclared files"
            )
        return result
