"""Vocabulary-aware OData metadata exploration engine.

Produces a structured markdown report by reasoning over SAP vocabulary
annotations (sap:label, sap:text, sap:unit, sap:semantics, sap:quickinfo,
sap:value-list, sap:display-format) and V4 Common.ValueList annotations.
"""

from __future__ import annotations

from .models import (
    Association,
    EntitySet,
    EntityType,
    Property,
    ServiceMetadata,
    ValueListInfo,
)


def explore_service(metadata: ServiceMetadata) -> str:
    """Generate a vocabulary-aware exploration report from parsed metadata."""
    et_by_name: dict[str, EntityType] = {
        et.name: et for et in metadata.entity_types
    }
    es_by_type: dict[str, EntitySet] = {}
    for es in metadata.entity_sets:
        short = es.entity_type.split(".")[-1]
        es_by_type[short] = es

    sections = [
        _overview(metadata),
        _entity_sets(metadata, et_by_name),
        _field_analysis(metadata, et_by_name),
        _value_helps(metadata, et_by_name),
        _relationships(metadata, et_by_name),
        _actions_and_functions(metadata),
    ]
    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _overview(metadata: ServiceMetadata) -> str:
    lines = ["# Service Overview"]
    if metadata.schema_namespace:
        lines.append(f"- **Namespace**: `{metadata.schema_namespace}`")
    biz_sets = [es for es in metadata.entity_sets if not _is_sap_infra(es.name)]
    infra_sets = len(metadata.entity_sets) - len(biz_sets)
    lines.append(f"- **Entity Sets**: {len(biz_sets)}")
    lines.append(f"- **Entity Types**: {len(biz_sets)}")
    if infra_sets:
        lines.append(f"- **Framework Entity Sets** (hidden): {infra_sets}")

    n_actions = len(metadata.actions) + len(metadata.action_imports)
    n_functions = (
        len(metadata.functions)
        + len(metadata.function_imports)
        + len(metadata.function_imports_v4)
    )
    if n_actions:
        lines.append(f"- **Actions**: {n_actions}")
    if n_functions:
        lines.append(f"- **Functions**: {n_functions}")
    if metadata.value_lists:
        lines.append(f"- **Value Helps**: {len(metadata.value_lists)}")
    return "\n".join(lines)


def _entity_sets(
    metadata: ServiceMetadata, et_by_name: dict[str, EntityType]
) -> str:
    if not metadata.entity_sets:
        return ""
    lines = ["# Entity Sets"]
    for es in [e for e in metadata.entity_sets if not _is_sap_infra(e.name)]:
        short_type = es.entity_type.split(".")[-1]
        caps = []
        if es.creatable:
            caps.append("C")
        if es.updatable:
            caps.append("U")
        if es.deletable:
            caps.append("D")
        caps_str = f" `[{'/'.join(caps)}]`" if caps else " `[read-only]`"

        et = et_by_name.get(short_type)
        if et and et.key_properties:
            key_parts = []
            for kp in et.key_properties:
                prop = _find_prop(et, kp)
                if prop and prop.label:
                    key_parts.append(f"{kp} ({prop.label})")
                else:
                    key_parts.append(kp)
            keys_str = f" — keys: {', '.join(key_parts)}"
        else:
            keys_str = ""

        lines.append(f"- **{es.name}** → `{short_type}`{caps_str}{keys_str}")
    return "\n".join(lines)


def _field_analysis(
    metadata: ServiceMetadata, et_by_name: dict[str, EntityType]
) -> str:
    if not metadata.entity_types:
        return ""
    lines = ["# Field Analysis"]

    for et in [e for e in metadata.entity_types if not _is_sap_infra(e.name)]:
        et_lines = _analyze_entity_type(et, metadata)
        if et_lines:
            lines.append(f"\n## {et.name}")
            lines.extend(et_lines)

    return "\n".join(lines) if len(lines) > 1 else ""


