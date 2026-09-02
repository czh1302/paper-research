from __future__ import annotations

import json
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

from .models import PilotSpecification
from .validation_bundle import ValidationBundleError, validation_input_paths


class PilotSpecificationValidationError(ValueError):
    """A frozen pilot contract is structurally unsafe or not executable."""


def validate_pilot_specification(specification: PilotSpecification) -> None:
    """Apply the deterministic validation shared by analysis and experiment workers."""
    try:
        validation_input_paths(specification)
    except ValidationBundleError as error:
        raise PilotSpecificationValidationError(str(error)) from error

    if specification.execution_mode == "code_only":
        raise PilotSpecificationValidationError(
            "New report Ideas require an executable CPU or CPU-proxy experiment"
        )

    resource_hosts = {
        (urlparse(resource.url).hostname or "").casefold()
        for resource in specification.resources
    }
    allowed = {item.casefold() for item in specification.allowed_hosts}
    direct_inference_domains = (
        "anthropic.com",
        "deepseek.com",
        "openai.com",
        "generativelanguage.googleapis.com",
        "api.together.xyz",
        "api.groq.com",
    )
    if any(
        (candidate := rule.removeprefix("*.")) == domain
        or candidate.endswith(f".{domain}")
        for rule in allowed
        for domain in direct_inference_domains
    ):
        raise PilotSpecificationValidationError(
            "Hosted model providers cannot be added to the subject network allow-list"
        )
    if specification.requires_live_inference and not specification.inference_contracts:
        raise PilotSpecificationValidationError(
            "Live managed inference requires a complete frozen protocol"
        )
    for host in resource_hosts:
        if host not in allowed and not any(
            rule.startswith("*.") and host.endswith(rule[1:]) for rule in allowed
        ):
            raise PilotSpecificationValidationError(
                f"Public resource host {host!r} is absent from the frozen network allow-list"
            )

    primary = specification.primary_metric_key
    if any(primary not in case.metrics for case in specification.evaluator_cases):
        raise PilotSpecificationValidationError(
            "Every evaluator fixture must include the primary metric"
        )
    if not any(item.expected_pass for item in specification.evaluator_cases) or not any(
        not item.expected_pass for item in specification.evaluator_cases
    ):
        raise PilotSpecificationValidationError(
            "The evaluator contract needs both a passing and a failing fixture"
        )

    schema = specification.metrics_json_schema
    serialized_schema = json.dumps(schema, ensure_ascii=False)
    if len(serialized_schema) > 20_000 or '"$ref"' in serialized_schema:
        raise PilotSpecificationValidationError(
            "The frozen metric schema is too large or contains external references"
        )
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        raise PilotSpecificationValidationError(
            "The metrics JSON schema is invalid"
        ) from error

    required = set(schema.get("required") or [])
    properties = set((schema.get("properties") or {}).keys())
    metric_pointers = [item.json_pointer for item in specification.metrics]
    metric_pointers.extend(
        pointer
        for item in specification.metrics
        for pointer in (item.baseline_json_pointer, item.intervention_json_pointer)
        if pointer
    )
    top_level_metric_fields = {
        pointer.lstrip("/").split("/")[0] for pointer in metric_pointers
    }
    if not top_level_metric_fields.issubset(properties):
        raise PilotSpecificationValidationError(
            "The metrics schema does not declare every metric JSON pointer"
        )
    if not top_level_metric_fields.issubset(required):
        raise PilotSpecificationValidationError(
            "Metric fields must be required by the frozen JSON schema"
        )
    for field in top_level_metric_fields:
        if (schema.get("properties", {}).get(field) or {}).get("type") not in {
            "number",
            "integer",
        }:
            raise PilotSpecificationValidationError(
                "Every declared metric must use a numeric JSON schema type"
            )
