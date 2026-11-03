"""Calling Claude for the readers (log links, P&IDs, documents, curves, mapping suggestions): the default model
and effort, one request with the server-side refusal fallback, token usage, the Message Batches API and the
text hash results are cached under."""

import hashlib
import os
import time

from . import models

DEFAULT_MODEL = os.environ.get("FUZER_LLM_MODEL", models.DEFAULT_MODEL)
DEFAULT_EFFORT = os.environ.get("FUZER_LLM_EFFORT", "medium")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def call(client, params: dict):
    """One synchronous request with server-side refusal fallback."""
    return client.beta.messages.create(
        **params,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if the model declines, the API retries on a fallback model
    )


def usage(message) -> dict:
    u = message.usage
    return {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}


def run_batch(client, requests: dict[str, dict], on_result, totals: dict, errors: list, poll_seconds: int = 30):
    """Half-price asynchronous run through the Message Batches API (usually done within an hour).
    requests maps custom_id -> request params; on_result(custom_id, message) handles each success."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    created = client.messages.batches.create(requests=[
        Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**params)) for cid, params in requests.items()
    ])
    print(f"batch {created.id}: {len(requests)} requests submitted", flush=True)
    while (b := client.messages.batches.retrieve(created.id)).processing_status != "ended":
        print(f"batch {created.id}: {b.request_counts.processing} still processing", flush=True)
        time.sleep(poll_seconds)
    for result in client.messages.batches.results(created.id):
        if result.result.type == "succeeded":
            try:
                on_result(result.custom_id, result.result.message)
            except Exception as e:
                totals["failed_requests"] += 1
                errors.append(f"{result.custom_id}: {type(e).__name__}: {e}")
        else:
            totals["failed_requests"] += 1
            errors.append(f"{result.custom_id}: {result.result.type}")
