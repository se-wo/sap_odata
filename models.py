from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class Property:
    name: str
    type: str
    nullable: bool = True
    max_length: int | None = None
    label: str = ""
    filterable: bool = True
    sortable: bool = True
    creatable: bool = True
    updatable: bool = True
    # Vocabulary-annotation-related sap: attributes
    text: str = ""
    quickinfo: str = ""
    value_list: str = ""
    unit: str = ""
    semantics: str = ""
    display_format: str = ""


@dataclass
class NavigationProperty:
    name: str
    relationship: str
    from_role: str
    to_role: str


@dataclass
class EntityType:
    name: str
    properties: list[Property] = field(default_factory=list)
    key_properties: list[str] = field(default_factory=list)
    navigation_properties: list[NavigationProperty] = field(default_factory=list)


@dataclass
class AssociationEnd:
    role: str
    entity_type: str
    multiplicity: str


@dataclass
class Association:
    name: str
    end1: AssociationEnd | None = None
    end2: AssociationEnd | None = None


@dataclass
class EntitySet:
    name: str
    entity_type: str
    addressable: bool = True
    creatable: bool = True
    updatable: bool = True
    deletable: bool = True


@dataclass
class FunctionImport:
    name: str
    http_method: str = "GET"
    return_type: str = ""
    parameters: list[Property] = field(default_factory=list)


@dataclass
class ValueListParameter:
    type: str  # InOut, In, Out, DisplayOnly, Constant, FilterOnly
    local_property: str
    value_list_property: str


@dataclass
class ValueListInfo:
    label: str = ""
    collection_path: str = ""
    search_supported: bool = False
    parameters: list[ValueListParameter] = field(default_factory=list)


@dataclass
class Annotation:
    term: str
    qualifier: str = ""
    value: Any = None


@dataclass
class ServiceMetadata:
    schema_namespace: str = ""
    entity_types: list[EntityType] = field(default_factory=list)
    associations: list[Association] = field(default_factory=list)
    entity_sets: list[EntitySet] = field(default_factory=list)
    function_imports: list[FunctionImport] = field(default_factory=list)
    annotations: dict[str, list[Annotation]] = field(default_factory=dict)
    value_lists: dict[str, ValueListInfo] = field(default_factory=dict)


@dataclass
class ServiceInfo:
    technical_name: str
    version: str
    title: str
    url: str = ""

    @property
    def service_path(self) -> str:
        """Extract the path portion from the full service URL."""
        if self.url:
            return urlparse(self.url).path
        return f"/sap/opu/odata/sap/{self.technical_name}"
