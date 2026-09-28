# Offline tests for services/chat_service.py and the chat message routes.
# Uses mongomock and a fake OpenAI client; no database, network or API key is needed.
# Run from the magenta root: PYTHONPATH=. pytest tests/test_chat_service.py
import sys
import json
import types
import asyncio
import importlib.util
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest
from loguru import logger
from openai.types.chat import ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

mongomock = pytest.importorskip("mongomock")

# stub modules that connect to databases or load heavy dependencies at import time
root = Path(__file__).parents[1]
if "core" not in sys.modules:
  core_package = types.ModuleType("core")
  core_package.__path__ = [str(root / "core")] # submodules load from disk, core/__init__.py is skipped
  sys.modules["core"] = core_package
sys.modules.setdefault("core.config", types.SimpleNamespace(
  logger=logger, openai_client=None, spacy_model=None, get_db=lambda: None, tenant_collections=None
))
sys.modules.setdefault("services.document_service", types.SimpleNamespace(
  perform_postgre_search=None,
  add_rag_results_to_message=lambda sysprompt, new_message, **kwargs: (new_message, None),
  add_documents_to_sysprompt=lambda sysprompt, documents_collection: sysprompt
))
sys.modules.setdefault("services.data_import", types.SimpleNamespace(
  load_documents_from_files=None, load_prompts_from_files=None
))

from services import chat_service
from services.chat_service import call_gpt, process_chat, stream_chat, call_gpt_stream

spec = importlib.util.spec_from_file_location("chats_routes_under_test", root / "routes" / "chats.py")
chats_routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chats_routes)


class FakeClient:
  """Returns queued responses (or raises queued exceptions) from chat.completions.create."""
  def __init__(self, responses):
    self.responses = list(responses)
    self.calls = []
    self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

  def create(self, **kwargs):
    self.calls.append(json.loads(json.dumps(kwargs, default=str)))
    response = self.responses.pop(0)
    if isinstance(response, Exception):
      raise response
    return response


def completion(content=None, tool_calls=None):
  return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def tool_call(call_id, name, arguments):
  return ChatCompletionMessageToolCall(id=call_id, type="function", function=Function(name=name, arguments=json.dumps(arguments)))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
  monkeypatch.setattr(chat_service.time, "sleep", lambda seconds: None)


@pytest.fixture
def db():
  database = mongomock.MongoClient()["magenta_offline_test"]
  database.prompts.insert_one({"prompt_id": "dm", "name": "dm", "type": "system", "prompt": "You are a DM.", "toolset": ["add_item"]})
  database.tools.insert_one({
    "tool_id": "add_item", "type": "function",
    "function": {
      "name": "add_item", "description": "Add an item",
      "parameters": {"type": "object", "properties": {"item": {"type": "string", "description": "item"}}, "required": ["item"]}
    }
  })
  database.chats.insert_one({"chat_id": "c1", "context_id": "s1", "sysprompt_id": "dm", "messages": [], "statuses": []})
  return database


def run_chat(db, client, message_id, text, inventory, **kwargs):
  return process_chat(
    chat_id="c1", message_id=message_id, new_message=text,
    chats_collection=db.chats, prompts_collection=db.prompts,
    documents_collection=db.documents, tools_collection=db.tools,
    call_llm_func=partial(call_gpt, client=client), json_mode=True,
    function_dictionary={"add_item": lambda item: inventory.append(item) or f"added {item}"},
    db=None, **kwargs
  )


# call_gpt -----------------------------------------------------------------

def test_control_chars_retried_then_clean():
  client = FakeClient([completion('{"text": "then\\u0014n"}'), completion('{"text": "then \\u2014 an"}')])
  result = call_gpt([{"role": "user", "content": "json please"}], client=client, json_mode=True)
  assert result["message"] == {"text": "then \u2014 an"}
  assert len(client.calls) == 2


def test_control_chars_stripped_after_retries():
  bad = completion('{"a": ["x\\u0019y", {"k\\u0001": "tab\\tnewline\\n"}]}')
  client = FakeClient([bad, bad, bad])
  result = call_gpt([{"role": "user", "content": "json"}], client=client, json_mode=True)
  assert result["message"] == {"a": ["xy", {"k": "tab\tnewline\n"}]}
  assert len(client.calls) == 3


