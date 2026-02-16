from __future__ import annotations

from xml.etree import ElementTree as ET

from .models import (
    Annotation,
    Association,
    AssociationEnd,
    EntitySet,
    EntityType,
    FunctionImport,
    NavigationProperty,
    Property,
    ServiceMetadata,
    ValueListInfo,
    ValueListParameter,
)

# OData V2 EDMX namespaces
NS = {
    "edmx": "http://schemas.microsoft.com/ado/2007/06/edmx",
    "edm": "http://schemas.microsoft.com/ado/2008/09/edm",
    "sap": "http://www.sap.com/Protocols/SAPData",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
}

# V4-style annotation namespace used in mixed V2/V4 metadata
NS_EDM_V4 = "http://docs.oasis-open.org/odata/ns/edm"


def _sap(elem: ET.Element, attr: str, default: str = "") -> str:
    return elem.get(f"{{{NS['sap']}}}{attr}", default)


def _bool(value: str, default: bool = True) -> bool:
    if not value:
        return default
    return value.lower() == "true"


def parse_metadata(xml_text: str) -> ServiceMetadata:
    root = ET.fromstring(xml_text)
    metadata = ServiceMetadata()

    # Find Schema element — try multiple EDM namespace versions
    schema = None
    for ns_uri in [
        "http://schemas.microsoft.com/ado/2008/09/edm",
        "http://schemas.microsoft.com/ado/2006/04/edm",
        "http://schemas.microsoft.com/ado/2009/11/edm",
    ]:
        schema = root.find(f".//{{{ns_uri}}}Schema")
        if schema is not None:
            NS["edm"] = ns_uri
            break

    if schema is None:
        return metadata

    metadata.schema_namespace = schema.get("Namespace", "")

    # Parse EntityTypes
    for et_elem in schema.findall(f"{{{NS['edm']}}}EntityType"):
        entity_type = _parse_entity_type(et_elem)
        metadata.entity_types.append(entity_type)

    # Parse Associations
    for assoc_elem in schema.findall(f"{{{NS['edm']}}}Association"):
        association = _parse_association(assoc_elem)
        metadata.associations.append(association)

    # Parse EntityContainer
    container = schema.find(f"{{{NS['edm']}}}EntityContainer")
    if container is not None:
        for es_elem in container.findall(f"{{{NS['edm']}}}EntitySet"):
            entity_set = _parse_entity_set(es_elem)
            metadata.entity_sets.append(entity_set)

        for fi_elem in container.findall(f"{{{NS['edm']}}}FunctionImport"):
            func_import = _parse_function_import(fi_elem)
            metadata.function_imports.append(func_import)

    # Parse vocabulary annotation blocks (V4-style <Annotations>)
    _parse_annotations(root, metadata)

    return metadata


def _parse_entity_type(elem: ET.Element) -> EntityType:
    name = elem.get("Name", "")
    et = EntityType(name=name)

    # Key properties
    key_elem = elem.find(f"{{{NS['edm']}}}Key")
    if key_elem is not None:
        for prop_ref in key_elem.findall(f"{{{NS['edm']}}}PropertyRef"):
            et.key_properties.append(prop_ref.get("Name", ""))

    # Properties
    for prop_elem in elem.findall(f"{{{NS['edm']}}}Property"):
        et.properties.append(_parse_property(prop_elem))

    # Navigation properties
    for nav_elem in elem.findall(f"{{{NS['edm']}}}NavigationProperty"):
        et.navigation_properties.append(NavigationProperty(
            name=nav_elem.get("Name", ""),
            relationship=nav_elem.get("Relationship", ""),
            from_role=nav_elem.get("FromRole", ""),
            to_role=nav_elem.get("ToRole", ""),
        ))

    return et


def _parse_property(elem: ET.Element) -> Property:
    max_len = elem.get("MaxLength")
    return Property(
        name=elem.get("Name", ""),
        type=elem.get("Type", ""),
        nullable=_bool(elem.get("Nullable", "true")),
        max_length=int(max_len) if max_len and max_len.isdigit() else None,
        label=_sap(elem, "label"),
        filterable=_bool(_sap(elem, "filterable"), True),
        sortable=_bool(_sap(elem, "sortable"), True),
        creatable=_bool(_sap(elem, "creatable"), True),
        updatable=_bool(_sap(elem, "updatable"), True),
        text=_sap(elem, "text"),
        quickinfo=_sap(elem, "quickinfo"),
        value_list=_sap(elem, "value-list"),
        unit=_sap(elem, "unit"),
        semantics=_sap(elem, "semantics"),
        display_format=_sap(elem, "display-format"),
    )