def _analyze_entity_type(
    et: EntityType, metadata: ServiceMetadata
) -> list[str]:
    lines: list[str] = []

    # Filter out RAP control fields (_ac/_oc)
    props = [p for p in et.properties if not _is_control_field(p)]

    # Keys & required fields
    keys: list[str] = []
    required: list[str] = []
    for prop in props:
        desc = _prop_short_desc(prop)
        if prop.name in et.key_properties:
            keys.append(desc)
        elif not prop.nullable:
            required.append(desc)
    if keys:
        lines.append(f"**Keys**: {', '.join(keys)}")
    if required:
        lines.append(f"**Required**: {', '.join(required)}")

    # Code → text associations
    text_assocs = [
        f"`{p.name}` → `{p.text}`"
        for p in props
        if p.text
    ]
    if text_assocs:
        lines.append(f"**Code → Display Text**: {'; '.join(text_assocs)}")

    # Measures with units
    unit_assocs = [
        f"`{p.name}` in `{p.unit}`"
        for p in props
        if p.unit
    ]
    if unit_assocs:
        lines.append(f"**Measures**: {'; '.join(unit_assocs)}")

    # Semantic fields
    sem_fields = [
        f"`{p.name}` ({p.semantics})"
        for p in props
        if p.semantics
    ]
    if sem_fields:
        lines.append(f"**Semantic Fields**: {'; '.join(sem_fields)}")

    # DDIC quickinfo (only when it adds info beyond label)
    ddic_info = [
        f"`{p.name}`: {p.quickinfo}"
        for p in props
        if p.quickinfo and p.quickinfo != p.label
    ]
    if ddic_info:
        lines.append(f"**DDIC Descriptions**: {'; '.join(ddic_info)}")

    # Display hints
    display_hints = [
        f"`{p.name}` → {p.display_format}"
        for p in props
        if p.display_format
    ]
    if display_hints:
        lines.append(f"**Display Hints**: {'; '.join(display_hints)}")

    # Full property table
    lines.append("")
    lines.append("| Field | Type | Label | Attributes |")
    lines.append("|-------|------|-------|------------|")
    for prop in props:
        short_type = prop.type.replace("Edm.", "")
        label = prop.label or ""
        attrs = _prop_attrs(prop, et, metadata)
        lines.append(f"| {prop.name} | {short_type} | {label} | {attrs} |")

    return lines


def _prop_short_desc(prop: Property) -> str:
    """Short description: 'Name (Label)' or just 'Name'."""
    if prop.label:
        return f"`{prop.name}` ({prop.label})"
    return f"`{prop.name}`"


def _prop_attrs(
    prop: Property, et: EntityType, metadata: ServiceMetadata
) -> str:
    """Compile attribute tags for a property."""
    attrs: list[str] = []
    if prop.name in et.key_properties:
        attrs.append("KEY")
    if not prop.nullable:
        attrs.append("required")
    if prop.max_length:
        attrs.append(f"max={prop.max_length}")
    if prop.text:
        attrs.append(f"text→{prop.text}")
    if prop.unit:
        attrs.append(f"unit→{prop.unit}")
    if prop.semantics:
        attrs.append(f"sem:{prop.semantics}")
    if prop.display_format:
        attrs.append(f"display:{prop.display_format}")
    vl_key = f"{et.name}/{prop.name}"
    if vl_key in metadata.value_lists:
        vl = metadata.value_lists[vl_key]
        attrs.append(f"valueHelp→{vl.collection_path}")
    if not prop.filterable:
        attrs.append("no-filter")
    if not prop.sortable:
        attrs.append("no-sort")
    return ", ".join(attrs)


def _value_helps(
    metadata: ServiceMetadata, et_by_name: dict[str, EntityType]
) -> str:
    if not metadata.value_lists:
        return ""
    lines = ["# Value Helps (Lookup Fields)"]

    for target_key, vl in sorted(metadata.value_lists.items()):
        parts = target_key.split("/")
        if len(parts) == 2:
            et_name, prop_name = parts
        else:
            et_name, prop_name = target_key, ""

        label_part = f" — \"{vl.label}\"" if vl.label else ""
        lines.append(
            f"\n### {target_key}{label_part}"
        )
        lines.append(f"Lookup entity set: **{vl.collection_path}**")

        key_mappings: list[str] = []
        auto_fill: list[str] = []
        display_only: list[str] = []
        filter_only: list[str] = []
        constants: list[str] = []

        for p in vl.parameters:
            if p.type in ("InOut", "In"):
                key_mappings.append(
                    f"`{p.local_property}` ↔ `{p.value_list_property}`"
                )
            elif p.type == "Out":
                auto_fill.append(
                    f"`{p.local_property}` ← `{p.value_list_property}`"
                )
            elif p.type == "DisplayOnly":
                display_only.append(f"`{p.value_list_property}`")
            elif p.type == "FilterOnly":
                filter_only.append(f"`{p.value_list_property}`")
            elif p.type == "Constant":
                constants.append(f"`{p.value_list_property}`")

        if key_mappings:
            lines.append(f"- **Key mapping**: {', '.join(key_mappings)}")
        if auto_fill:
            lines.append(f"- **Auto-fill on selection**: {', '.join(auto_fill)}")
        if display_only:
            lines.append(
                f"- **Shown in dropdown**: {', '.join(display_only)}"
            )
        if filter_only:
            lines.append(f"- **Filter columns**: {', '.join(filter_only)}")
        if constants:
            lines.append(f"- **Constants**: {', '.join(constants)}")
        if vl.search_supported:
            lines.append("- **Search**: supported")

    return "\n".join(lines)


