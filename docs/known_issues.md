# Known Issues

## spacy_model passed as None in document routes

**Status:** Open
**Affected Tests:** `test_augmented_retrieval`, `test_upload_get_and_delete_document`

### Problem

`routes/documents.py` passes `spacy_model=None` at:
- Line 110 (`upload_document` endpoint)
- Line 136 (`search_documents` endpoint)

This causes `embed_text_spacy()` to fail with `'NoneType' object is not callable` when trying to call `spacy_model(text)`.

### Fix

Get the actual spacy model instance (see `services/data_import.py:133` for working example) via app state or dependency injection.