def test_json_word_added_when_missing():
  client = FakeClient([completion('{"ok": 1}')])
  messages = [{"role": "user", "content": "hello", "message_id": "q-1"}]
  call_gpt(messages, sysprompt="Be brief.", client=client, json_mode=True)
  sent = client.calls[0]["messages"]
  assert sent[0] == {"role": "developer", "content": "Be brief.\n\nRespond in JSON."}
  assert messages == [{"role": "user", "content": "hello", "message_id": "q-1"}] # caller's list untouched


def test_json_word_not_added_when_present_or_not_json_mode():
  client = FakeClient([completion('{"ok": 1}'), completion("hi")])
  call_gpt([{"role": "user", "content": "Reply in JSON"}], sysprompt="Be brief.", client=client, json_mode=True)
  call_gpt([{"role": "user", "content": "hello"}], sysprompt="Be brief.", client=client)
  assert client.calls[0]["messages"][0]["content"] == "Be brief."
  assert client.calls[1]["messages"][0]["content"] == "Be brief."
  assert "response_format" not in client.calls[1]


def test_response_format_override():
  schema = {"type": "json_schema", "json_schema": {"name": "x", "strict": True, "schema": {"type": "object"}}}
  client = FakeClient([completion('{"ok": 1}')])
  call_gpt([{"role": "user", "content": "hello"}], client=client, json_mode=True, response_format=schema)
  assert client.calls[0]["response_format"] == schema
  assert client.calls[0]["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.parametrize("bad_content", [None, "", "not json"])
def test_missing_or_invalid_json_retried_once(bad_content):
  client = FakeClient([completion(bad_content), completion('{"ok": 1}')])
  assert call_gpt([{"role": "user", "content": "json"}], client=client, json_mode=True)["message"] == {"ok": 1}
  client = FakeClient([completion(bad_content), completion(bad_content)])
  with pytest.raises(ValueError, match="no valid JSON"):
    call_gpt([{"role": "user", "content": "json"}], client=client, json_mode=True)


def test_json_mode_tool_calls_without_content():
  calls = [tool_call("t1", "add_item", {"item": "rope"})]
  client = FakeClient([completion(None, calls)])
  result = call_gpt([{"role": "user", "content": "json"}], client=client, json_mode=True, tools=[{"type": "function"}])
  assert result == {"message": None, "tool_calls": calls}
  assert len(client.calls) == 1


def test_transient_errors_retried():
  request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
  client = FakeClient([
    openai.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None),
    openai.APIConnectionError(request=request),
    openai.InternalServerError("oops", response=httpx.Response(500, request=request), body=None),
    completion("fine")
  ])
  assert call_gpt([{"role": "user", "content": "hi"}], client=client)["message"] == "fine"
  request_error = openai.APITimeoutError(request=request)
  client = FakeClient([request_error] * 4)
  with pytest.raises(openai.APITimeoutError):
    call_gpt([{"role": "user", "content": "hi"}], client=client)
  assert len(client.calls) == 4


def test_non_transient_error_not_retried():
  request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
  client = FakeClient([openai.BadRequestError("bad", response=httpx.Response(400, request=request), body=None)])
  with pytest.raises(openai.BadRequestError):
    call_gpt([{"role": "user", "content": "hi"}], client=client)
  assert len(client.calls) == 1


def test_stream_strips_control_chars_and_adds_json_word():
  chunk = lambda text: SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))])
  client = FakeClient([[chunk('{"text": "a'), chunk('\\u0014b"}')]])
  gen = call_gpt_stream([{"role": "user", "content": "hello"}], client=client, json_mode=True)
  with pytest.raises(StopIteration) as stop:
    while True:
      next(gen)
  assert json.loads(stop.value.value["message"]) == {"text": "ab"}
  assert client.calls[0]["messages"][0] == {"role": "developer", "content": "Respond in JSON."}


# process_chat ---------------------------------------------------------------

