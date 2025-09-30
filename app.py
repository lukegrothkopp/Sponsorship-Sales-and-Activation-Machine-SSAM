import os
from pathlib import Path
from datetime import datetime
import io
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Optional: if you rely on these elsewhere in your project, keep imports.
# If not, you can comment them out to avoid import errors while iterating.
from services.providers import SponsorUnitedClient, DigiDeckClient, SalesforceClient, DynamicsClient, TableauClient
from services.reasoning import propose_for_prospect, TEAM_ASSETS
from services.storage import build_contract_store
from services.s3store import s3_enabled, upload_bytes, presigned_url

load_dotenv()

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
CURRENT_SEASON = datetime.now().year

# Catalog of assets per partner, organized by category (example data)
PARTNER_ASSETS = {
    "coke": {
        "Digital Media": [
            {"name": "Social series (player Q&A video)", "season": CURRENT_SEASON},
            {"name": "Email newsletter (150k)", "season": CURRENT_SEASON},
            {"name": "In-app trivia game", "season": CURRENT_SEASON},
        ],
        "Signage": [
            {"name": "Branded concession stand", "season": CURRENT_SEASON},
            {"name": "Concourse sampling footprint", "season": CURRENT_SEASON},
        ],
        "Radio": [
            {"name": "30-second play-by-play", "season": CURRENT_SEASON},
        ],
        "Television": [
            {"name": "Broadcast feature segment", "season": CURRENT_SEASON},
        ],
        "LED-Ribbon": [
            {"name": "Marquee LED ribbon", "season": CURRENT_SEASON},
            {"name": "In-bowl LED corners", "season": CURRENT_SEASON},
        ],
        "IP-Use of Marks": [
            {"name": "Jersey Patch Partner", "season": CURRENT_SEASON},
            {"name": "Use of marks in retail", "season": CURRENT_SEASON},
        ],
        "Community Engagement": [
            {"name": "Community clinic", "season": CURRENT_SEASON},
        ],
    },
    "zippay": {
        "Digital Media": [
            {"name": "Season launch splash", "season": CURRENT_SEASON},
        ],
        "Signage": [
            {"name": "Concourse sampling footprint", "season": CURRENT_SEASON},
        ],
        "Radio": [],
        "Television": [],
        "LED-Ribbon": [],
        "IP-Use of Marks": [],
        "Community Engagement": [],
    },
}

