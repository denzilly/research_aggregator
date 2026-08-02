import json
import re

import requests

import config

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
BATCH_SIZE = 15
REQUEST_TIMEOUT = 120
MAX_ATTEMPTS = 2

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _system_prompt(keywords: list[str]) -> str:
    keyword_list = ", ".join(keywords)
    return (
        "You will be given a JSON array of paper abstracts, each as "
        '{"id": ..., "abstract": ...}. For each paper, evaluate its relevance '
        f"to these research interest keywords: {keyword_list}.\n\n"
        "Return, for every input id:\n"
        "- relevance_score: a number 0-10 (10 = highly relevant, 0 = unrelated)\n"
        "- summary: a 2-3 sentence plain-language summary of the paper aimed at "
        "a researcher already familiar with this subfield\n\n"
        "Respond ONLY with a JSON array, no markdown fences, no other text:\n"
        '[{"id": "...", "relevance_score": <number>, "summary": "..."}, ...]'
    )


def _parse_response_content(content: str) -> dict[str, dict]:
    cleaned = FENCE_RE.sub("", content).strip()
    try:
        items = json.loads(cleaned)
    except json.JSONDecodeError:
        # Model added stray preamble/postamble text despite instructions —
        # fall back to extracting the outermost [...] array.
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end == -1 or end < start:
            raise
        items = json.loads(cleaned[start:end + 1])

    result = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        paper_id = item.get("id")
        if not paper_id:
            continue
        result[paper_id] = {
            "relevance_score": item.get("relevance_score"),
            "summary": item.get("summary"),
        }
    return result


def _call_openrouter(batch: list[dict], keywords: list[str]) -> dict[str, dict]:
    # Note: OpenRouter/deepseek's `response_format: json_object` mode forces
    # a top-level JSON *object*, incompatible with the top-level array this
    # prompt asks for — deliberately not used here. Plain prompting + a
    # tolerant parser (above) proved more reliable in testing.
    user_content = json.dumps([
        {"id": p["id"], "abstract": p.get("abstract") or p.get("title", "")}
        for p in batch
    ])

    payload = {
        "model": config.OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt(keywords)},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 4000,
    }

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_response_content(content)


def _score_batch(batch: list[dict], keywords: list[str]) -> dict[str, dict]:
    for _ in range(MAX_ATTEMPTS):
        try:
            return _call_openrouter(batch, keywords)
        except Exception:
            continue
    return {}


def score_and_summarize(papers: list[dict], keywords: list[str]) -> dict[str, dict]:
    """Returns {paper_id: {"relevance_score": float|None, "summary": str|None}}.
    Papers the model fails to score (batch error, or the model omitted an id)
    still get an entry with None values so ingestion writes the row rather
    than dropping the paper."""
    results: dict[str, dict] = {}

    for i in range(0, len(papers), BATCH_SIZE):
        batch = papers[i:i + BATCH_SIZE]
        batch_results = _score_batch(batch, keywords)
        for paper in batch:
            results[paper["id"]] = batch_results.get(
                paper["id"], {"relevance_score": None, "summary": None}
            )

    return results
