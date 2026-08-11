"""Python symbol and import extraction via the stdlib `ast` module.

Python gets its own extractor rather than going through tree-sitter because `ast` is
already installed, faster, and exact — it is the same parser the interpreter uses, so
there is no grammar-version drift between what AgentGuard believes and what Python does.
"""

from __future__ import annotations

import ast

from agentguard.repo.models import ImportRecord, SymbolRecord

_MAX_SIGNATURE = 200


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:  # noqa: BLE001 - unparse can fail on exotic trees
        args = "..."
    returns = ""
    if node.returns is not None:
        try:
            returns = f" -> {ast.unparse(node.returns)}"
        except Exception:  # noqa: BLE001
            returns = ""
    return f"({args}){returns}"[:_MAX_SIGNATURE]


def _base_names(node: ast.ClassDef) -> str:
    names = []
    for base in node.bases:
        try:
            names.append(ast.unparse(base))
        except Exception:  # noqa: BLE001
            continue
    return f"({', '.join(names)})" if names else ""


def extract(source: str, path: str) -> tuple[list[SymbolRecord], list[ImportRecord], bool]:
    """Returns (symbols, imports, clean_parse).

    `clean_parse` is not cosmetic. "Parsed cleanly and found no symbols" and "could not
    parse at all" look identical in the symbol lists but mean opposite things: only the
    first licenses the conclusion that a symbol is *absent*. Conflating them would make
    every syntax error a source of false hallucination challenges (SPEC §14).

    Unparsable source is routine — the agent is often mid-edit — so this never raises.
    """
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError):
        return [], [], False

    symbols: list[SymbolRecord] = []
    imports: list[ImportRecord] = []

    def visit(node: ast.AST, parent: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qualname = f"{parent}.{child.name}" if parent else child.name
                symbols.append(
                    SymbolRecord(
                        name=child.name,
                        qualname=qualname,
                        kind="class",
                        path=path,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        parent=parent,
                        signature=_base_names(child),
                    )
                )
                visit(child, qualname)

            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{parent}.{child.name}" if parent else child.name
                symbols.append(
                    SymbolRecord(
                        name=child.name,
                        qualname=qualname,
                        kind="method" if parent else "function",
                        path=path,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        parent=parent,
                        signature=_signature(child),
                    )
                )
                # Nested defs are indexed too — a closure is still a real definition.
                visit(child, qualname)

            elif isinstance(child, ast.Assign | ast.AnnAssign):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        qualname = f"{parent}.{target.id}" if parent else target.id
                        symbols.append(
                            SymbolRecord(
                                name=target.id,
                                qualname=qualname,
                                kind="constant" if target.id.isupper() else "variable",
                                path=path,
                                line=child.lineno,
                                end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                                parent=parent,
                            )
                        )

            elif isinstance(child, ast.Import):
                for alias in child.names:
                    imports.append(
                        ImportRecord(
                            raw=alias.name,
                            names=[alias.name.split(".")[0]],
                            alias=alias.asname,
                            line=child.lineno,
                        )
                    )

            elif isinstance(child, ast.ImportFrom):
                imports.append(
                    ImportRecord(
                        raw=child.module or "",
                        names=[a.name for a in child.names],
                        alias=None,
                        line=child.lineno,
                        level=child.level or 0,
                    )
                )

            else:
                # Descend through if/try/with so conditionally-defined symbols are found.
                if isinstance(child, ast.If | ast.Try | ast.With | ast.AsyncWith | ast.For):
                    visit(child, parent)

    visit(tree, None)
    return symbols, imports, True


def extract_referenced_attributes(source: str) -> set[tuple[str, str]]:
    """Find `Something.method` references — the shape of a SPEC §14 claim.

    Returns (receiver, attribute) pairs, e.g. ("UserRepository", "get_active_users") from
    `UserRepository.get_active_users()`. Used by the Evidence Engine in Phase 3 to spot
    calls to methods that do not exist.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()

    out: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            out.add((node.value.id, node.attr))
    return out


def defined_names(source: str) -> set[str]:
    """Every name this source *defines* or binds locally.

    The Evidence Engine must never flag a symbol the agent is creating in the very edit
    being checked — that false positive would make AgentGuard unusable (SPEC §14).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names