PARTNERS = {
    "active": [
        {"id": "coke", "name": "Coke"},
        {"id": "zippay", "name": "ZipPay"},
    ],
    "prospective": [
        {"id": "acme", "name": "Acme Beverages"},
        {"id": "stellar", "name": "Stellar Fitness"},
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
# State helpers (tasks & selection)
# ------------------------
def _ensure_partner_state(pid: str):
    """Initialize session state buckets for a partner."""
    if "tasks" not in st.session_state:
        st.session_state["tasks"] = {}  # tasks[pid] = list of dicts
    if pid not in st.session_state["tasks"]:
        st.session_state["tasks"][pid] = []

    if "asset_sel" not in st.session_state:
        st.session_state["asset_sel"] = {}  # asset_sel[pid][category][asset_name] = bool
    if pid not in st.session_state["asset_sel"]:
        st.session_state["asset_sel"][pid] = {}
        # initialize all assets unchecked
        for cat, items in PARTNER_ASSETS.get(pid, {}).items():
            st.session_state["asset_sel"][pid][cat] = {a["name"]: False for a in items}

def _select_all_assets(pid: str, value: bool = True):
    """Set all checkboxes True/False for displayed partner."""
    _ensure_partner_state(pid)
    for cat in st.session_state["asset_sel"][pid]:
        for name in st.session_state["asset_sel"][pid][cat]:
            st.session_state["asset_sel"][pid][cat][name] = value

def _create_task(pid: str, asset: str, desc: str, specs: str, qty: int, classification: str):
    """Append a new task for a partner."""
    _ensure_partner_state(pid)
    st.session_state["tasks"][pid].append({
        "asset": asset,
        "description": desc,
        "specifications": specs,
        "quantity": qty,
        "type": classification,  # "contracted" or "value added"
        "created": datetime.now().isoformat(timespec="seconds"),
    })

def _export_assets_csv(pid: str) -> bytes:
    """Export current assets (with selection status) to CSV."""
    _ensure_partner_state(pid)
    rows = []
    cats = PARTNER_ASSETS.get(pid, {})
    sel = st.session_state["asset_sel"][pid]
    for cat, items in cats.items():
        for a in items:
            rows.append({
                "partner_id": pid,
                "category": cat,
                "asset": a["name"],
                "season": a.get("season", CURRENT_SEASON),
                "selected": bool(sel.get(cat, {}).get(a["name"], False)),
            })
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode("utf-8")

# ------------------------
# Query params helpers (modern API, dict-like)
# ------------------------
def set_route(page=None, scope=None, partner=None, section=None, replace=False):
    """Update query params; rerun only if something actually changed."""
    qp = st.query_params  # dict-like proxy
    changed = False

    if replace:
        for k in list(qp.keys()):
            del qp[k]
        changed = True

    if page is not None and qp.get("page") != page:
        qp["page"] = page; changed = True
    if scope is not None and qp.get("scope") != scope:
        qp["scope"] = scope; changed = True
    if partner is not None:
        if partner in ("", None):
            if "partner" in qp:
                del qp["partner"]; changed = True
        elif qp.get("partner") != partner:
            qp["partner"] = partner; changed = True
    if section is not None and qp.get("section") != section:
        qp["section"] = section; changed = True

    if changed:
        st.rerun()

# Read current route (strings, not lists)
current_page    = st.query_params.get("page", "Me")
current_scope   = st.query_params.get("scope", "active")
current_partner = st.query_params.get("partner", None)
current_section = st.query_params.get("section", "overview")

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

    # Keep the URL shareable/reflecting current page
    if sel != current_page:
        set_route(page=sel)

    # Partnerships controls (dedented; not under the set_route branch)
    if sel == "Partnerships":
        # scope switcher
        current_scope = st.selectbox(
            "Scope", ["active","prospective"],
            index=0 if current_scope=="active" else 1,
            key="scope_select"
        )

        # brand chooser
        names = [p["name"] for p in PARTNERS[current_scope]]
        ids   = [p["id"]   for p in PARTNERS[current_scope]]
        if names:
            pick = st.selectbox("Open brand page", names, key="brand_select")
            pid  = ids[names.index(pick)]
            if st.button("Open ▶"):
                set_route(page="Partnerships", scope=current_scope, partner=pid, section="overview")

        # Secondary, indented child link for the currently-open brand
        if current_partner:
            p, _ = _partner_by_id(current_partner)
            if p:
                st.markdown(
                    f"<div style='margin-left:10px; color:#bbb;'>↳ <b>{p['name']}</b></div>",
                    unsafe_allow_html=True
                )
                # Optional quick navigation between brand sections (doesn't auto-rerun)
                sections = ["overview","tasks","files","calendar","social","presentations","data"]
                try:
                    sec_idx = sections.index(current_section)
                except ValueError:
                    sec_idx = 0
                sub = st.selectbox("Navigate brand sections", sections, index=sec_idx, key="brand_section_select")
                if st.button("Go"):
                    set_route(page="Partnerships", scope=current_scope, partner=current_partner, section=sub)

# Overwrite route with sidebar choice if user changed it
current_page = sel

# ------------------------
# Page renderers
# ------------------------
def render_me():
    u = _me()
    st.subheader("My Profile")
    col1, col2 = st.columns([0.18, 0.82])
    with col1:
        if u["photo"]:
            st.image(u["photo"])
        else:
            st.image("https://placehold.co/200x200?text=ME")
    with col2:
        st.write(f"**Name:** {u['name']}")
        st.write(f"**Email:** {u['email']}")
        st.write(f"**Role:** {u['role']}")

    st.markdown("---")
    st.subheader("My Partners")
    has_link = False
    for pid in u["partner_ids"]:
        p, scope = _partner_by_id(pid)
        if not p:
            continue
        has_link = True
        if st.button(f"Open {p['name']} ▶", key=f"me_open_{pid}"):
            set_route(page="Partnerships", scope=scope or "active", partner=pid, section="overview")
    if not has_link:
        st.info("No partners assigned yet.")

def render_partnerships():
    # If a partner is chosen (from sidebar or deep link), show its brand page
    if current_partner:
        partner, scope = _partner_by_id(current_partner)
        if not partner:
            st.error("Partner not found."); return
        pid = current_partner
        _ensure_partner_state(pid)

        st.subheader(f"{partner['name']} — {scope.title()} Partnership")

        # Horizontal sub-tabs (no URL sync inside tab bodies to avoid loops)
        tab_labels = ["overview","tasks","files","calendar","social","presentations","data"]
        tabs = st.tabs(tab_labels)

        # --- OVERVIEW ---
        with tabs[0]:
            st.markdown("**Assets in contract (by category)**")
            cats = PARTNER_ASSETS.get(pid, {})
            if cats:
                for cat, items in cats.items():
                    with st.expander(cat, expanded=False):
                        for a in items:
                            st.write(f"• {a['name']}  · Season {a.get('season', CURRENT_SEASON)}")
            else:
                st.caption("No assets listed yet.")
            if st.button("← Back to all partnerships"):
                set_route(page="Partnerships", scope=scope, partner=None, section=None)

        # --- TASKS ---
        with tabs[1]:
            st.markdown("### Tasks")

            # Top action row: + New Task | Export | Select All
            btn_col1, btn_col2, btn_col3 = st.columns([1,1,1])
            with btn_col1:
                new_task_clicked = st.button("+ New Task", use_container_width=True)
            with btn_col2:
                export_clicked = st.button("Export", use_container_width=True)
            with btn_col3:
                select_all_clicked = st.button("Select All", use_container_width=True)

            if select_all_clicked:
                _select_all_assets(pid, value=True)  # button already triggers a rerun

            if export_clicked:
                csv_bytes = _export_assets_csv(pid)
                st.download_button(
                    "Download CSV",
                    data=csv_bytes,
                    file_name=f"{partner['name']}_assets_{CURRENT_SEASON}.csv",
                    mime="text/csv",
                )

            # New Task dialog (modal if available, else inline)
            if new_task_clicked:
                st.session_state["show_new_task_modal"] = True

            if st.session_state.get("show_new_task_modal"):
                if hasattr(st, "experimental_dialog"):
                    @st.experimental_dialog("Create New Task")
                    def _new_task_dialog():
                        cats = PARTNER_ASSETS.get(pid, {})
                        all_assets = [f"{cat} — {a['name']}" for cat, items in cats.items() for a in items]
                        with st.form("new_task_form"):
                            asset_pick = st.selectbox("Asset", all_assets)
                            desc  = st.text_area("Task description")
                            specs = st.text_area("Specifications / production notes")
                            qty   = st.number_input("Quantity", min_value=1, step=1, value=1)
                            classification = st.selectbox("Type", ["contracted","value added"])
                            submitted = st.form_submit_button("Save Task")
                        if submitted:
                            asset_name = asset_pick.split(" — ", 1)[1] if " — " in asset_pick else asset_pick
                            _create_task(pid, asset_name, desc, specs, int(qty), classification)
                            st.session_state["show_new_task_modal"] = False
                            st.rerun()
                    _new_task_dialog()
                else:
                    with st.expander("Create New Task", expanded=True):
                        cats = PARTNER_ASSETS.get(pid, {})
                        all_assets = [f"{cat} — {a['name']}" for cat, items in cats.items() for a in items]
                        with st.form("new_task_form_inline"):
                            asset_pick = st.selectbox("Asset", all_assets, key="nt_asset")
                            desc  = st.text_area("Task description", key="nt_desc")
                            specs = st.text_area("Specifications / production notes", key="nt_specs")
                            qty   = st.number_input("Quantity", min_value=1, step=1, value=1, key="nt_qty")
                            classification = st.selectbox("Type", ["contracted","value added"], key="nt_type")
                            submitted = st.form_submit_button("Save Task")
                        if submitted:
                            asset_name = asset_pick.split(" — ", 1)[1] if " — " in asset_pick else asset_pick
                            _create_task(pid, asset_name, desc, specs, int(qty), classification)
                            st.session_state["show_new_task_modal"] = False
                            st.rerun()

            # Show tasks table (simple)
            existing = st.session_state["tasks"].get(pid, [])
            if existing:
                st.markdown("#### Existing Tasks")
                df = pd.DataFrame(existing)
                st.dataframe(df, use_container_width=True)
            else:
                st.info("No tasks yet.")

            st.markdown("---")
            st.markdown("### Assets (select to include in tasks/export)")
            # Render asset sections with checkboxes and season at right
            cats = PARTNER_ASSETS.get(pid, {})
            for cat, items in cats.items():
                st.markdown(f"**{cat}**")
                for a in items:
                    left, right = st.columns([0.9, 0.1])
                    with left:
                        checked = st.checkbox(
                            a["name"],
                            value=st.session_state["asset_sel"][pid][cat].get(a["name"], False),
                            key=f"chk_{pid}_{cat}_{a['name']}",
                        )
                        st.session_state["asset_sel"][pid][cat][a["name"]] = checked
                    with right:
                        st.markdown(
                            f"<div style='text-align:right;'>Season {a.get('season', CURRENT_SEASON)}</div>",
                            unsafe_allow_html=True,
                        )

        # --- FILES ---
        with tabs[2]:
            st.info("Files (dummy): show S3/Qdrant/Drive links here.")

        # --- CALENDAR ---
        with tabs[3]:
            st.info("Calendar (dummy): game days, activations, deadlines.")

        # --- SOCIAL ---
        with tabs[4]:
            st.info("Social (dummy): planned & delivered posts + metrics.")

        # --- PRESENTATIONS ---
        with tabs[5]:
            st.info("Presentations (dummy): links to DigiDeck/PowerPoints.")

        # --- DATA ---
        with tabs[6]:
            st.info("Data (dummy): Tableau/QBR/engagement KPIs.")
        return

    # No partner selected — grid of buttons for Active/Prospective
    st.subheader("Active Partnerships")
    cols = st.columns(3)
    for i, p in enumerate(PARTNERS["active"]):
        with cols[i % 3]:
            if st.button(p["name"], key=f"act_{p['id']}"):
                set_route(page="Partnerships", scope="active", partner=p["id"], section="overview")

    st.markdown("---")
    st.subheader("Prospective Partnerships")
    cols = st.columns(3)
    for i, p in enumerate(PARTNERS["prospective"]):
        with cols[i % 3]:
            if st.button(p["name"], key=f"pros_{p['id']}"):
                set_route(page="Partnerships", scope="prospective", partner=p["id"], section="overview")

# ------------------------
# Temporary placeholders for other pages (avoid NameError)
# ------------------------
def render_prospecting():
    st.subheader("Prospecting")
    st.info("Prospecting page (coming soon).")

def render_selling():
    st.subheader("Selling")
    st.info("Selling page (coming soon).")

def render_reports():
    st.subheader("Reports")
    st.info("Reports (Proof-of-Performance) page (coming soon).")

def render_users():
    st.subheader("Users")
    st.info("User-to-partnership mapping (coming soon).")

def render_presentations():
    st.subheader("Presentations")
    st.info("Links to DigiDeck / pitch decks (coming soon).")

def render_files():
    st.subheader("Files")
    st.info("Central file space (S3/Drive) (coming soon).")

def render_contracts():
    st.subheader("Contracts & Terms — Q&A")
    st.info("Contract Q&A and document search (coming soon).")

def render_data():
    st.subheader("Data")
    st.info("3rd-party and internal data dashboards (coming soon).")

def render_settings():
    st.subheader("Settings")
    st.caption("Provider selection will go here.")
    st.selectbox("Vector DB provider", ["pinecone","qdrant","chroma"], index=0)

# ------------------------
# Router
# ------------------------
if   current_page == "Me":             render_me()
elif current_page == "Partnerships":   render_partnerships()
elif current_page == "Prospecting":    render_prospecting()
elif current_page == "Selling":        render_selling()
elif current_page == "Reports":        render_reports()
elif current_page == "Users":          render_users()
elif current_page == "Presentations":  render_presentations()
elif current_page == "Files":          render_files()
elif current_page == "Contracts":      render_contracts()
elif current_page == "Data":           render_data()
else:                                  render_settings()
