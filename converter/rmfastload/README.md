# converter/rmfastload

Parses `/Root/FastLoad/RMFastLoad` object-id membership for NX parts and
reports why the direct-JT display path cannot apply the cadmpeg-style active
body subset relation.

| Module | Role |
|---|---|
| `parser.py` | Bounded physical allocation and product-bounded membership table. |
| `selection.py` | Complete-set subset selector and membership overlap counts. |
| `diagnostics.py` | Stream lookup and a conversion-summary diagnostic. |
| `__main__.py` | `python -m converter.rmfastload <part.prt>` report. |

The membership ids are Parasolid FACE/EDGE/VERTEX node ids. Direct-JT decoding
exposes no complete body-to-node-id set, so the parser never drops geometry.
`select_active_bodies` is the relation to use once topology-backed body
node-id sets are available.
