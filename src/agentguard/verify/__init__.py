"""Verification Engine and Completion Gate (SPEC §19, §20).

Deliberately free of eager imports: `core.taskstate` depends on `verify.runners`, so a
package-level import of `completion_gate` here would close a cycle.
"""