def test_tool_messages_persisted_hidden_and_replayed(db):
  inventory = []
  client = FakeClient([
    completion(None, [tool_call("t1", "add_item", {"item": "rope"}), tool_call("t2", "add_item", {"item": "lamp"})]),
    completion(None, [tool_call("t3", "add_item", {"item": "key"})]),
    completion('{"text": "You pick up the rope, lamp and key."}'),
    completion('{"text": "You already have them."}'),
  ])
  result = run_chat(db, client, "m1", "take everything", inventory)
  assert result == {"message": {"text": "You pick up the rope, lamp and key."}}
  assert inventory == ["rope", "lamp", "key"]

  chat = db.chats.find_one({"chat_id": "c1"})
  assert [(m["role"], m["message_id"], m.get("internal", False)) for m in chat["messages"]] == [
    ("user", "q-m1", False),
    ("assistant", "m1", True), ("tool", "m1", True), ("tool", "m1", True),
    ("assistant", "m1", True), ("tool", "m1", True),
    ("assistant", "m1", False),
  ]
  assert chat["messages"][2]["tool_call_id"] == "t1" and chat["messages"][2]["content"] == "added rope"
  assert chat["statuses"][-1] == {"message_id": "m1", "status": "completed"}

  # next turn replays the tool rounds without storage-only keys
  run_chat(db, client, "m2", "anything else?", inventory)
  sent = client.calls[-1]["messages"]
  assert [m["role"] for m in sent] == ["developer", "user", "assistant", "tool", "tool", "assistant", "tool", "assistant", "user"]
  assert all(not ({"message_id", "timestamp", "internal"} & m.keys()) for m in sent)
  assert sent[2]["tool_calls"][0]["id"] == "t1"
  assert all(isinstance(m["content"], str) for m in sent if "content" in m)
  assert inventory == ["rope", "lamp", "key"]

  # player-facing listings exclude internal messages
  chats_routes.tenant_collections = SimpleNamespace(get_collection=lambda tenant_id, name: db[name])
  listed = asyncio.run(chats_routes.list_chat_messages("c1"))
  assert [(m["role"], m["message_id"]) for m in listed] == [("user", "q-m1"), ("assistant", "m1"), ("user", "q-m2"), ("assistant", "m2")]
  all_messages = asyncio.run(chats_routes.list_chat_messages("c1", no_internal=False))
  assert len(all_messages) == 9 and all_messages[1]["content"] == "--INTERNAL--"
  message = asyncio.run(chats_routes.get_chat_message("c1", "m1"))
  assert message["content"] == json.dumps({"text": "You pick up the rope, lamp and key."})
  assert [m["message_id"] for m in asyncio.run(chats_routes.get_chat("c1"))["messages"]] == ["q-m1", "m1", "q-m2", "m2"]
  assert asyncio.run(chats_routes.get_chat_message_status("c1", "m1"))["status"] == "completed"


def test_old_format_chat_still_works(db):
  db.chats.update_one({"chat_id": "c1"}, {"$set": {"messages": [
    {"message_id": "q-old", "role": "user", "content": "hi"},
    {"message_id": "old", "role": "assistant", "content": '{"text": "hello"}'},
  ], "statuses": [{"message_id": "old", "status": "in_progress"}, {"message_id": "old", "status": "completed"}]}})
  client = FakeClient([completion('{"text": "welcome back"}')])
  assert run_chat(db, client, "m1", "again", [])["message"] == {"text": "welcome back"}
  assert [m["role"] for m in client.calls[0]["messages"]] == ["developer", "user", "assistant", "user"]
  assert [m["message_id"] for m in db.chats.find_one({"chat_id": "c1"})["messages"]] == ["q-old", "old", "q-m1", "m1"]


