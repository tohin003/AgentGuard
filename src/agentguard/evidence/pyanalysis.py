"""Extracting claims from Python source (SPEC §14).

The SPEC's worked example is a method call:

    Agent: "I'll use UserRepository.get_active_users()."
    -> No get_active_users() found -> Unsupported claim -> Challenge agent

Finding that is a dictionary lookup. The hard part — the part that decides whether this
tool is usable — is **not** flagging the thousand cases that look similar and are fine:

* the agent is *defining* `get_active_users` in this very edit
* `repo.get_active_users()` where `repo` is a parameter of unknown type
* `self.get_active_users()` inside a subclass that inherits it
* a class whose base class comes from a library we cannot see

So this module is deliberately timid. It only produces a claim when it can name the
receiver's type with confidence, and every unknown resolves to silence.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from agentguard.core.enums import ClaimKind
from agentguard.evidence.models import Claim


@dataclass(slots=True)
class SourceFacts:
    """Everything one Python source tells us, independent of any repository."""

    parsed: bool = False
    defined_names: set[str] = field(default_factory=set)
    # qualname -> attribute names, for classes defined in this source
    class_attributes: dict[str, set[str]] = field(default_factory=dict)
    class_bases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    imported_names: set[str] = field(default_factory=set)
    # `import os` binds a *module*: `os.path` is not a claim about a type.
    # `from x import Repo` binds something that might be a class, so `Repo.method` is.
    module_bindings: set[str] = field(default_factory=set)
    from_import_bindings: dict[str, str] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)


class _Analyzer(ast.NodeVisitor):
    """Walks a module, tracking just enough local type information to be useful.

    The type environment holds only bindings we are sure of:
      * ``x = ClassName(...)``       -> x : ClassName
      * ``x: ClassName = ...``       -> x : ClassName
      * ``def f(x: ClassName)``      -> x : ClassName
      * ``self`` inside ``class C``  -> self : C

    A name rebound to a different type, or to anything we cannot read, is *removed* from
    the environment rather than guessed at.
    """

    def __init__(self, path: str, source_lines: list[str]) -> None:
        self.path = path
        self.lines = source_lines
        self.facts = SourceFacts(parsed=True)
        self._types: dict[str, str] = {}
        self._ambiguous: set[str] = set()
        self._class_stack: list[str] = []
        # True only directly inside a class body. `X = 1` there is a class attribute;
        # the same statement inside one of its methods is a local variable.
        self._in_class_body = False

    # -- helpers ------------------------------------------------------------------

    def _snippet(self, node: ast.AST) -> str:
        line = getattr(node, "lineno", 0)
        if 1 <= line <= len(self.lines):
            return self.lines[line - 1].strip()[:160]
        return ""

    def _bind(self, name: str, type_name: str | None) -> None:
        if type_name is None:
            # Unknown assignment: the name no longer means what it meant.
            self._types.pop(name, None)
            self._ambiguous.add(name)
            return
        existing = self._types.get(name)
        if existing is not None and existing != type_name:
            self._types.pop(name, None)
            self._ambiguous.add(name)
            return
        if name not in self._ambiguous:
            self._types[name] = type_name

    # Generic wrappers whose parameter *is* the value's type. `Optional[Foo]` is a Foo;
    # `list[Foo]` is emphatically not — it is a list, and `claims.append(...)` on a
    # `list[Claim]` must never be read as a claim about `Claim.append`. Getting this
    # wrong produced a steady stream of confident nonsense against real code.
    _TRANSPARENT_GENERICS = frozenset(
        {"Optional", "Final", "ClassVar", "Annotated", "Required", "NotRequired"}
    )

    @staticmethod
    def _annotation_name(node: ast.expr | None) -> str | None:
        """`Foo`, `foo.Foo`, `Optional[Foo]` -> "Foo". Containers and anything else -> None."""
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A string annotation may be any expression; only a bare name is safe.
            text = node.value.strip()
            return text if text.isidentifier() else None
        if isinstance(node, ast.Subscript):
            wrapper = _Analyzer._annotation_name(node.value)
            if wrapper not in _Analyzer._TRANSPARENT_GENERICS:
                return None
            inner = node.slice
            if isinstance(inner, ast.Tuple):
                # Optional[X] is Union[X, None]; Annotated[X, ...] keeps the first arg.
                inner = inner.elts[0] if inner.elts else None
            if isinstance(inner, ast.Name | ast.Attribute):
                return _Analyzer._annotation_name(inner)
        return None

    @staticmethod
    def _constructor_name(node: ast.expr) -> str | None:
        """`ClassName(...)` -> "ClassName". Not `module.ClassName(...)`, which could be
        anything, and not a call on a call."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id
        return None

    # -- visitors -----------------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = ".".join([*self._class_stack, node.name])
        self.facts.defined_names.add(node.name)
        self.facts.class_attributes.setdefault(qualname, set())
        bases: list[str] = []
        for base in node.bases:
            name = self._annotation_name(base)
            bases.append(name if name else "<unresolvable>")
        self.facts.class_bases[qualname] = tuple(bases)

        self._class_stack.append(node.name)
        outer_types = dict(self._types)
        outer_in_body = self._in_class_body
        self._in_class_body = True
        self._types["self"] = qualname
        self._types["cls"] = qualname
        for child in node.body:
            self.visit(child)
        self._types = outer_types
        self._in_class_body = outer_in_body
        self._class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.facts.defined_names.add(node.name)
        if self._class_stack:
            owner = ".".join(self._class_stack)
            self.facts.class_attributes.setdefault(owner, set()).add(node.name)

        outer_types = dict(self._types)
        outer_ambiguous = set(self._ambiguous)
        outer_in_body = self._in_class_body
        self._in_class_body = False
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            self.facts.defined_names.add(arg.arg)
            annotated = self._annotation_name(arg.annotation)
            if annotated:
                self._types[arg.arg] = annotated
            else:
                # An unannotated parameter could be anything. Silence, not guesswork.
                self._types.pop(arg.arg, None)
                self._ambiguous.add(arg.arg)
        for arg in (args.vararg, args.kwarg):
            if arg is not None:
                self.facts.defined_names.add(arg.arg)
                self._ambiguous.add(arg.arg)

        for child in node.body:
            self.visit(child)
        self._types = outer_types
        self._ambiguous = outer_ambiguous
        self._in_class_body = outer_in_body

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Assign(self, node: ast.Assign) -> None:
        type_name = self._constructor_name(node.value)
        in_class_body = bool(self._class_stack) and self._in_class_body
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.facts.defined_names.add(target.id)
                self._bind(target.id, type_name)
                if in_class_body:
                    # `RENAME = "rename"` inside a class is a class attribute.
                    owner = ".".join(self._class_stack)
                    self.facts.class_attributes.setdefault(owner, set()).add(target.id)
            elif isinstance(target, ast.Attribute) and self._class_stack:
                # `self.x = ...` is an attribute of the enclosing class.
                if isinstance(target.value, ast.Name) and target.value.id == "self":
                    owner = ".".join(self._class_stack)
                    self.facts.class_attributes.setdefault(owner, set()).add(target.attr)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        annotated = self._annotation_name(node.annotation)
        if isinstance(node.target, ast.Name):
            self.facts.defined_names.add(node.target.id)
            self._bind(node.target.id, annotated)
            if self._class_stack and self._in_class_body:
                # `subject: str` inside a dataclass declares a field.
                owner = ".".join(self._class_stack)
                self.facts.class_attributes.setdefault(owner, set()).add(node.target.id)
        elif isinstance(node.target, ast.Attribute) and self._class_stack:
            if isinstance(node.target.value, ast.Name) and node.target.value.id == "self":
                owner = ".".join(self._class_stack)
                self.facts.class_attributes.setdefault(owner, set()).add(node.target.attr)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        # Loop variables have element types we cannot infer.
        for name in _target_names(node.target):
            self.facts.defined_names.add(name)
            self._types.pop(name, None)
            self._ambiguous.add(name)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                for name in _target_names(item.optional_vars):
                    self.facts.defined_names.add(name)
                    self._ambiguous.add(name)
                    self._types.pop(name, None)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = (alias.asname or alias.name).split(".")[0]
            self.facts.defined_names.add(bound)
            self.facts.imported_names.add(bound)
            self.facts.module_bindings.add(bound)
            self.facts.claims.append(
                Claim(
                    kind=ClaimKind.MODULE_IMPORTABLE,
                    subject=alias.name,
                    path=self.path,
                    line=node.lineno,
                    snippet=self._snippet(node),
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            bound = alias.asname or alias.name
            self.facts.defined_names.add(bound)
            self.facts.imported_names.add(bound)
            if alias.name != "*":
                self.facts.from_import_bindings[bound] = module
            # A relative import's module is resolved against the file's package, which
            # the resolver does; here we only record the claim.
            if alias.name != "*":
                self.facts.claims.append(
                    Claim(
                        kind=ClaimKind.SYMBOL_EXISTS,
                        subject=alias.name,
                        owner=module,
                        path=self.path,
                        line=node.lineno,
                        snippet=self._snippet(node),
                    )
                )
        if module and node.level == 0:
            self.facts.claims.append(
                Claim(
                    kind=ClaimKind.MODULE_IMPORTABLE,
                    subject=module,
                    path=self.path,
                    line=node.lineno,
                    snippet=self._snippet(node),
                )
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        receiver = node.value
        owner: str | None = None

        if isinstance(receiver, ast.Name):
            name = receiver.id
            if name in self.facts.module_bindings:
                owner = None  # `os.path` — a module attribute, not a type's
            elif name in self._ambiguous:
                owner = None  # rebound, or a parameter of unknown type
            elif name in self._types:
                owner = self._types[name]  # inferred: `repo = UserRepository(...)`
            elif name in self.facts.class_attributes:
                owner = name  # direct access on a class defined in this file
            elif name in self.facts.from_import_bindings:
                owner = name  # `from x import Repo` — the resolver checks if it is a class
            elif name not in self.facts.defined_names:
                owner = name  # unbound global; the resolver decides whether it knows it

        elif isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name):
            # `UserRepository(session).get_by_id(...)` — the type of a constructor call
            # is the class. Safe because the resolver proceeds only when the name
            # resolves to an actual class; a factory *function* of the same shape does
            # not match and is therefore ignored.
            owner = receiver.func.id

        if owner and not owner.startswith("<"):
            self.facts.claims.append(
                Claim(
                    kind=ClaimKind.ATTRIBUTE_ON_TYPE,
                    subject=f"{owner}.{node.attr}",
                    owner=owner,
                    path=self.path,
                    line=node.lineno,
                    snippet=self._snippet(node),
                )
            )

        self.generic_visit(node)


def _target_names(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple | ast.List):
        return [name for element in node.elts for name in _target_names(element)]
    return []


def analyze(source: str, path: str = "") -> SourceFacts:
    """Never raises. Unparsable source yields `parsed=False` and no claims."""
    try:
        tree = ast.parse(source, filename=path or "<edit>")
    except (SyntaxError, ValueError, RecursionError):
        return SourceFacts(parsed=False)

    analyzer = _Analyzer(path, source.splitlines())
    try:
        analyzer.visit(tree)
    except (RecursionError, AttributeError):
        return SourceFacts(parsed=False)
    return analyzer.facts
