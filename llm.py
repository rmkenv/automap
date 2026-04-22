"""
llm.py — Intent parsing via Ollama Cloud API
Extracts structured map intent from natural language descriptions.
"""

import os
import json
import re
from ollama import Client


def get_ollama_client() -> Client:
    """Initialize Ollama Cloud client."""
    api_key = os.environ.get("OLLAMA_API_KEY")
    if not api_key:
        raise ValueError("OLLAMA_API_KEY environment variable not set.")
    return Client(
        host="https://ollama.com",
        headers={"Authorization": f"Bearer {api_key}"},
    )


INTENT_SYSTEM_PROMPT = """You are a GIS assistant that parses natural language map requests into structured JSON.

Given a user's description of a map they want to build, extract:
- geography: the place name or region (e.g. "Baltimore, MD", "Long Island, NY", "Cook County, IL")
- layers: REQUIRED non-empty list of 1-4 GIS layer types. If the user does not specify layers, choose 2 sensible defaults for that geography (e.g. ["census tracts", "counties"]). NEVER return an empty list.
- basemap: one of: "streets", "satellite", "topo", "light", "dark" (default "streets")
- notes: any special styling or filtering requests (optional, can be null)

Respond ONLY with valid JSON, no preamble, no markdown, no explanation. Example:
{
  "geography": "Baltimore, MD",
  "layers": ["census tracts", "flood zones"],
  "basemap": "light",
  "notes": null
}"""

# JSON Schema for Ollama structured outputs
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "geography": {"type": "string"},
        "layers": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 4,
        },
        "basemap": {
            "type": "string",
            "enum": ["streets", "satellite", "topo", "light", "dark"],
        },
        "notes": {"type": ["string", "null"]},
    },
    "required": ["geography", "layers", "basemap"],
}

VALID_BASEMAPS = {"streets", "satellite", "topo", "light", "dark"}


def parse_map_intent(user_description: str, model: str = "gpt-oss:120b") -> dict:
    """
    Parse a natural language map description into structured intent JSON.

    Args:
        user_description: Free-text map request from the user
        model: Ollama Cloud model to use

    Returns:
        dict with keys: geography, layers, basemap, notes
    """
    client = get_ollama_client()

    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Parse this map request into JSON:\n\n{user_description}",
        },
    ]

    # Try with structured output format first (guarantees schema)
    try:
        response = client.chat(
            model=model,
            messages=messages,
            format=INTENT_SCHEMA,
            options={"temperature": 0.1},
        )
    except Exception:
        # Fall back to plain chat if model doesn't support format param
        response = client.chat(
            model=model,
            messages=messages,
            options={"temperature": 0.1},
        )

    raw = response["message"]["content"].strip()

    # Strip markdown fences if model added them anyway
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    try:
        intent = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned unparseable JSON:\n{raw}\n\nError: {e}")

    # ── Validate and normalize required fields ────────────────────────────────

    # geography
    if not intent.get("geography"):
        raise ValueError(f"LLM response missing 'geography' field. Raw: {raw}")

    # layers — must be a non-empty list; if empty, infer a sensible default
    layers = intent.get("layers")
    if isinstance(layers, str):
        layers = [layers]
    if not isinstance(layers, list) or len(layers) == 0:
        # LLM returned empty layers — infer from geography or use sensible defaults
        geography = intent.get("geography", "").lower()
        if any(w in geography for w in ["bay", "river", "creek", "flood", "water"]):
            layers = ["flood zones", "counties"]
        else:
            layers = ["census tracts", "counties"]
    intent["layers"] = [str(l).strip() for l in layers if str(l).strip()]

    # basemap — default if missing or invalid
    basemap = intent.get("basemap", "streets")
    if basemap not in VALID_BASEMAPS:
        basemap = "streets"
    intent["basemap"] = basemap

    # notes — optional
    intent.setdefault("notes", None)

    return intent


def refine_layer_query(layer_name: str, geography: str, model: str = "gpt-oss:120b") -> str:
    """
    Generate an optimized ArcGIS Online search query string for a given layer type.

    Args:
        layer_name: e.g. "FEMA flood zones"
        geography: e.g. "Baltimore, MD"

    Returns:
        Search query string for AGOL
    """
    client = get_ollama_client()

    messages = [
        {
            "role": "user",
            "content": (
                f"I need to search ArcGIS Online for the layer: '{layer_name}' in '{geography}'.\n"
                f"Generate a short (3-6 word) ArcGIS Online search query that would find authoritative "
                f"versions of this layer. Prioritize official government or agency sources.\n"
                f"Respond ONLY with the search query string, nothing else."
            ),
        }
    ]

    response = client.chat(
        model=model,
        messages=messages,
        options={"temperature": 0.1},
    )

    return response["message"]["content"].strip().strip('"')
