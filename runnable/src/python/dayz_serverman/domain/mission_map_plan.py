"""Mission map plan document schema 1: stored and portable forms, versions, canonical JSON and fingerprint (D1)."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from enum import Enum
from typing import Any, Iterable

from .mission_map_geometry import Bounds, parse_calibration, parse_terrain
from .mission_map_objects import automatic_name, new_object_fields, object_bounds_problem, parse_object, vertex_count
from .mission_map_values import (
    CONTROL_CHARACTER, GLOBAL_ABUNDANCE_STEPS, IDENTIFIER, MAX_DOCUMENT_BYTES, MAX_DOCUMENT_DEPTH, MAX_OBJECTS,
    MAX_PLAN_VERTICES, PLAN_FORMAT, PLAN_VERSION, SHA256, TARGET_KEY, ObjectKind, ObjectRuleError, PlanProblem,
    PlanValidationError, PlanVersionError, document_error, exact_fields, integer, json_depth, matching_text,
    plain_name, refuse_json_constant, unique_json_keys,
)
from .profiles import ProfileValidationError, validate_profile_id, validate_relative_path


class PlanForm(str, Enum):
    """Stored form with every field, or portable export form without the stored-only fields."""

    STORED = "stored"
    PORTABLE = "portable"


# Top-level fields of each form; the stored-only ones never leave the manager (D7)
PORTABLE_FIELDS = frozenset(("format", "version", "terrain", "global_abundance", "objects", "background"))
STORED_ONLY_FIELDS = frozenset(("plan_id", "association", "records"))
STORED_FIELDS = PORTABLE_FIELDS | STORED_ONLY_FIELDS
# Fields of the stored-only association and records sections, and of the image background
ASSOCIATION_FIELDS = frozenset(("profile_id", "mission_root", "mission_key", "runtime_profile_key"))
RECORDS_FIELDS = frozenset(("originals", "baseline", "applied"))
BACKGROUND_FIELDS = frozenset(("file_name", "sha256", "byte_size", "pixel_width", "pixel_height", "calibration"))
# Object fields that do not change mission files, so the fingerprint leaves them out (R12)
PRESENTATION_FIELDS = frozenset(("name", "visible", "locked"))
# Largest integer that a record revision or an image size may state: the exact JSON integer range
MAX_SAFE_INTEGER = 2**53 - 1
# Longest image file name
MAX_FILE_NAME = 255


def parse_plan_text(text: str | bytes, form: PlanForm) -> dict[str, Any]:
    """Return a validated plan from UTF-8 JSON text of at most 8 MiB."""
    # Measure the exact UTF-8 bytes before parsing
    try:
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    except UnicodeError as error:
        raise document_error("the plan document is not UTF-8 text") from error
    if len(data) > MAX_DOCUMENT_BYTES:
        raise document_error("the plan document is larger than 8 MiB")
    # Parse strict JSON: no NaN or infinity, no repeated key, no runaway nesting
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=refuse_json_constant,
                           object_pairs_hook=unique_json_keys)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise document_error(f"the plan document is not valid UTF-8 JSON: {error}") from error
    return parse_plan(value, form)


def parse_plan(raw: object, form: PlanForm) -> dict[str, Any]:
    """Return a normalized copy of a plan document, or raise PlanValidationError with every problem."""
    if not isinstance(raw, dict):
        raise document_error("the plan document must be a JSON object")
    _check_version(raw)
    if json_depth(raw) > MAX_DOCUMENT_DEPTH:
        raise document_error(f"the plan document is nested deeper than {MAX_DOCUMENT_DEPTH} levels")
    # Refuse unknown or missing top-level fields of the chosen form
    expected = STORED_FIELDS if form is PlanForm.STORED else PORTABLE_FIELDS
    if set(raw) != expected:
        unknown, missing = sorted(set(raw) - expected), sorted(expected - set(raw))
        raise document_error(f"the plan document has unknown fields {unknown} or misses fields {missing}")
    # Validate the document-level sections
    try:
        plan: dict[str, Any] = {"format": PLAN_FORMAT, "version": PLAN_VERSION}
        plan["terrain"] = parse_terrain(raw["terrain"])
        steps = GLOBAL_ABUNDANCE_STEPS
        plan["global_abundance"] = integer(raw["global_abundance"], "global_abundance", steps[0], steps[-1])
        if plan["global_abundance"] not in steps:
            raise ObjectRuleError("global_abundance must be -50, -25, 0, 25, 50, 75 or 100")
        plan["background"] = parse_background(raw["background"], stored=form is PlanForm.STORED)
        if form is PlanForm.STORED:
            plan["plan_id"] = matching_text(raw["plan_id"], "plan_id", IDENTIFIER)
            plan["association"] = parse_association(raw["association"])
            plan["records"] = parse_records(raw["records"], plan["association"])
        if not isinstance(raw["objects"], list) or len(raw["objects"]) > MAX_OBJECTS:
            raise ObjectRuleError(f"objects must be a list of at most {MAX_OBJECTS} items")
    except ObjectRuleError as error:
        raise document_error(str(error)) from error
    # Validate every object and collect one problem for each failing object
    plan["objects"], problems = parse_objects(raw["objects"], bounds_of(plan))
    if not problems and sum(vertex_count(item) for item in plan["objects"]) > MAX_PLAN_VERTICES:
        problems.append(PlanProblem(f"the plan has more than {MAX_PLAN_VERTICES} polygon vertices"))
    if problems:
        raise PlanValidationError(problems)
    return plan


def parse_objects(raw_objects: list[object], bounds: Bounds) -> tuple[list[dict[str, Any]], list[PlanProblem]]:
    """Return the parsed objects and one problem for each object that fails a rule."""
    objects: list[dict[str, Any]] = []
    problems: list[PlanProblem] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_objects):
        identifier = raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None
        # Bounds-independent rules first, so such a failure is never reported as excludable
        try:
            item = parse_object(raw)
            if item["id"].casefold() in seen:
                raise ObjectRuleError("the identifier is already used in this plan")
            seen.add(item["id"].casefold())
            problem = object_bounds_problem(item, bounds)
            if problem is not None:
                raise ObjectRuleError(problem, bounds_dependent=True)
        except ObjectRuleError as error:
            problems.append(PlanProblem(str(error), index, identifier, error.bounds_dependent))
            continue
        objects.append(item)
    return objects, problems


def bounds_of(plan: dict[str, Any]) -> Bounds:
    """Return the terrain bounds of a parsed plan."""
    bounds = plan["terrain"]["bounds"]
    return Bounds(bounds["x_min"], bounds["z_min"], bounds["width"], bounds["height"])


def to_portable(plan: dict[str, Any]) -> dict[str, Any]:
    """Return the portable form of a stored plan: no stored-only field and no local image path."""
    portable = {key: copy.deepcopy(value) for key, value in plan.items() if key not in STORED_ONLY_FIELDS}
    if portable.get("background") is not None:
        portable["background"].pop("path", None)
    return portable


def canonical_json(value: object) -> bytes:
    """Return the canonical UTF-8 JSON bytes: sorted keys, no whitespace, no NaN (D3)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def configuration_fingerprint(plan: dict[str, Any]) -> str:
    """Return the SHA-256 digest of the configuration content that Apply turns into mission files."""
    content = {
        "bounds": plan["terrain"]["bounds"],
        "global_abundance": plan["global_abundance"],
        "objects": [{key: value for key, value in item.items() if key not in PRESENTATION_FIELDS}
                    for item in plan["objects"]],
    }
    return hashlib.sha256(canonical_json(_number_form(content))).hexdigest()


