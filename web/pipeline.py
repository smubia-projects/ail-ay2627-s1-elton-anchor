"""Answering a question in the website: the Phase 8 route, scoped to one Library.

  1. Follow-ups   with chat history, one Luna call rewrites the question to
                  stand alone ("and for G8?" -> "Who is the TA for G8?").
  2. Router       one cheap call tags the question. In the full app a numeric
                  question goes to the SQL route over the Library's
                  spreadsheets; the demo reads no spreadsheets, so every tag
                  goes to hybrid search (retrieval.py).
  3. Answer       the same Explained-answer prompt the eval scores (answering.py),
                  streamed: the page shows each step and the final answer as
                  they are written (stream() below).
  4. Checker      quotes + support against what was retrieved; failures make
                  the answer Unsure, naming the failed check.

The demo reads no pictures (no Diagram descriptions), so no answer rests on one.
Every query filters on the Library id (retrieval.py), so one visitor's question
never reaches another visitor's material (record 0001).
"""
import pathlib
import queue
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import partial_json  # noqa: E402  (web/, beside this file)
import checker  # noqa: E402
import retrieval  # noqa: E402  (web/)
import router  # noqa: E402
from answering import ask_with_docs  # noqa: E402
from openai_client import ask_json, client  # noqa: E402

K = 5  # chunks per answer; k scales with Library size (build plan) - 5 suits a small Library

CONDENSE = """\
A student is asking follow-up questions about their course materials. Rewrite
their latest message as one question that stands on its own, using the earlier
turns only to fill in what it refers to. If it already stands alone, return it
unchanged. Do not answer it.

EARLIER TURNS (oldest first)
{history}

LATEST MESSAGE
{question}

Reply with JSON only: {{"question": "<the standalone question>"}}
"""

_client = None


def _c():
    global _client
    if _client is None:
        _client = client()
    return _client


def condense(question, history):
    if not history:
        return question, None
    turns = "\n".join(f"Student: {h['question']}\nAssistant: {h['answer'].get('final_answer', '')}"
                      for h in history)
    reply, usage = ask_json(_c(), CONDENSE.format(history=turns, question=question), effort="low",
                            valid=lambda v: isinstance(v.get("question"), str) and v["question"].strip())
    return reply["question"].strip(), usage


def _excerpts(evidence):
    """What the answering model was given to read, in rank order, for the page's Sources panel."""
    return [{"id": e["id"], "file": e["file"], "loc": e.get("loc") or "", "text": e["text"]} for e in evidence or []]


def _steps(steps, source):
    """Steps as the page shows them: each source id becomes {id, file, loc}."""
    out = []
    for s in steps or []:
        if not isinstance(s, dict) or not isinstance(s.get("text"), str):
            continue
        ev = source(s.get("source")) if isinstance(s.get("source"), str) else None
        step = {"text": s["text"], "quote": s.get("quote") if isinstance(s.get("quote"), str) else None,
                "source": {"id": ev["id"], "file": ev["file"], "loc": ev["loc"]} if ev else None}
        if s.get("calc"):  # a worked solution's calculation, run as code (worked.py)
            step |= {"calc": s["calc"], "value": s.get("value")}
        out.append(step)
    return out


class _Drafts:
    """Turns the reply so far into a draft answer for the page while it is being
    written: at most one draft every DRAFT_EVERY seconds, and only when it changed.
    A step's citation appears once its source id is complete."""

    DRAFT_EVERY = 0.06

    def __init__(self, emit, source, base):
        self.emit, self.source, self.base = emit, source, base
        self.text, self.last, self.sent = "", 0.0, None

    def __call__(self, text):
        self.text = text
        if time.monotonic() - self.last >= self.DRAFT_EVERY:
            self.flush()

    def flush(self):
        obj = partial_json.parse(self.text)
        if not obj:
            return
        draft = self.base | {"final_answer": obj.get("final_answer") if isinstance(obj.get("final_answer"), str) else "",
                             "not_found": obj.get("not_found") is True,
                             "steps": _steps(obj.get("steps"), self.source)}
        if draft != self.sent:
            self.sent, self.last = draft, time.monotonic()
            self.emit({"type": "draft", "answer": draft})


def answer(library_id, question, history=(), emit=None):
    """Everything the page shows for one question.
    emit(event), if given, hears how it is going (see stream())."""
    emit = emit or (lambda event: None)
    c = _c()
    timing, t0 = {}, time.monotonic()

    def lap(name):
        nonlocal t0
        timing[name] = round(time.monotonic() - t0, 1)
        t0 = time.monotonic()

    emit({"type": "stage", "stage": "reading"})
    standalone, _ = condense(question, list(history))
    lap("condense")
    tag, _ = router.route(c, standalone)
    lap("router")
    route = {"tag": tag, "path": "hybrid"}
    base = {"question": question, "standalone": standalone, "route": route}

    hits = retrieval.search(standalone, K, library_id)
    lap("search")
    evidence = [retrieval.as_evidence(h) for h in hits]
    emit({"type": "excerpts", "excerpts": _excerpts(evidence)})
    by_id = {e["id"]: e for e in evidence}
    drafts = _Drafts(emit, by_id.get, base)
    result = ask_with_docs(c, standalone, evidence, on_text=drafts, library_pictures=[])
    drafts.flush()
    timing["first_text"] = (result.get("usage") or {}).get("first_text_seconds")
    lap("answer")
    emit({"type": "stage", "stage": "checking"})
    checked, _ = checker.check(c, standalone, result)
    lap("check")
    print(f"[answer] {route['path']}: " + ", ".join(f"{k} {v}s" for k, v in timing.items()), flush=True)

    by_id = {e["id"]: e for e in result.get("retrieved", [])}
    return base | {
        "final_answer": result["final_answer"], "not_found": bool(result.get("not_found")),
        "steps": _steps(result.get("steps"), by_id.get),
        "unsure": checked["unsure"], "unsure_reasons": checked["unsure_reasons"],
        "pictures": [], "excerpts": _excerpts(result.get("retrieved")),
    }


def stream(library_id, question, history=()):
    """answer(), as events while it happens, for the website's streamed answers:

      {"type": "stage", "stage": "reading"}    rewriting the question, routing, searching
      {"type": "excerpts", "excerpts": [...]}  what was found, before the answer is written
      {"type": "draft", "answer": {...}}       the answer so far, as the model writes it
      {"type": "stage", "stage": "checking"}   written; the checker is running
      {"type": "done", "answer": {...}}
      {"type": "error", "message": "..."}

    A draft is shown before it is checked; the page says so until "done".
    Nothing is stored: the chat history stays in the visitor's browser."""
    events = queue.Queue()

    def work():
        try:
            events.put({"type": "done", "answer": answer(library_id, question, history, emit=events.put)})
        except Exception as e:  # the page shows a plain message; the server log keeps the detail
            import traceback
            traceback.print_exc()
            events.put({"type": "error", "message": f"Something went wrong while answering ({type(e).__name__})."})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()
    while (event := events.get()) is not None:
        yield event
