# application/reference_index/

Startup index and exact-name lookups for referenced `.prt` component files.

| Module | Responsibility |
| --- | --- |
| `roots.py` | Configured reference roots: `PRT_REFERENCE_ROOT`, legacy `PRT_REFERENCE_ROOTS`, and the project `.env` file. Also owns the `ReferenceIndex` snapshot type. |
| `cache.py` | Atomic project-local cache (`.prt-reference-index.json`) with root-matching validation. |
| `indexing.py` | One `os.walk`-based build of the filename index and the process-level retained snapshot (`initialize_reference_index`, `reference_index`). |
| `lookup.py` | `find_sub_assembly`: parent-directory first, then indexed roots, then additional roots. |

Lookup order is deliberate: the assembly's own directory is authoritative and
is checked first; configured roots follow in declared order, so network
directories are walked once at startup instead of per lookup.
