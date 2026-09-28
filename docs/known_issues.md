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

## Default model differs between entry points

**Status:** Open

`process_chat` and `POST /chats/{chat_id}/send` default to `gpt-5-mini`, while `call_gpt`, `call_gpt_single`, `call_gpt_stream`, `stream_chat` and `POST /chats/{chat_id}/send/stream` default to `gpt-4o`. Callers get different models depending on the entry point unless they pass `model`.

## Client disconnect during stream_chat leaves the message in_progress

**Status:** Open

If the client disconnects while `stream_chat` is streaming, the generator is closed with `GeneratorExit`, which the `except Exception` handler does not catch. No `failed` status is written, so the latest status stays `in_progress`.

## process_chat is synchronous

**Status:** Open

`process_chat` and the OpenAI client are synchronous. An `async def` caller that invokes `process_chat` directly blocks the event loop for the whole LLM exchange. Async callers should use `aprocess_chat`, which runs it in a worker thread, or call `process_chat` from a plain `def` background task.

