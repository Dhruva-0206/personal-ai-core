"""
Central configuration. Everything is read from environment variables so
real credentials never live in code. Copy .env.example to .env and fill it
in before running anything for real.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default)


# --- Nebius Token Factory ---
# Token Factory exposes an OpenAI-compatible endpoint, so we reuse the
# OpenAI SDK with a custom base_url rather than a bespoke client.
NEBIUS_API_KEY = _get("NEBIUS_API_KEY")
NEBIUS_BASE_URL = _get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")

# Text extraction model. Nemotron models on Token Factory currently run in
# reasoning mode by default and return their answer in `reasoning_content`
# instead of `content` — llm_client.py handles both, but if a non-reasoning
# variant becomes available/preferred, swap the model id here.
EXTRACTION_MODEL = _get("EXTRACTION_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")

# Embedding model for entity-resolution similarity and (later) retrieval.
EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B")

# --- Neo4j (graph memory) ---
NEO4J_URI = _get("NEO4J_URI")
NEO4J_USER = _get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _get("NEO4J_PASSWORD")

# --- Entity resolution / scoring knobs ---
# Adopted as sensible starting points based on reference architecture we
# reviewed — not sacred, tune freely as we test against real data.
ENTITY_SIMILARITY_THRESHOLD = float(_get("ENTITY_SIMILARITY_THRESHOLD", "0.93"))
ENTITY_SUBSTRING_MIN_LEN = int(_get("ENTITY_SUBSTRING_MIN_LEN", "3"))

# Same idea as entity resolution, but for attribute names on a single
# entity's own state history (e.g. "city" vs "location"). 0.72 chosen from
# two data points — city/location (should-merge) scored 0.8034, city/job
# (should-NOT-merge) scored 0.6509. This sits closer to the midpoint of
# that gap rather than hugging the merge case, since we only have two
# samples and false negatives (missed synonyms) are cheaper to catch
# later than silent false merges. Revisit once more real attribute pairs
# accumulate from actual usage.
ATTRIBUTE_SIMILARITY_THRESHOLD = float(_get("ATTRIBUTE_SIMILARITY_THRESHOLD", "0.72"))
ATTRIBUTE_SUBSTRING_MIN_LEN = int(_get("ATTRIBUTE_SUBSTRING_MIN_LEN", "3"))

WEIGHT_SIMILARITY = float(_get("WEIGHT_SIMILARITY", "0.55"))
WEIGHT_IMPORTANCE = float(_get("WEIGHT_IMPORTANCE", "0.35"))
WEIGHT_RECENCY = float(_get("WEIGHT_RECENCY", "0.10"))
IMPORTANCE_LANDMARK_FLOOR = float(_get("IMPORTANCE_LANDMARK_FLOOR", "0.75"))
RECENCY_HALF_LIFE_DAYS = float(_get("RECENCY_HALF_LIFE_DAYS", "365"))

# Multiplier applied to an episode's combined_score if every State it wrote
# has since been superseded — a guessed starting value (0.5 = half credit),
# not calibrated. The point isn't to hide superseded episodes (they should
# still surface for historical questions via other lanes later) but to
# stop a fully-outdated fact from outranking the current one purely on
# text similarity. Revisit once real usage shows whether 0.5 is too harsh
# or too weak.
OUTDATED_STATE_PENALTY = float(_get("OUTDATED_STATE_PENALTY", "0.5"))

# Not currently applied as a filter — tested and reverted. At small corpus
# sizes (~4 test episodes), genuine-match similarity (~0.46) and
# noise-floor similarity for unrelated queries (~0.43-0.47) overlapped too
# much for any single cutoff to separate cleanly (see CLAUDE.md). Revisit
# once a real corpus with diverse content gives embeddings more natural
# separation to calibrate against.
VECTOR_MIN_SIMILARITY = float(_get("VECTOR_MIN_SIMILARITY", "0.50"))

# Full-text lane: raw Lucene scores from the episode_raw_text index are
# min-max normalized into this range so they're comparable to the vector
# lane's 0-1ish combined_score. Both bounds are guessed, not calibrated.
FULLTEXT_SCORE_MIN = float(_get("FULLTEXT_SCORE_MIN", "0.40"))
FULLTEXT_SCORE_MAX = float(_get("FULLTEXT_SCORE_MAX", "0.75"))

# Recency safety-net lane: guarantees something-you-just-said is always
# retrievable even if it scores poorly on similarity/fulltext. Mirrors the
# reference architecture we studied but is unvalidated for our own usage
# patterns — guessed starting values.
RECENT_WINDOW_MINUTES = int(_get("RECENT_WINDOW_MINUTES", "5"))
RECENT_LANE_SCORE = float(_get("RECENT_LANE_SCORE", "0.25"))
RECENT_LANE_MAX = int(_get("RECENT_LANE_MAX", "2"))

# Namespace: every graph write/read is scoped to a speaker id so multiple
# people (or test runs) never bleed into each other's memory.
DEFAULT_SPEAKER = _get("DEFAULT_SPEAKER", "default")
