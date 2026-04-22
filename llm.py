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
- layers: a list of 1-4 GIS layer types they want to see (e.g. ["census tracts", "FEMA flood zones", "hospitals"])
- basemap: one of: "streets", "satellite", "topo", "light", "dark" (default "streets")
- notes: any special styling or filtering requests (optional, can be null)

Respond ONLY with valid JSON, no preamble, no markdown, no explanation. Example:
{
  "geography": "Baltimore, MD",
  "layers": ["census tracts", "flood zones"],
  "basemap": "light",
  "notes": "highlight areas with high flood risk"
}"""


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
        {
            "role": "user",
            "content": f"Parse this map request into JSON:\n\n{user_description}",
        }
    ]

    response = client.chat(
        model=model,
        messages=messages,
        options={"temperature": 0.1},  # Low temp for structured output
    )

    raw = response["message"]["content"].strip()

    # Strip markdown fences if model added them
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    try:
        intent = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned unparseable JSON: {raw}\n\nError: {e}")

    # Normalize fields
    intent.setdefault("basemap", "streets")
    intent.setdefault("notes", None)
    if isinstance(intent.get("layers"), str):
        intent["layers"] = [intent["layers"]]

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