def _parse_association(elem: ET.Element) -> Association:
    assoc = Association(name=elem.get("Name", ""))
    ends = elem.findall(f"{{{NS['edm']}}}End")
    for i, end_elem in enumerate(ends[:2]):
        end = AssociationEnd(
            role=end_elem.get("Role", ""),
            entity_type=end_elem.get("Type", ""),
            multiplicity=end_elem.get("Multiplicity", ""),
        )
        if i == 0:
            assoc.end1 = end
        else:
            assoc.end2 = end
    return assoc


def _parse_entity_set(elem: ET.Element) -> EntitySet:
    return EntitySet(
        name=elem.get("Name", ""),
        entity_type=elem.get("EntityType", ""),
        addressable=_bool(_sap(elem, "addressable"), True),
        creatable=_bool(_sap(elem, "creatable"), True),
        updatable=_bool(_sap(elem, "updatable"), True),
        deletable=_bool(_sap(elem, "deletable"), True),
    )


def _parse_function_import(elem: ET.Element) -> FunctionImport:
    fi = FunctionImport(
        name=elem.get("Name", ""),
        http_method=elem.get(f"{{{NS['m']}}}HttpMethod", "GET"),
        return_type=elem.get("ReturnType", ""),
    )
    for param_elem in elem.findall(f"{{{NS['edm']}}}Parameter"):
        fi.parameters.append(_parse_property(param_elem))
    return fi


# ---------------------------------------------------------------------------
# Vocabulary annotation parsing (V4-style <Annotations Target="...">)
# ---------------------------------------------------------------------------

def _parse_annotations(root: ET.Element, metadata: ServiceMetadata) -> None:
    """Parse all <Annotations> blocks from the metadata document."""
    # Search in both the V4 EDM namespace and V2 EDM namespace (SAP sometimes
    # embeds V4-style annotations inside the V2 schema using the V2 namespace)
    for ns in (NS_EDM_V4, NS["edm"]):
        for ann_block in root.iter(f"{{{ns}}}Annotations"):
            target = ann_block.get("Target", "")
            if not target:
                continue
            # Strip namespace prefix from target (e.g. "SAP.ConnectionType" -> "ConnectionType")
            short_target = _strip_namespace(target, metadata.schema_namespace)
            _parse_annotation_block(ann_block, ns, short_target, metadata)


def _strip_namespace(target: str, schema_ns: str) -> str:
    """Remove leading schema namespace from a target string."""
    if schema_ns and target.startswith(schema_ns + "."):
        return target[len(schema_ns) + 1:]
    return target


def _parse_annotation_block(
    block: ET.Element,
    ns: str,
    target: str,
    metadata: ServiceMetadata,
) -> None:
    """Parse child <Annotation> elements within an <Annotations> block."""
    for ann_elem in block:
        tag = ann_elem.tag
        # Accept both V4 and V2 namespace Annotation elements
        if tag not in (f"{{{NS_EDM_V4}}}Annotation", f"{{{NS['edm']}}}Annotation"):
            continue

        term = ann_elem.get("Term", "")
        qualifier = ann_elem.get("Qualifier", "")
        value = _extract_annotation_value(ann_elem, ns)

        annotation = Annotation(term=term, qualifier=qualifier, value=value)
        metadata.annotations.setdefault(target, []).append(annotation)

        # Handle specific well-known annotations
        short_term = term.split(".")[-1] if "." in term else term

        if term in ("com.sap.vocabularies.Common.v1.ValueList", "Common.ValueList"):
            vl = _parse_value_list(ann_elem, ns)
            if vl:
                metadata.value_lists[target] = vl

        elif short_term in ("InsertRestrictions", "UpdateRestrictions", "DeleteRestrictions"):
            _apply_capability_restriction(short_term, value, target, metadata)


def _extract_annotation_value(elem: ET.Element, ns: str) -> object:
    """Extract the value from an <Annotation> element.

    Handles: inline String/Bool/EnumMember attributes, nested <Record>,
    <Collection>, <PropertyValue> structures.
    """
    # Check for inline value attributes
    for attr in ("String", "Bool", "EnumMember", "Int", "Path", "EnumValue"):
        val = elem.get(attr)
        if val is not None:
            if attr == "Bool":
                return val.lower() == "true"
            return val

    # Check for child value elements
    for child_ns in (ns, NS_EDM_V4, NS["edm"]):
        # <String>, <Bool>, etc.
        for simple in ("String", "Bool", "Int", "Path", "EnumMember"):
            child = elem.find(f"{{{child_ns}}}{simple}")
            if child is not None and child.text:
                if simple == "Bool":
                    return child.text.strip().lower() == "true"
                return child.text.strip()

        # <Record>
        record = elem.find(f"{{{child_ns}}}Record")
        if record is not None:
            return _parse_record(record, child_ns)

        # <Collection>
        collection = elem.find(f"{{{child_ns}}}Collection")
        if collection is not None:
            return _parse_collection(collection, child_ns)

    return None


