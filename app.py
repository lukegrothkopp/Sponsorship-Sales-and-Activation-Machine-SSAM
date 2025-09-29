import os
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

from services.providers import SponsorUnitedClient, DigiDeckClient, SalesforceClient, DynamicsClient, TableauClient
from services.reasoning import propose_for_prospect, TEAM_ASSETS
from services.storage import build_contract_store
from services.s3store import s3_enabled, upload_bytes, presigned_url

ASSETS = Path(__file__).parent / "assets"
FAVICON = ASSETS / "favicon.png"        # tab icon
HEADER_LOGO = ASSETS / "SSAM_Logo.png"  # page header logo

st.set_page_config(
    page_title="Sponsorship Sales & Activation Machine",
    page_icon=str(FAVICON) if FAVICON.exists() else "🏟️",
    layout="wide",
)

# ------------------------
# Simple in-app directory
# ------------------------
PARTNERS = {
    "active": [
        {"id": "coke", "name": "Coke", "assets": [
            "Marquee LED ribbon", "Social series (player Q&A video)",
            "Email newsletter (150k)", "In-app trivia game", "Community clinic"
        ]},
        {"id": "zippay", "name": "ZipPay", "assets": [
            "Concourse sampling footprint", "Season launch splash",
            "In-bowl LED corners"
        ]},
    ],
    "prospective": [
        {"id": "acme", "name": "Acme Beverages", "assets": []},
        {"id": "stellar", "name": "Stellar Fitness", "assets": []},
    ],
}

def _partner_by_id(pid):
    for scope in ("active", "prospective"):
        for p in PARTNERS[scope]:
            if p["id"] == pid:
                return p, scope
    return None, None

def _me():
    # Pull from secrets if provided, otherwise sensible defaults
    return {
        "name": st.secrets.get("USER_NAME", "Your Name"),
        "email": st.secrets.get("USER_EMAIL", "you@example.com"),
        "role": st.secrets.get("USER_ROLE", "Account Exec"),
        # Comma-separated partner ids in secrets (e.g., "coke,zippay")
        "partner_ids": [s.strip() for s in st.secrets.get("USER_PARTNERS", "coke").split(",") if s.strip()],
        "photo": st.secrets.get("USER_PHOTO_URL", None),
    }

# ------------------------
# Query params helpers (use experimental_* which returns lists)
# ------------------------
def set_route(page=None, scope=None, partner=None, section=None):
    params = st.experimental_get_query_params()
    if page is not None:    params["page"]    = [page]
    if scope is not None:   params["scope"]   = [scope]
    if partner is not None: params["partner"] = [partner]
    if section is not None: params["section"] = [section]
    st.experimental_set_query_params(**params)
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()

params = st.experimental_get_query_params()
current_page    = params.get("page",    ["Me"])[0]
current_scope   = params.get("scope",   ["active"])[0]
current_partner = params.get("partner", [None])[0]
current_section = params.get("section", ["overview"])[0]

# ------------------------
# Sidebar (navigation) — SINGLE source of truth
# ------------------------
with st.sidebar:
    if HEADER_LOGO.exists():
        st.image(str(HEADER_LOGO), width=96)

    PAGES = [
        "Me","Partnerships","Prospecting","Selling","Reports",
        "Users","Presentations","Files","Contracts","Data","Settings"
    ]
    try:
        start_idx = PAGES.index(current_page)
    except ValueError:
        start_idx = 0

    sel = st.radio("Navigate", PAGES, index=start_idx, key="nav_radio")

    # Partnerships sidebar controls (scope + quick open)
    if sel == "Partnerships":
        current_scope = st.selectbox("Scope", ["active","prospective"],
                                     index=0 if current_scope=="active" else 1, key="scope_select")
        names = [p["name"] for p in PARTNERS]()
