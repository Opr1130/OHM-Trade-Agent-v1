"""Shared durable persistence primitives for O'Pip.

One archive implementation is reused across sequences so that durability
semantics cannot drift between evidence families.
"""