def test_failed_status_pushed_and_completed_rounds_kept(db):
  inventory = []
  client = FakeClient([
    completion(None, [tool_call("t1", "add_item", {"item": "rope"})]),
    completion(None), completion(None), # invalid JSON-mode completion twice
  ])
  errors = []
  with pytest.raises(ValueError, match="no valid JSON"):
    run_chat(db, client, "m1", "take rope", inventory, error_callback_func=lambda chat_id, e: errors.append(chat_id))
  chat = db.chats.find_one({"chat_id": "c1"})
  assert chat["statuses"][0] == {"message_id": "m1", "status": "in_progress"}
  assert chat["statuses"][-1]["status"] == "failed" and "no valid JSON" in chat["statuses"][-1]["error"]
  assert [(m["role"], m.get("internal", False)) for m in chat["messages"]] == [("user", False), ("assistant", True), ("tool", True)]
  assert errors == ["c1"]

  chats_routes.tenant_collections = SimpleNamespace(get_collection=lambda tenant_id, name: db[name])
  status = asyncio.run(chats_routes.get_chat_status("c1"))
  assert status["status"] == "failed" and "no valid JSON" in status["result"]["error"]
  assert asyncio.run(chats_routes.get_chat_message_status("c1", "m1"))["status"] == "failed"

  # a later turn after the failure produces a valid message sequence
  client = FakeClient([completion('{"text": "ok"}')])
  run_chat(db, client, "m2", "retry", inventory)
  assert [m["role"] for m in client.calls[0]["messages"]] == ["developer", "user", "assistant", "tool", "user"]
  assert db.chats.find_one({"chat_id": "c1"})["statuses"][-1] == {"message_id": "m2", "status": "completed"}


def test_failed_status_on_missing_prompt(db):
  db.chats.update_one({"chat_id": "c1"}, {"$set": {"sysprompt_id": "missing"}})
  with pytest.raises(ValueError, match="not found"):
    run_chat(db, FakeClient([]), "m1", "hi", [])
  assert db.chats.find_one({"chat_id": "c1"})["statuses"][-1]["status"] == "failed"


def test_skip_word(db):
  client = FakeClient([completion("PASS")])
  sent = []
  result = process_chat(
    chat_id="c1", message_id="m1", new_message="hi",
    chats_collection=db.chats, prompts_collection=db.prompts,
    documents_collection=db.documents, tools_collection=db.tools,
    call_llm_func=partial(call_gpt, client=client), db=None, skip_word="PASS",
    callback_func=lambda message, chat_id: sent.append(message)
  )
  assert result == {} and sent == []
  assert db.chats.find_one({"chat_id": "c1"})["statuses"][-1]["status"] == "completed"


# stream_chat ----------------------------------------------------------------

def test_stream_chat_persists_tool_rounds_and_failure(db):
  inventory = []
  def fake_stream(responses):
    def call_llm_func(**kwargs):
      yield "chunk"
      return responses.pop(0)
    return call_llm_func

  responses = [
    {"message": "", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "add_item", "arguments": '{"item": "rope"}'}}]},
    {"message": "done", "tool_calls": None},
  ]
  events = list(stream_chat(
    chat_id="c1", message_id="m1", new_message="take rope",
    chats_collection=db.chats, prompts_collection=db.prompts,
    documents_collection=db.documents, tools_collection=db.tools,
    call_llm_func=fake_stream(responses), function_dictionary={"add_item": lambda item: inventory.append(item) or "added"}
  ))
  assert '"type": "done"' in events[-1]
  chat = db.chats.find_one({"chat_id": "c1"})
  assert [(m["role"], m.get("internal", False)) for m in chat["messages"]] == [("user", False), ("assistant", True), ("tool", True), ("assistant", False)]

  gen = stream_chat(
    chat_id="c1", message_id="m2", new_message="again",
    chats_collection=db.chats, prompts_collection=db.prompts,
    documents_collection=db.documents, tools_collection=db.tools,
    call_llm_func=fake_stream([]),
  )
  with pytest.raises(IndexError):
    list(gen)
  assert db.chats.find_one({"chat_id": "c1"})["statuses"][-1]["status"] == "failed"


def test_aprocess_chat(db):
  client = FakeClient([completion('{"text": "async"}')])
  result = asyncio.run(chat_service.aprocess_chat(
    chat_id="c1", message_id="m1", new_message="hi",
    chats_collection=db.chats, prompts_collection=db.prompts,
    documents_collection=db.documents, tools_collection=db.tools,
    call_llm_func=partial(call_gpt, client=client), json_mode=True, db=None
  ))
  assert result == {"message": {"text": "async"}}