def _relationships(
    metadata: ServiceMetadata, et_by_name: dict[str, EntityType]
) -> str:
    if not metadata.associations:
        return ""

    # Build role → entity type short name map from associations
    role_to_type: dict[str, str] = {}
    for assoc in metadata.associations:
        if assoc.end1:
            role_to_type[assoc.end1.role] = assoc.end1.entity_type.split(".")[-1]
        if assoc.end2:
            role_to_type[assoc.end2.role] = assoc.end2.entity_type.split(".")[-1]

    lines = ["# Relationships"]

    for assoc in metadata.associations:
        if not assoc.end1 or not assoc.end2:
            continue
        t1 = assoc.end1.entity_type.split(".")[-1]
        t2 = assoc.end2.entity_type.split(".")[-1]
        if _is_sap_infra(t1) or _is_sap_infra(t2):
            continue
        m1 = assoc.end1.multiplicity
        m2 = assoc.end2.multiplicity

        # Find navigation property that uses this association
        nav_name = ""
        source_et = et_by_name.get(t1)
        if source_et:
            for nav in source_et.navigation_properties:
                rel_short = nav.relationship.split(".")[-1]
                if rel_short == assoc.name:
                    nav_name = nav.name
                    break

        nav_str = f" via `{nav_name}`" if nav_name else ""
        lines.append(f"- **{t1}** ({m1}) → **{t2}** ({m2}){nav_str}")

    return "\n".join(lines)


def _actions_and_functions(metadata: ServiceMetadata) -> str:
    has_any = (
        metadata.actions
        or metadata.functions
        or metadata.function_imports
        or metadata.action_imports
        or metadata.function_imports_v4
    )
    if not has_any:
        return ""

    lines = ["# Actions & Functions"]

    # V4 Actions
    for action in metadata.actions:
        bound = " [bound]" if action.is_bound else ""
        params = ", ".join(f"{p.name}: {p.type}" for p in action.parameters)
        ret = f" → `{action.return_type}`" if action.return_type else ""
        lines.append(f"- **POST** `{action.name}({params})`{ret}{bound}")

    # V4 Functions
    for func in metadata.functions:
        bound = " [bound]" if func.is_bound else ""
        composable = " [composable]" if func.is_composable else ""
        params = ", ".join(f"{p.name}: {p.type}" for p in func.parameters)
        ret = f" → `{func.return_type}`" if func.return_type else ""
        lines.append(
            f"- **GET** `{func.name}({params})`{ret}{bound}{composable}"
        )

    # V4 Action Imports
    for ai in metadata.action_imports:
        es = f" → {ai.entity_set_path}" if ai.entity_set_path else ""
        lines.append(f"- **ActionImport** `{ai.name}` (action: `{ai.action}`){es}")

    # V4 Function Imports
    for fi in metadata.function_imports_v4:
        es = f" → {fi.entity_set_path}" if fi.entity_set_path else ""
        lines.append(
            f"- **FunctionImport** `{fi.name}` (function: `{fi.function}`){es}"
        )

    # V2 Function Imports
    for fi in metadata.function_imports:
        params = ", ".join(
            f"{p.name}: {p.type.replace('Edm.', '')}" for p in fi.parameters
        )
        ret = f" → `{fi.return_type}`" if fi.return_type else ""
        lines.append(f"- **{fi.http_method}** `{fi.name}({params})`{ret}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_prop(et: EntityType, name: str) -> Property | None:
    """Find a property by name in an entity type."""
    for p in et.properties:
        if p.name == name:
            return p
    return None


def _is_sap_infra(name: str) -> bool:
    """Check if an entity type/set name is SAP framework infrastructure."""
    return name.startswith("SAP__")


def _is_control_field(prop: Property) -> bool:
    """Check if a property is a RAP dynamic action/CBA control field."""
    return prop.name.endswith("_ac") or prop.name.endswith("_oc")
