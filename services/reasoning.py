from typing import Dict
from .llm import generate_pitch_insight

# Lightweight catalog of example team assets used to match prospects
TEAM_ASSETS = [
    "Marquee LED ribbon : high-reach, in-bowl visibility",
    "Social series : player Q&A (video)",
    "Email newsletter : 150k subscribers",
    "In-app game : trivia with coupon unlock",
    "Concourse activation : sampling footprint",
    "Community clinic : youth engagement",
]

def propose_for_prospect(prospect: Dict) -> Dict:
    """
    Calls the LLM helper to produce:
      - opener (1 sentence)
      - rationale (3–5 sentences)
      - matching_assets (<=5)
      - next_steps (3–5 bullets)
    Falls back to a simple heuristic if no OPENAI_API_KEY is set.
    """
    return generate_pitch_insight(prospect, TEAM_ASSETS)
