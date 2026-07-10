"""Health-data integration (CQRS).

`commands.py` mutates state (sync, import, add, delete) and `queries.py` reads
the Activity read model (lists, stats, weekly volume). Route handlers dispatch
commands with `commands.handle(db, cmd)` and queries with `queries.ask(db, q)`.
"""
