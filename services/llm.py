import os
from typing import Dict, List
from openai import OpenAI

def get_client() -> OpenAI:
    return OpenAI()

def generate_pitch_insight(prospect: Dict, team_assets: List[str]) -> Dict:
    """Return dict with opener, rationale, matching_assets, next_steps using GPT-4o-mini.
    Falls back to heuristics if no OPENAI_API_KEY present."""
    if not os.getenv("OPENAI_API_KEY"):
        company = prospect.get("company","The brand")
        industry = (prospect.get("industry") or "consumer")
        city = prospect.get("hq_city","your market")
        matched = [a for a in team_assets if industry.lower() in a.lower()] or team_assets[:3]
        return {
            "opener": f"We admire {company}'s momentum in {industry}. Let's explore a {company} x Team program in {city}.",
            "rationale": f"{company} seeks relevance and measurable reach. Pair marquee assets with data-backed outcomes to drive KPIs.",
            "matching_assets": matched,
            "next_steps": ["Confirm objectives & KPIs","Share target segments","Align flight & inventory","Draft deal memo"],
        }

    client = get_client()
    prompt = f"""You are a sponsorship strategist. Given a prospect and available team assets, craft:
- A concise opener (1 sentence)
- A rationale (3–5 sentences) mapping the brand's industry to relevant fan/value props
- A shortlist of matching team assets (<=5)
- Next steps (3–5 bullet phrases)

Prospect: {prospect}
Team assets: {team_assets}
Return JSON with keys: opener, rationale, matching_assets, next_steps.
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        messages=[{"role":"user","content":prompt}]
    )
    text = resp.choices[0].message.content
    import json
    try:
        data = json.loads(text)
    except Exception:
        data = {"opener": text.strip(), "rationale": "", "matching_assets": [], "next_steps": []}
    return data