def _number_form(value: Any) -> Any:
    """Return the value with integral floats as integers, so -0.0, 0.0 and 0, and 2.0 and 2, are one number.

    JSON from the window does not keep that difference, so it must not show as a configuration change.
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _number_form(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_number_form(item) for item in value]
    return value


def has_draft_changes(plan: dict[str, Any], applied_fingerprint: str | None) -> bool:
    """Return whether the plan differs from the last-applied configuration (R15)."""
    # Without a last-applied record, any object or a non-neutral global value is a change
    if applied_fingerprint is None:
        return bool(plan["objects"]) or plan["global_abundance"] != 0
    return configuration_fingerprint(plan) != applied_fingerprint


def new_identifier(used: Iterable[str] = ()) -> str:
    """Return a random identifier that no used identifier matches without regard to case."""
    taken = {identifier.casefold() for identifier in used}
    while True:
        identifier = secrets.token_hex(8)
        if identifier not in taken:
            return identifier


def with_new_object(plan: dict[str, Any], kind: ObjectKind, **fields: Any) -> dict[str, Any]:
    """Return a copy of the plan with a new visible, unlocked object at index 0 (top of the list)."""
    objects = plan["objects"]
    identifier = new_identifier(existing["id"] for existing in objects)
    item = {**new_object_fields(kind, identifier, automatic_name(kind, objects)), **fields}
    # Validate the new object against the plan rules before it joins the list
    parsed, problems = parse_objects([item], bounds_of(plan))
    if not problems and parsed[0]["id"].casefold() in {existing["id"].casefold() for existing in objects}:
        problems.append(PlanProblem("the identifier is already used in this plan", 0, parsed[0]["id"]))
    # The plan-level limits hold for the result, as they hold for every saved plan
    vertices = sum(vertex_count(existing) for existing in objects + parsed)
    if not problems and (len(objects) + 1 > MAX_OBJECTS or vertices > MAX_PLAN_VERTICES):
        problems.append(PlanProblem(f"a plan holds at most {MAX_OBJECTS} objects and {MAX_PLAN_VERTICES} vertices"))
    if problems:
        raise PlanValidationError(problems)
    return {**copy.deepcopy(plan), "objects": parsed + copy.deepcopy(objects)}


def parse_association(value: object) -> dict[str, Any]:
    """Return the owner profile, the mission root relative to the DayZ root and the target keys (D2)."""
    raw = exact_fields(value, "association", ASSOCIATION_FIELDS)
    try:
        profile_id = validate_profile_id(raw["profile_id"])
        mission_root = validate_relative_path(raw["mission_root"], "mission_root")
    except ProfileValidationError as error:
        raise ObjectRuleError(str(error)) from error
    runtime_key = raw["runtime_profile_key"]
    return {"profile_id": profile_id, "mission_root": mission_root,
            "mission_key": matching_text(raw["mission_key"], "mission_key", TARGET_KEY),
            "runtime_profile_key": None if runtime_key is None else matching_text(
                runtime_key, "runtime_profile_key", TARGET_KEY)}


def parse_records(value: object, association: dict[str, Any]) -> dict[str, Any]:
    """Return the identity references to the protected originals, the working baseline and the last apply (D3)."""
    raw = exact_fields(value, "records", RECORDS_FIELDS)
    # At most one original for each target of the association: the mission and, if used, the runtime profile
    targets = {association["mission_key"], association["runtime_profile_key"]} - {None}
    if not isinstance(raw["originals"], list):
        raise ObjectRuleError("records.originals must be a list")
    originals: list[dict[str, str]] = []
    for item in raw["originals"]:
        original = exact_fields(item, "records.originals item", {"target_key", "manifest_sha256"})
        if original["target_key"] not in targets - {entry["target_key"] for entry in originals}:
            raise ObjectRuleError("records.originals names a target outside the association, or twice")
        originals.append({"target_key": original["target_key"],
                          "manifest_sha256": matching_text(original["manifest_sha256"], "manifest_sha256", SHA256)})
    result: dict[str, Any] = {"originals": originals, "baseline": None, "applied": None}
    # The baseline is named by its revision, the last apply by its operation ID
    if raw["baseline"] is not None:
        baseline = exact_fields(raw["baseline"], "records.baseline", {"revision", "manifest_sha256"})
        result["baseline"] = {"revision": integer(baseline["revision"], "revision", 0, MAX_SAFE_INTEGER),
                              "manifest_sha256": matching_text(baseline["manifest_sha256"], "manifest_sha256", SHA256)}
    if raw["applied"] is not None:
        applied = exact_fields(raw["applied"], "records.applied", {"operation_id", "manifest_sha256"})
        result["applied"] = {"operation_id": matching_text(applied["operation_id"], "operation_id", IDENTIFIER),
                             "manifest_sha256": matching_text(applied["manifest_sha256"], "manifest_sha256", SHA256)}
    return result


def parse_background(value: object, *, stored: bool) -> dict[str, Any] | None:
    """Return the optional image reference and calibration; only the stored form holds the local path (R04)."""
    if value is None:
        return None
    raw = exact_fields(value, "background", BACKGROUND_FIELDS | ({"path"} if stored else set()))
    # The file name is a plain name without folders
    file_name = plain_name(raw["file_name"], "file_name", MAX_FILE_NAME)
    if file_name != raw["file_name"] or "/" in file_name or "\\" in file_name:
        raise ObjectRuleError("file_name must be a plain file name")
    result: dict[str, Any] = {"file_name": file_name, "sha256": matching_text(raw["sha256"], "sha256", SHA256)}
    for name in ("byte_size", "pixel_width", "pixel_height"):
        result[name] = integer(raw[name], name, 1, MAX_SAFE_INTEGER)
    result["calibration"] = parse_calibration(raw["calibration"])
    # A missing image keeps its reference without a path (D7)
    if stored:
        path = raw["path"]
        if path is not None and (not isinstance(path, str) or not path or CONTROL_CHARACTER.search(path)):
            raise ObjectRuleError("background path must be null or text without control characters")
        result["path"] = path
    return result


def _check_version(raw: dict[str, Any]) -> None:
    """Apply the version policy: version 1 parses, a higher one came from a newer build."""
    if raw.get("format") != PLAN_FORMAT:
        raise document_error("the document is not a mission map plan; it has no plan format marker")
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise document_error("the plan version must be an integer")
    if version > PLAN_VERSION:
        raise PlanVersionError((PlanProblem(f"the plan version {version} was created by a newer build"),))
    if version < PLAN_VERSION:
        raise document_error(f"the plan version {version} is not supported")
