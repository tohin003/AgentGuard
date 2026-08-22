"""Path-boundary helpers for repository evidence and mutation handling.

Hook payloads are host-provided data.  A path in one is not automatically a path in the
repository whose ``cwd`` arrived with the payload.  This module provides the one canonical
conversion used by the index and evidence engine so absolute paths, ``..`` traversal, and
symlinks pointing outside the workspace cannot turn an evidence lookup into an arbitrary
filesystem read.
"""

from __future__ import annotations

from pathlib import Path


def relative_path(root: Path, raw: str | Path) -> str | None:
    """Return a canonical repo-relative POSIX path, or ``None`` if it escapes ``root``.

    ``Path.resolve(strict=False)`` canonicalises existing symlink components while still
    allowing a path for a file that is about to be created.  Containment is checked after
    that canonicalisation, so a symlink such as ``repo/out -> /tmp`` cannot be used to
    read or index ``/tmp/secret.py``.  Callers still need normal filesystem error handling
    because the path may disappear or change between this check and the subsequent read.
    """

    if not isinstance(raw, (str, Path)):
        return None
    try:
        base = Path(root).expanduser().resolve(strict=False)
        candidate = Path(raw).expanduser()
        # Relative paths are interpreted exactly as paths inside the workspace.  Absolute
        # paths are accepted only when their canonical target is inside that workspace.
        absolute = base / candidate if not candidate.is_absolute() else candidate
        canonical = absolute.resolve(strict=False)
        canonical_relative = canonical.relative_to(base)

        # Preserve a safe path's repository spelling.  The scanner stores paths exactly
        # as they appear in the tree, so canonicalising an internal symlink to its target
        # here would make ``normalize("link.py")`` disagree with the ``link.py`` index
        # key.  Paths containing ``..`` are the exception: their operating-system
        # resolution can depend on a preceding symlink, so use the canonical spelling.
        try:
            lexical_relative = absolute.relative_to(base)
        except ValueError:
            lexical_relative = canonical_relative
        relative = (
            canonical_relative
            if any(part in ("", ".", "..") for part in lexical_relative.parts)
            else lexical_relative
        )
    except (OSError, RuntimeError, ValueError):
        # RuntimeError covers symlink loops; ValueError covers non-relative paths on
        # platforms with different drives.  Both mean "no evidence", never a filesystem
        # access outside the guarded repository.
        return None

    rendered = relative.as_posix()
    # ``relative_to`` returns ``.`` for the root itself.  A root is not a file path and
    # accepting it would make refresh/read callers inspect the directory unexpectedly.
    return None if rendered in ("", ".") else rendered