def _parse_record(elem: ET.Element, ns: str) -> dict:
    """Parse a <Record> element into a dict."""
    result: dict = {}
    rec_type = elem.get("Type", "")
    if rec_type:
        result["$Type"] = rec_type

    for child_ns in (ns, NS_EDM_V4, NS["edm"]):
        for pv in elem.findall(f"{{{child_ns}}}PropertyValue"):
            prop_name = pv.get("Property", "")
            if not prop_name:
                continue
            # Check inline value
            prop_val = pv.get("String") or pv.get("Bool") or pv.get("Path") or pv.get("EnumMember")
            if prop_val is not None:
                if pv.get("Bool") is not None:
                    result[prop_name] = prop_val.lower() == "true"
                else:
                    result[prop_name] = prop_val
            else:
                result[prop_name] = _extract_annotation_value(pv, ns)
    return result


def _parse_collection(elem: ET.Element, ns: str) -> list:
    """Parse a <Collection> element into a list."""
    items = []
    for child in elem:
        tag_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag_local == "Record":
            items.append(_parse_record(child, ns))
        elif tag_local == "PropertyPath":
            items.append(child.text.strip() if child.text else "")
        elif tag_local == "String":
            items.append(child.text.strip() if child.text else "")
        elif tag_local == "AnnotationPath":
            items.append(child.text.strip() if child.text else "")
        else:
            items.append(child.text.strip() if child.text else "")
    return items


def _parse_value_list(ann_elem: ET.Element, ns: str) -> ValueListInfo | None:
    """Parse a Common.ValueList annotation into a ValueListInfo."""
    value = _extract_annotation_value(ann_elem, ns)
    if not isinstance(value, dict):
        return None

    vl = ValueListInfo(
        label=value.get("Label", ""),
        collection_path=value.get("CollectionPath", ""),
        search_supported=bool(value.get("SearchSupported", False)),
    )

    params = value.get("Parameters")
    if isinstance(params, list):
        for p in params:
            if not isinstance(p, dict):
                continue
            p_type_raw = p.get("$Type", "")
            # Extract short type: "Common.ValueListParameterInOut" -> "InOut"
            p_type = p_type_raw
            for prefix in (
                "com.sap.vocabularies.Common.v1.ValueListParameter",
                "Common.ValueListParameter",
            ):
                if p_type_raw.startswith(prefix):
                    p_type = p_type_raw[len(prefix):]
                    break

            vl.parameters.append(ValueListParameter(
                type=p_type,
                local_property=p.get("LocalDataProperty", ""),
                value_list_property=p.get("ValueListProperty", ""),
            ))

    return vl


def _apply_capability_restriction(
    term_short: str,
    value: object,
    target: str,
    metadata: ServiceMetadata,
) -> None:
    """Apply Capabilities.*Restrictions to the matching EntitySet."""
    if not isinstance(value, dict):
        return

    # Target for capability restrictions is usually the entity set name
    # (could be "Namespace.EntitySetName" — we strip the namespace)
    es_name = target.split("/")[0]

    for es in metadata.entity_sets:
        if es.name == es_name:
            insertable = value.get("Insertable")
            updatable = value.get("Updatable")
            deletable = value.get("Deletable")

            if term_short == "InsertRestrictions" and insertable is not None:
                es.creatable = bool(insertable)
            elif term_short == "UpdateRestrictions" and updatable is not None:
                es.updatable = bool(updatable)
            elif term_short == "DeleteRestrictions" and deletable is not None:
                es.deletable = bool(deletable)
            break


# ---------------------------------------------------------------------------
# LLM-friendly description
# ---------------------------------------------------------------------------

