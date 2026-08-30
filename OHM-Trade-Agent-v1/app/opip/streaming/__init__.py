"""O'Pip Sequence 4 — Real-Time Market Intelligence (BUILD 4.1).

This package contains deterministic contracts and pure feature math only.
BUILD 4.1 has no network access, no persistent worker, and no exchange
execution authority. It is not consumed by the Sequence 3 Risk Shield, the
O'Pip Decision Engine, or any outbound messaging integration. It exists so
BUILD 4.2 can add a live worker against a stable contract layer without
changing these types materially.
"""
