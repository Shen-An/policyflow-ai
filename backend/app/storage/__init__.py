"""Object-store package: versioned bytes are an authority of their own (Stage 5).

``data-model.md`` Cross-Entity Invariant #5: PostgreSQL owns business state,
Milvus owns vectors, the object store owns bytes, and no adapter becomes a second
authority for any of them. This package is the only place in the application that
talks to the object store.
"""
