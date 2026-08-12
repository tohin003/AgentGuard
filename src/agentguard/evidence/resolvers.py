"""Claim -> verdict, against the repository (SPEC §14, §15, §16).

    Claim -> Evidence lookup -> FOUND: validate / NOT FOUND: unverified

Every resolver here follows the same discipline, and it is worth stating once:

    A claim is only ever reported as CONTRADICTED when we hold **complete** evidence
    about the thing it concerns.

For a method call that means knowing the receiver's type, having a clean parse of the
file defining it, and having resolved every one of its base classes. If any link is
missing the answer is silence, not suspicion. A class inheriting from `pydantic.BaseModel`
has dozens of methods AgentGuard cannot see; challenging a call to one of them would be
confidently wrong, and confidently wrong is worse than quiet.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentguard.core.enums import ClaimKind, FailureMode, Severity, Verdict
from agentguard.core.models import EvidenceRef
from agentguard.evidence.models import Claim, Resolution
from agentguard.evidence.pyanalysis import SourceFacts
from agentguard.repo.index import RepoIndex

STDLIB_MODULES: frozenset[str] = frozenset(sys.stdlib_module_names) | {"typing_extensions"}

# Import name -> distribution name, where they differ. The main safety net for undeclared
# imports is "is it imported anywhere else in this repo", but these are common enough to
# be worth naming.
IMPORT_TO_DISTRIBUTION: dict[str, str] = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "attr": "attrs",
    "google": "google-api-python-client",
    "OpenSSL": "pyopenssl",
    "serial": "pyserial",
    "usb": "pyusb",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "fitz": "pymupdf",
    "magic": "python-magic",
    "redis": "redis",
    "psycopg2": "psycopg2-binary",
    "MySQLdb": "mysqlclient",
    "zoneinfo": "backports.zoneinfo",
    "pkg_resources": "setuptools",
    "git": "gitpython",
    "grpc": "grpcio",
    "jose": "python-jose",
    "multipart": "python-multipart",
}

# Bases that add nothing an agent could call by mistake.
_TRANSPARENT_BASES: frozenset[str] = frozenset(
    {"object", "Protocol", "ABC", "ABCMeta", "Generic", "Enum", "StrEnum", "IntEnum",
     "IntFlag", "Flag", "ReprEnum", "NamedTuple", "TypedDict"}
)
_ENUM_BASES: frozenset[str] = frozenset(
    {"Enum", "StrEnum", "IntEnum", "IntFlag", "Flag", "ReprEnum"}
)
# Value types mixed into an enum. Ignored only when an Enum base is also present.
_VALUE_MIXINS: frozenset[str] = frozenset({"str", "int", "float", "bytes", "tuple"})

# Framework base classes whose subclasses declare their real attributes in their own body
# (ORM columns, pydantic fields), but whose own definition is generated and invisible from
# source. Treating them as opaque made AgentGuard silent on every model in a real
# codebase — 138 of 160 remaining misses were SQLAlchemy models.
#
# The trade is explicit: to see through one of these, we must assert what it contributes,
# and anything missed from that list becomes a *false positive*. So each base is only
# treated as transparent when the defining file actually imports that framework, and the
# API surface below is the price of admission. Precision is the metric worth protecting;
# if these lists are wrong the benchmark will say so.
_FRAMEWORK_BASES: dict[str, tuple[str, ...]] = {
    "Base": ("sqlalchemy",),
    "DeclarativeBase": ("sqlalchemy",),
    "Model": ("sqlalchemy", "flask_sqlalchemy", "django"),
    "BaseModel": ("pydantic",),
    "BaseSettings": ("pydantic",),
    "Document": ("mongoengine", "beanie"),
}

_FRAMEWORK_API: frozenset[str] = frozenset(
    {
        # SQLAlchemy declarative
        "metadata", "registry", "query", "__table__", "__tablename__", "__mapper__",
        "__table_args__", "_sa_instance_state", "_sa_class_manager", "c", "columns",
        # pydantic v2 and v1
        "model_dump", "model_dump_json", "model_validate", "model_validate_json",
        "model_copy", "model_construct", "model_json_schema", "model_rebuild",
        "model_fields_set", "model_extra", "model_post_init",
        "dict", "json", "copy", "parse_obj", "parse_raw", "parse_file", "from_orm",
        "schema", "schema_json", "construct", "validate", "update_forward_refs",
        "Config", "fields", "__fields__", "__config__",
        # Django-ish
        "objects", "save", "delete", "pk", "DoesNotExist", "Meta",
    }
)

_ALWAYS_PRESENT_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__class__", "__dict__", "__doc__", "__module__", "__name__", "__qualname__",
        "__init__", "__new__", "__repr__", "__str__", "__eq__", "__hash__",
        "__enter__", "__exit__", "__iter__", "__next__", "__len__", "__call__",
        "__getattr__", "__setattr__", "__getitem__", "__setitem__", "__contains__",
        "mro", "model_config", "model_fields",
        # Enum members carry these regardless of what the enum mixes in.
        "name", "value", "_value_", "_name_", "_member_names_", "_member_map_",
    }
)


class Resolver:
    """Resolves claims against a repository index plus the file being edited."""

    def __init__(self, index: RepoIndex, local: SourceFacts | None = None, edited_path: str = "") -> None:
        self.index = index
        self.local = local
        self.edited_path = edited_path

    # -- entry point --------------------------------------------------------------

    def resolve(self, claim: Claim) -> Resolution | None:
        """`None` means "no opinion" — the overwhelmingly common answer."""
        if not self.index.is_built:
            return None
        handler = {
            ClaimKind.ATTRIBUTE_ON_TYPE: self._attribute,
            ClaimKind.SYMBOL_EXISTS: self._imported_symbol,
            ClaimKind.MODULE_IMPORTABLE: self._module,
            ClaimKind.DEPENDENCY_DECLARED: self._dependency,
        }.get(claim.kind)
        return handler(claim) if handler else None

    # -- attribute on a type ------------------------------------------------------

    def _attribute(self, claim: Claim) -> Resolution | None:
        owner, _, attribute = claim.subject.rpartition(".")
        if not owner or not attribute or attribute in _ALWAYS_PRESENT_ATTRIBUTES:
            return None
        if attribute.startswith("__") and attribute.endswith("__"):
            return None

        known = self._known_attributes(owner)
        if known is None:
            known = self._duplicates(owner)
        if known is None:
            return None  # we do not confidently know this type — say nothing

        attributes, definition = known
        if attribute in attributes:
            return Resolution(
                claim=claim,
                verdict=Verdict.SUPPORTED,
                failure_mode=FailureMode.NOT_A_FAILURE,
                severity=Severity.INFO,
                summary=f"{claim.subject} exists",
                evidence=[definition] if definition else [],
            )

        visible = sorted(name for name in attributes if not name.startswith("_"))
        return Resolution(
            claim=claim,
            verdict=Verdict.CONTRADICTED,
            # SPEC §3, "hallucinate APIs": a member on a type we fully understand.
            failure_mode=FailureMode.HALLUCINATED_API,
            severity=Severity.HIGH,
            summary=f"{claim.subject} does not exist",
            detail=(
                f"`{owner}` is defined in this repository and has no member "
                f"`{attribute}`."
            ),
            evidence=[definition] if definition else [],
            alternatives=visible[:12],
        )

    def _attributes_of_record(self, record) -> set[str] | None:
        """Attributes of one specific class record, following its bases.

        Keyed on the record rather than the name, so a duplicated class name cannot send
        the lookup back through the ambiguous path — which is what made the first attempt
        at duplicate handling silently do nothing.
        """
        if not self.index.is_parsed(record.path) or not record.bases_known:
            return None

        attributes = set(self.index.attributes_of(record.qualname))
        bases = [b.split("[")[0].split(".")[-1].strip() for b in record.bases]
        is_enum = any(b in _ENUM_BASES for b in bases)

        for bare in bases:
            if not bare or bare in _TRANSPARENT_BASES:
                continue
            if is_enum and bare in _VALUE_MIXINS:
                continue
            if self._is_framework_base(bare, record.path):
                attributes |= _FRAMEWORK_API
                continue
            parent = self._index_class(bare)
            if parent is None:
                return None
            inherited = self._attributes_of_record(parent)
            if inherited is None:
                return None
            attributes |= inherited
        return attributes

    def _known_attributes(self, owner: str) -> tuple[set[str], EvidenceRef | None] | None:
        """Every attribute of `owner`, or None if we cannot be sure of all of them.

        Returning None is the safe answer and this function returns it often: for types
        defined outside the repository, for types whose file did not parse cleanly, and
        for any type with a base class we cannot resolve.
        """
        attributes: set[str] = set()
        definition: EvidenceRef | None = None
        pending = [owner]
        seen: set[str] = set()

        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)

            local_hit = self._local_class(current)
            if local_hit is not None:
                own, bases = local_hit
                attributes |= own
                if definition is None:
                    definition = EvidenceRef(
                        source="ast",
                        path=self.edited_path,
                        symbol=current,
                        note="defined in this edit",
                    )
                # The same base filtering as the index path below. Without it a class
                # defined *in the file being edited* — `class Mode(str, Enum)` — queued
                # `str`, failed to resolve it, and bailed. Every enum declared in the file
                # under edit was invisible, which is most of them.
                is_enum = any(b in _ENUM_BASES for b in bases)
                for base in bases:
                    if base.startswith("<"):
                        return None  # unreadable base class
                    if base in _TRANSPARENT_BASES or (is_enum and base in _VALUE_MIXINS):
                        continue
                    if self._is_framework_base(base, self.edited_path):
                        attributes |= _FRAMEWORK_API
                        continue
                    pending.append(base)
                continue

            record = self._index_class(current)
            if record is None:
                return None  # not a type we know about

            if not self.index.is_parsed(record.path):
                return None  # incomplete evidence about the defining file
            if not record.bases_known:
                return None  # this extractor does not report inheritance

            attributes |= self.index.attributes_of(record.qualname)
            if definition is None:
                definition = EvidenceRef(
                    source="ast",
                    path=record.path,
                    line=record.line,
                    symbol=record.qualname,
                    note=record.kind,
                )
            bases = [b.split("[")[0].split(".")[-1].strip() for b in record.bases]
            # An enum's members are entirely in its own body, so a value-type mixin does
            # not hide anything — `class Mode(str, Enum)` is as knowable as `class Mode(Enum)`.
            # Bailing on the unresolvable `str` was costing 159 of 160 detections on a real
            # codebase: enums are everywhere, and AgentGuard was silent on all of them.
            # The mixin is only ignored *alongside* an Enum base, so an ordinary class
            # inheriting `str` still counts as unknown and stays silent.
            is_enum = any(b in _ENUM_BASES for b in bases)
            for bare in bases:
                if not bare or bare in _TRANSPARENT_BASES:
                    continue
                if is_enum and bare in _VALUE_MIXINS:
                    continue
                if self._is_framework_base(bare, record.path):
                    attributes |= _FRAMEWORK_API
                    continue
                pending.append(bare)

        return (attributes, definition) if attributes or definition else None

    def _is_framework_base(self, base: str, path: str) -> bool:
        """A generated base we can see through, but only where the framework is present.

        `Base` is a common enough name that recognising it unconditionally would be
        guessing. Requiring the defining file to import the framework keeps it evidence-led.
        """
        frameworks = _FRAMEWORK_BASES.get(base)
        if not frameworks:
            return False
        imported = " ".join(record.raw for record in self.index.imports_of(path))
        return any(framework in imported for framework in frameworks)

    def _duplicates(self, owner: str) -> tuple[set[str], EvidenceRef | None] | None:
        """A name declared more than once — routine in a monorepo where several services
        each define their own `Message` or `GradingMode`.

        The ambiguity is irrelevant when *every* copy lacks the attribute: whichever one
        was meant, the member is absent. If any copy has it, stay silent, because then the
        answer really does depend on which is which.
        """
        bare = owner.rsplit(".", 1)[-1]
        candidates = [
            symbol for symbol in self.index.find_symbol(bare)
            if symbol.kind in ("class", "struct", "interface", "enum")
        ]
        if len(candidates) < 2:
            return None

        union: set[str] = set()
        for record in candidates:
            attributes = self._attributes_of_record(record)
            if attributes is None:
                return None  # one copy is unreadable, so the union proves nothing
            union |= attributes
        first = candidates[0]
        return union, EvidenceRef(source="ast", path=first.path, line=first.line,
                                  symbol=first.qualname,
                                  note=f"{len(candidates)} definitions of this name")

    def _local_class(self, name: str) -> tuple[set[str], tuple[str, ...]] | None:
        """A class defined in the file currently being edited."""
        if self.local is None:
            return None
        for qualname, own in self.local.class_attributes.items():
            if qualname == name or qualname.rsplit(".", 1)[-1] == name.rsplit(".", 1)[-1]:
                return own, self.local.class_bases.get(qualname, ())
        return None

    def _index_class(self, name: str):
        bare = name.rsplit(".", 1)[-1]
        candidates = [
            symbol
            for symbol in self.index.find_symbol(bare)
            if symbol.kind in ("class", "struct", "interface", "enum")
        ]
        # An ambiguous name (two classes, same name, different files) is not something we
        # can reason about safely.
        return candidates[0] if len(candidates) == 1 else None

    # -- `from module import name` ------------------------------------------------

    def _imported_symbol(self, claim: Claim) -> Resolution | None:
        module = claim.owner
        if not module:
            return None

        target = self.index.resolve_module(module)
        if target is None or not self.index.is_parsed(target):
            return None  # external, or incompletely understood

        if claim.subject in self._exported_names(target):
            return Resolution(
                claim=claim,
                verdict=Verdict.SUPPORTED,
                failure_mode=FailureMode.NOT_A_FAILURE,
                severity=Severity.INFO,
                summary=f"{module}.{claim.subject} exists",
                evidence=[EvidenceRef(source="ast", path=target, symbol=claim.subject)],
            )

        # A submodule of the package is a legitimate import too.
        if self.index.resolve_module(f"{module}.{claim.subject}") is not None:
            return None

        available = sorted(
            name for name in self._exported_names(target) if not name.startswith("_")
        )
        return Resolution(
            claim=claim,
            verdict=Verdict.CONTRADICTED,
            # SPEC §3, "invent functions": a name imported from a module of ours that
            # neither defines nor re-exports it.
            failure_mode=FailureMode.INVENTED_FUNCTION,
            severity=Severity.HIGH,
            summary=f"`{claim.subject}` is not defined in `{module}`",
            detail=f"{target} does not define or re-export `{claim.subject}`.",
            evidence=[EvidenceRef(source="ast", path=target, note="module contents")],
            alternatives=available[:12],
        )

    def _exported_names(self, path: str) -> set[str]:
        """Names obtainable from a module: what it defines plus what it re-exports.

        Re-exports matter. A package `__init__.py` that does `from .models import User`
        defines no symbol called `User`, but `from shop import User` is perfectly valid,
        and treating it as a hallucination would be a glaring false positive.
        """
        names = {symbol.name for symbol in self.index.symbols_in(path) if symbol.parent is None}
        for record in self.index.imports_of(path):
            names.update(record.names)
            if record.alias:
                names.add(record.alias)
        return names

    # -- module importability -----------------------------------------------------

    def _module(self, claim: Claim) -> Resolution | None:
        module = claim.subject
        root = module.split(".")[0]

        if root in STDLIB_MODULES:
            return None
        if self.index.resolve_module(module) is not None:
            return None
        if self.local is not None and root in self.local.defined_names and root != module:
            return None

        if self._is_declared(root):
            return None

        # Strongest safety net: if any other file in this repository already imports it,
        # it evidently works here, whatever the manifests say.
        elsewhere = self._imported_elsewhere(root)
        if elsewhere:
            return None

        # Without a manifest we have no basis for believing dependencies are declared
        # anywhere, so "not declared" carries no information.
        if not self.index.dependencies.manifests:
            return None

        # A module that shares a top-level package with this repository but does not
        # resolve is a genuinely strong signal — the agent is importing from a part of
        # *this* codebase that does not exist.
        internal_looking = any(
            path.split("/")[0] == root or f"/{root}/" in f"/{path}" for path in self.index.files
        )

        # An undeclared *third-party* import is a different matter, and the Phase 3 audit
        # against real repositories showed why: transitive dependencies (`botocore` behind
        # `boto3`), per-service manifests in a monorepo, and script-only imports all look
        # identical to an invented library from here. Without solving "which environment
        # will actually run this file", the signal is too weak to interrupt anyone over —
        # so it is recorded at LOW and surfaces in `agentguard log`, never as a challenge.
        severity = Severity.HIGH if internal_looking else Severity.LOW

        return Resolution(
            claim=claim,
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            # Two different SPEC §3 failures wear the same shape here, and the split
            # follows the evidence rather than a guess. A module under one of *our*
            # top-level packages that does not resolve is a file that is not there
            # ("hallucinate files"); anything else is a library we cannot account for
            # ("invent libraries").
            #
            # The second is knowingly imprecise, and it is why that branch is LOW and
            # never challenged: from inside the repository, an invented library and a
            # real-but-undeclared one (a transitive dependency, a per-service manifest)
            # are indistinguishable without knowing which environment will run the file.
            failure_mode=(
                FailureMode.HALLUCINATED_FILE if internal_looking else FailureMode.INVENTED_LIBRARY
            ),
            severity=severity,
            summary=f"`{module}` could not be verified",
            detail=(
                f"`{root}` is not in the standard library, is not declared in "
                f"{', '.join(self.index.dependencies.manifests)}, and is not imported "
                f"anywhere else in this repository."
            ),
            evidence=[
                EvidenceRef(
                    source="manifest",
                    path=self.index.dependencies.manifests[0],
                    note="declared dependencies",
                )
            ],
        )

    def _is_declared(self, root: str) -> bool:
        if self.index.is_declared_dependency(root):
            return True
        distribution = IMPORT_TO_DISTRIBUTION.get(root)
        return bool(distribution and self.index.is_declared_dependency(distribution))

    def _imported_elsewhere(self, root: str) -> str | None:
        for path, parsed in self.index.parsed.items():
            if path == self.edited_path:
                continue
            for record in parsed.imports:
                if record.raw.split(".")[0] == root:
                    return path
        return None

    # -- new dependency -----------------------------------------------------------

    def _dependency(self, claim: Claim) -> Resolution | None:
        """SPEC §16's DEPENDENCY challenge: "Is a new dependency actually necessary?" """
        if self.index.is_declared_dependency(claim.subject):
            return None

        return Resolution(
            claim=claim,
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            failure_mode=FailureMode.UNNECESSARY_DEPENDENCY,
            severity=Severity.MEDIUM,
            summary=f"`{claim.subject}` is a new dependency",
            detail=(
                "It is not declared in "
                f"{', '.join(self.index.dependencies.manifests) or 'any manifest'}. "
                "New dependencies are permanent, so the reason should be explicit."
            ),
            evidence=[
                EvidenceRef(source="manifest", path=manifest)
                for manifest in self.index.dependencies.manifests[:2]
            ],
        )


def resolve_all(
    claims: list[Claim],
    index: RepoIndex,
    local: SourceFacts | None = None,
    edited_path: str = "",
) -> list[Resolution]:
    resolver = Resolver(index, local, edited_path)
    out: list[Resolution] = []
    for claim in claims:
        try:
            resolution = resolver.resolve(claim)
        except Exception:  # noqa: BLE001 - a broken resolver must not block the agent
            continue
        if resolution is not None:
            out.append(resolution)
    return out


def workspace_root(index: RepoIndex) -> Path:
    return index.root
