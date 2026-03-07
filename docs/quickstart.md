# Quickstart

## Python API

```python
from gutenbit import Catalog, Database

catalog = Catalog.fetch()
books = catalog.search(author="Shakespeare")[:2]

with Database("gutenbit.db") as db:
    db.ingest(books)
    results = db.search("to be or not to be")

for hit in results[:3]:
    print(hit.title)
    print(hit.content[:160])
```

## Why this stays maintainable

- `Catalog` and `Database` are stable, top-level imports.
- The example is short and mirrors real usage.
- API pages are generated automatically from module docstrings.