def describe(metadata: ServiceMetadata) -> str:
    """Generate an LLM-friendly plain text description of the service metadata."""
    lines: list[str] = []

    if metadata.schema_namespace:
        lines.append(f"Service: {metadata.schema_namespace}")
        lines.append("")

    # Build a lookup: entity type name -> EntityType
    et_by_name: dict[str, EntityType] = {et.name: et for et in metadata.entity_types}

    # Entity Sets (what you can actually query)
    if metadata.entity_sets:
        lines.append("=== Entity Sets (queryable endpoints) ===")
        for es in metadata.entity_sets:
            short_type = es.entity_type.split(".")[-1] if "." in es.entity_type else es.entity_type
            caps = []
            if es.creatable:
                caps.append("create")
            if es.updatable:
                caps.append("update")
            if es.deletable:
                caps.append("delete")
            caps_str = f" [{', '.join(caps)}]" if caps else ""
            lines.append(f"  {es.name} -> {short_type}{caps_str}")
        lines.append("")

    # Entity Types with properties
    if metadata.entity_types:
        lines.append("=== Entity Types ===")
        for et in metadata.entity_types:
            lines.append(f"\n  {et.name}")
            lines.append(f"    Keys: {', '.join(et.key_properties)}")

            for prop in et.properties:
                short_type = prop.type.replace("Edm.", "")
                parts = [f"    - {prop.name}: {short_type}"]
                if prop.label:
                    parts.append(f'"{prop.label}"')
                attrs = []
                if prop.name in et.key_properties:
                    attrs.append("KEY")
                if not prop.nullable:
                    attrs.append("required")
                if prop.max_length:
                    attrs.append(f"max={prop.max_length}")
                if prop.text:
                    attrs.append(f"text->{prop.text}")
                if prop.unit:
                    attrs.append(f"unit->{prop.unit}")
                if prop.semantics:
                    attrs.append(f"semantics={prop.semantics}")
                if prop.display_format:
                    attrs.append(f"display={prop.display_format}")
                # Check if this property has a value help
                vl_key = f"{et.name}/{prop.name}"
                vl = metadata.value_lists.get(vl_key)
                if vl:
                    attrs.append(f"value-help->{vl.collection_path}")
                if not prop.filterable:
                    attrs.append("not-filterable")
                if not prop.sortable:
                    attrs.append("not-sortable")
                if attrs:
                    parts.append(f"({', '.join(attrs)})")
                lines.append(" ".join(parts))

            if et.navigation_properties:
                lines.append("    Navigation:")
                for nav in et.navigation_properties:
                    lines.append(f"      -> {nav.name}")

    # Associations
    if metadata.associations:
        lines.append("\n=== Relationships ===")
        for assoc in metadata.associations:
            if assoc.end1 and assoc.end2:
                t1 = assoc.end1.entity_type.split(".")[-1]
                t2 = assoc.end2.entity_type.split(".")[-1]
                lines.append(
                    f"  {t1} ({assoc.end1.multiplicity}) "
                    f"<-> {t2} ({assoc.end2.multiplicity})"
                    f"  [{assoc.name}]"
                )

    # Function Imports
    if metadata.function_imports:
        lines.append("\n=== Function Imports (actions) ===")
        for fi in metadata.function_imports:
            params = ", ".join(
                f"{p.name}: {p.type.replace('Edm.', '')}" for p in fi.parameters
            )
            ret = f" -> {fi.return_type}" if fi.return_type else ""
            lines.append(f"  {fi.http_method} {fi.name}({params}){ret}")

    # Value Helps
    if metadata.value_lists:
        lines.append("\n=== Value Helps ===")
        for target_key, vl in sorted(metadata.value_lists.items()):
            search = "yes" if vl.search_supported else "no"
            label_part = f' "{vl.label}"' if vl.label else ""
            lines.append(f"  {target_key} -> {vl.collection_path}{label_part} (search: {search})")
            for p in vl.parameters:
                if p.type in ("InOut", "In"):
                    lines.append(f"    {p.type}: {p.local_property} <-> {p.value_list_property}")
                elif p.type == "Out":
                    lines.append(f"    Out: {p.local_property} <- {p.value_list_property}")
                elif p.type == "DisplayOnly":
                    lines.append(f"    Display: {p.value_list_property}")
                elif p.type == "Constant":
                    lines.append(f"    Constant: {p.value_list_property}")
                elif p.type == "FilterOnly":
                    lines.append(f"    Filter: {p.value_list_property}")
                else:
                    lines.append(f"    {p.type}: {p.local_property} <-> {p.value_list_property}")

    # Text Associations summary
    text_assocs = []
    for et in metadata.entity_types:
        for prop in et.properties:
            if prop.text:
                text_assocs.append((et.name, prop.name, prop.text))
    if text_assocs:
        lines.append("\n=== Text Associations ===")
        for et_name, prop_name, text_prop in text_assocs:
            lines.append(f"  {et_name}.{prop_name} -> {text_prop}")

    return "\n".join(lines)
