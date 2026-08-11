"""Symbol and import extraction for non-Python languages, via tree-sitter.

tree-sitter is optional. If the grammar pack is unavailable the extractors return nothing
and the index simply holds fewer symbols — AgentGuard degrades to "I have no evidence
about this file", which is the correct behaviour. It must never mean "this symbol does not
exist", because that would manufacture false challenges (SPEC §14).
"""

from __future__ import annotations

import functools
import logging

from agentguard.repo.models import ImportRecord, SymbolRecord

log = logging.getLogger(__name__)

# node type -> symbol kind, per language.
_DEFINITION_NODES: dict[str, dict[str, str]] = {
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "method_definition": "method",
        "variable_declarator": "variable",
    },
    "typescript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "function_signature": "function",
        "method_definition": "method",
        "method_signature": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "variable_declarator": "variable",
        "public_field_definition": "property",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "mod_item": "module",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "method_declaration": "method",
        "enum_declaration": "enum",
    },
    "ruby": {
        "class": "class",
        "module": "module",
        "method": "method",
        "singleton_method": "method",
    },
}
_DEFINITION_NODES["tsx"] = _DEFINITION_NODES["typescript"]

# Node types that introduce a naming scope, so `Class.method` qualnames come out right.
_SCOPE_KINDS = {"class", "interface", "struct", "trait", "module", "enum"}

_IMPORT_NODES: dict[str, tuple[str, ...]] = {
    "javascript": ("import_statement",),
    "typescript": ("import_statement",),
    "tsx": ("import_statement",),
    "go": ("import_spec",),
    "rust": ("use_declaration",),
    "java": ("import_declaration",),
    "ruby": (),
}

_TS_GRAMMAR = {"tsx": "tsx", "typescript": "typescript"}


@functools.lru_cache(maxsize=16)
def _parser(language: str):
    """Cached per language; grammar loading is expensive and the daemon is long-lived."""
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None
    try:
        return get_parser(_TS_GRAMMAR.get(language, language))
    except Exception:  # noqa: BLE001 - unknown/unbuilt grammar
        log.debug("agentguard: no tree-sitter grammar for %s", language)
        return None


def available(language: str) -> bool:
    return language in _DEFINITION_NODES and _parser(language) is not None


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _name_of(node, source: bytes) -> str | None:
    field = node.child_by_field_name("name")
    if field is not None:
        return _text(field, source)
    # Ruby methods and a few others expose the name positionally.
    for child in node.children:
        if child.type in ("identifier", "constant", "property_identifier", "type_identifier"):
            return _text(child, source)
    return None


def extract(
    source_text: str, path: str, language: str
) -> tuple[list[SymbolRecord], list[ImportRecord], bool]:
    """Returns (symbols, imports, clean_parse).

    tree-sitter is error-tolerant: it returns a usable tree even for broken source. The
    symbols it finds are real and safe to record as positive evidence, but a tree
    containing ERROR nodes must not license the conclusion that something is *absent* —
    see the note in `symbols_python.extract`.
    """
    parser = _parser(language)
    definitions = _DEFINITION_NODES.get(language)
    if parser is None or definitions is None:
        return [], [], False

    source = source_text.encode("utf-8", "replace")
    try:
        tree = parser.parse(source)
    except Exception:  # noqa: BLE001 - malformed input must not break indexing
        return [], [], False

    symbols: list[SymbolRecord] = []
    imports: list[ImportRecord] = []
    import_types = _IMPORT_NODES.get(language, ())

    def walk(node, scope: str | None) -> None:
        next_scope = scope

        kind = definitions.get(node.type)
        if kind is not None:
            name = _name_of(node, source)
            if name:
                qualname = f"{scope}.{name}" if scope else name
                params = node.child_by_field_name("parameters")
                symbols.append(
                    SymbolRecord(
                        name=name,
                        qualname=qualname,
                        kind=kind,
                        path=path,
                        line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent=scope,
                        signature=_text(params, source)[:200] if params is not None else "",
                    )
                )
                if kind in _SCOPE_KINDS:
                    next_scope = qualname

        if node.type in import_types:
            record = _import_record(node, source, language)
            if record is not None:
                imports.append(record)

        for child in node.children:
            walk(child, next_scope)

    walk(tree.root_node, None)
    return symbols, imports, not tree.root_node.has_error


def _import_record(node, source: bytes, language: str) -> ImportRecord | None:
    module: str | None = None
    names: list[str] = []

    for child in node.walk_descendants() if hasattr(node, "walk_descendants") else _descend(node):
        if child.type in ("string", "string_literal", "interpreted_string_literal", "raw_string_literal"):
            module = _text(child, source).strip("\"'`")
        elif child.type in ("import_specifier", "named_imports"):
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                names.append(_text(name_node, source))
        elif child.type == "identifier" and language in ("javascript", "typescript", "tsx"):
            names.append(_text(child, source))

    if module is None and language in ("rust", "java"):
        module = _text(node, source).removeprefix("use ").removeprefix("import ").rstrip(";").strip()

    if not module:
        return None

    level = 0
    if module.startswith("./"):
        level = 1
    elif module.startswith("../"):
        level = module.count("../") + 1

    return ImportRecord(
        raw=module,
        names=sorted(set(names)),
        line=node.start_point[0] + 1,
        level=level,
    )


def _descend(node):
    stack = list(node.children)
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)
