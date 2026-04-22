# 🗺️ Map Genie

**Natural Language → ArcGIS Online → Interactive Folium Map**

Describe any map in plain English. Map Genie uses Ollama Cloud to parse your intent, searches ArcGIS Online for matching public layers, and assembles an interactive Folium map — no GIS expertise required.

## How It Works

```
"Show me FEMA flood zones and census tracts in Baltimore, MD"
         ↓
   Ollama Cloud (gpt-oss:120b)
   → { geography, layers, basemap }
         ↓
   ArcGIS Online REST API
   → Feature Service URLs + GeoJSON
         ↓
   Folium Map
   → Interactive HTML rendered in Streamlit
```

## Stack

| Component | Tool |
|-----------|------|
| LLM | Ollama Cloud (`gpt-oss:120b`) |
| Layer search | ArcGIS Online REST API |
| Geocoding | Nominatim (OSM, free, no key) |
| Map rendering | Folium |
| UI | Streamlit |

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get an Ollama API key

Sign up at [ollama.com](https://ollama.com) → Settings → API Keys

### 3. Run locally

```bash
export OLLAMA_API_KEY=your_key_here
streamlit run app.py
```

Or set it in the sidebar at runtime.

## Deploy to Streamlit Cloud

1. Push this repo to GitHub
2. Connect at [share.streamlit.io](https://share.streamlit.io)
3. Add `OLLAMA_API_KEY` as a secret in the Streamlit Cloud settings
4. Deploy — no GPU required

## Project Structure

```
map_genie/
├── app.py           # Streamlit UI + pipeline orchestration
├── llm.py           # Ollama Cloud intent parsing
├── agol_search.py   # ArcGIS Online search + GeoJSON fetch
├── map_builder.py   # Folium map assembly
├── requirements.txt
└── .env.example
```

## Example Prompts

- `Show me FEMA flood zones and census tracts in Baltimore, MD`
- `Map hospitals and parks in Washington DC with a satellite basemap`
- `Show superfund sites and low income areas in Detroit, MI`
- `Give me wetlands and farmland in the Chesapeake Bay watershed`
- `Show bike infrastructure and transit stops in Philadelphia, PA`

## Notes

- Searches **public** ArcGIS Online layers only (no AGOL login required)
- Results ranked by authoritativeness (FEMA, EPA, Census, USGS owners ranked higher)
- Spatial filtering clips results to the geocoded bounding box of your geography
- Up to 500 features per layer (configurable via AGOL query `resultRecordCount`)
