# app.py
import os
import json
import io
from pathlib import Path
from datetime import datetime
import uuid
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Optional external services — safe to comment if not present
try:
    from services.providers import SponsorUnitedClient, DigiDeckClient, SalesforceClient, DynamicsClient, TableauClient  # noqa
except Exception:
    SponsorUnitedClient = DigiDeckClient = SalesforceClient = DynamicsClient = TableauClient = None  # stubs

try:
    from services.reasoning import propose_for_prospect, TEAM_ASSETS  # noqa
except Exception:
    def propose_for_prospect(_): return {"opener": "", "rationale": "", "matching_assets": [], "next_steps": []}
    TEAM_ASSETS = ["LED ribbon", "Social post", "Player appearance"]

try:
    from services.storage import build_contract_store  # noqa
except Exception:
    def build_contract_store(*_, **__): return (None, 0, "chroma")

try:
    from services.s3store import s3_enabled, upload_bytes, presigned_url, download_bytes  # noqa
except Exception:
    def s3_enabled(): return False
    def upload_bytes(*_, **__): return False
    def presigned_url(*_, **__): return None
    def download_bytes(*_, **__): return None

load_dotenv()

# ------------------------
# App chrome
# ------------------------
ASSETS = Path(__file__).parent / "assets"
FAVICON = ASSETS / "favicon.png"        # tab icon
HEADER_LOGO = ASSETS / "SSAM_Logo.png"  # page header logo

st.set_page_config(
    page_title="Sponsorship Sales & Activation Machine",
    page_icon=str(FAVICON) if FAVICON.exists() else "🏟️",
    layout="wide",
)

def apply_theme():
    theme = st.session_state.get("ui_theme", "dark")
    ACCENT = "#16A34A"  # green
    TAB = ACCENT

    if theme == "dark":
        st.markdown(f"""
        <style>
        .stApp {{ background:#0E1117; color:#FAFAFA; }}
        section[data-testid="stSidebar"] {{ background:#161A23; }}
        div[role="tablist"] button[aria-selected="true"] {{ border-bottom:2px solid {TAB}; }}

        /* Make all buttons green */
        .stButton>button,
        .stDownloadButton>button,
        .stFormSubmitButton>button {{
            background:{ACCENT}; color:#0E1117; border:0;
        }}
        .stButton>button:hover,
        .stDownloadButton>button:hover,
        .stFormSubmitButton>button:hover {{
            filter:brightness(1.05);
        }}
        </style>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <style>
        div[role="tablist"] button[aria-selected="true"] {{ border-bottom:2px solid {TAB}; }}

        .stButton>button,
        .stDownloadButton>button,
        .stFormSubmitButton>button {{
            background:{ACCENT}; color:white; border:0;
        }}
        .stButton>button:hover,
        .stDownloadButton>button:hover,
        .stFormSubmitButton>button:hover {{
            filter:brightness(0.95);
        }}
        </style>
        """, unsafe_allow_html=True)

# ------------------------
# Constants & data model
# ------------------------
CURRENT_SEASON = datetime.now().year
DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)

# Example asset catalogs per partner (can be replaced via upload)
DEFAULT_ASSETS = {
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
        "Digital Media": [{"name": "Season launch splash", "season": CURRENT_SEASON}],
        "Signage": [{"name": "Concourse sampling footprint", "season": CURRENT_SEASON}],
        "Radio": [], "Television": [], "LED-Ribbon": [], "IP-Use of Marks": [], "Community Engagement": [],
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

# ------------------------
# Helpers (routing, ids, storage)
# ------------------------
def slug(s: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in s).strip("-")

def _partner_by_id(pid):
    for scope in ("active", "prospective"):
        for p in PARTNERS[scope]:
            if p["id"] == pid:
                return p, scope
    return None, None

def my_profile():
    return profile()

def set_route(page=None, scope=None, partner=None, section=None, replace=False):
    """Update query params; rerun only on real change (prevents loops)."""
    qp = st.query_params
    changed = False
    if replace:
        for k in list(qp.keys()): del qp[k]
        changed = True
    if page is not None and qp.get("page") != page: qp["page"] = page; changed = True
    if scope is not None and qp.get("scope") != scope: qp["scope"] = scope; changed = True
    if partner is not None:
        if partner in ("", None):
            if "partner" in qp: del qp["partner"]; changed = True
        elif qp.get("partner") != partner: qp["partner"] = partner; changed = True
    if section is not None and qp.get("section") != section: qp["section"] = section; changed = True
    if changed: st.rerun()

# Query state
current_page    = st.query_params.get("page", "Me")
current_scope   = st.query_params.get("scope", "active")
current_partner = st.query_params.get("partner", None)
current_section = st.query_params.get("section", "overview")

# ------------------------
# Persistence: local JSON with optional S3
# ------------------------
def _local_path(kind: str, pid: str) -> Path:
    # kind: "tasks" or "assets"
    return DATA_DIR / f"{kind}_{pid}.json"

def _s3_key(kind: str, pid: str) -> str:
    return f"ssam/{kind}/{pid}.json"

def save_json(kind: str, pid: str, data: dict | list) -> None:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    # write local
    _local_path(kind, pid).write_bytes(payload)
    # optional S3
    if s3_enabled():
        upload_bytes(_s3_key(kind, pid), payload, content_type="application/json")

def load_json(kind: str, pid: str, default):
    # Try S3 first
    if s3_enabled():
        blob = download_bytes(_s3_key(kind, pid))
        if blob:
            try:
                return json.loads(blob.decode("utf-8"))
            except Exception:
                pass
    # Fallback local
    lp = _local_path(kind, pid)
    if lp.exists():
        try:
            return json.loads(lp.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

# ------------------------
# Profile persistence
# ------------------------
PROFILE_LOCAL = DATA_DIR / "profile.json"
PROFILE_S3 = "ssam/profile/profile.json"

DEFAULT_PROFILE = {
    "name": st.secrets.get("USER_NAME", "Your Name"),
    "email": st.secrets.get("USER_EMAIL", "you@example.com"),
    "role": st.secrets.get("USER_ROLE", "AE"),  # AE, Admin, Brand, Agency
    "partner_ids": [s.strip() for s in st.secrets.get("USER_PARTNERS", "coke").split(",") if s.strip()],
    "photo": st.secrets.get("USER_PHOTO_URL", None),
}

def save_profile(profile: dict):
    payload = json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8")
    PROFILE_LOCAL.write_bytes(payload)
    if s3_enabled():
        upload_bytes(PROFILE_S3, payload, content_type="application/json")

def load_profile() -> dict:
    # Try S3 first
    if s3_enabled():
        blob = download_bytes(PROFILE_S3)
        if blob:
            try:
                return json.loads(blob.decode("utf-8"))
            except Exception:
                pass
    # Fallback local
    if PROFILE_LOCAL.exists():
        try:
            return json.loads(PROFILE_LOCAL.read_text(encoding="utf-8"))
        except Exception:
            return DEFAULT_PROFILE
    # Default
    return DEFAULT_PROFILE

def profile() -> dict:
    """Cached profile in session_state."""
    if "profile" not in st.session_state:
        st.session_state["profile"] = load_profile()
    return st.session_state["profile"]

# ------------------------
# Session state buckets
# ------------------------
def load_assets_for(pid: str):
    if "assets" not in st.session_state:
        st.session_state["assets"] = {}
    if pid not in st.session_state["assets"]:
        # load from storage or default
        data = load_json("assets", pid, DEFAULT_ASSETS.get(pid, {}))
        st.session_state["assets"][pid] = data

def ensure_partner_state(pid: str):
    if "tasks" not in st.session_state:
        st.session_state["tasks"] = {}  # tasks[pid] = list of dicts
    if pid not in st.session_state["tasks"]:
        task_list = load_json("tasks", pid, [])
        st.session_state["tasks"][pid] = task_list

    if "asset_sel" not in st.session_state:
        st.session_state["asset_sel"] = {}
    if pid not in st.session_state["asset_sel"]:
        load_assets_for(pid)
        st.session_state["asset_sel"][pid] = {}
        for cat, items in st.session_state["assets"][pid].items():
            st.session_state["asset_sel"][pid][cat] = {a["name"]: False for a in items}

def save_tasks(pid: str):
    save_json("tasks", pid, st.session_state["tasks"][pid])

def save_assets(pid: str):
    save_json("assets", pid, st.session_state["assets"][pid])

# Task operations
def new_task(pid: str, asset: str, desc: str, specs: str, qty: int, classification: str, assignee: str | None):
    ensure_partner_state(pid)
    st.session_state["tasks"][pid].append({
        "id": str(uuid.uuid4()),
        "asset": asset,
        "description": desc,
        "specifications": specs,
        "quantity": int(qty),
        "type": classification,     # "contracted" or "value added"
        "assignee": assignee or "",
        "status": "open",           # open / complete
        "created": datetime.now().isoformat(timespec="seconds"),
    })
    save_tasks(pid)

def update_task(pid: str, task_id: str, **fields):
    ensure_partner_state(pid)
    for t in st.session_state["tasks"][pid]:
        if t["id"] == task_id:
            t.update({k: v for k, v in fields.items() if v is not None})
            break
    save_tasks(pid)

def delete_tasks(pid: str, ids: list[str]):
    ensure_partner_state(pid)
    st.session_state["tasks"][pid] = [t for t in st.session_state["tasks"][pid] if t["id"] not in ids]
    save_tasks(pid)

# Export helpers
def export_assets_csv(pid: str) -> bytes:
    load_assets_for(pid); ensure_partner_state(pid)
    rows = []
    cats = st.session_state["assets"][pid]
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

def export_tasks_xlsx(pid: str) -> bytes | None:
    try:
        import openpyxl  # noqa
    except Exception:
        return None
    ensure_partner_state(pid)
    df = pd.DataFrame(st.session_state["tasks"][pid])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Tasks")
    buf.seek(0)
    return buf.getvalue()

# ------------------------
# Sidebar (navigation)
# ------------------------
with st.sidebar:
    if HEADER_LOGO.exists():
        st.image(str(HEADER_LOGO), width=96)

    # Theme toggle
    st.selectbox("Theme", ["dark", "light"], index=0 if st.session_state.get("ui_theme","dark")=="dark" else 1,
                 key="ui_theme", on_change=apply_theme)

    PAGES = ["Me","Partnerships","Prospecting","Selling","Reports",
             "Users","Presentations","Files","Contracts","Data","Settings"]

    try:
        start_idx = PAGES.index(current_page)
    except ValueError:
        start_idx = 0

    sel = st.radio("Navigate", PAGES, index=start_idx, key="nav_radio")
    if sel != current_page: set_route(page=sel)

    if sel == "Partnerships":
        current_scope = st.selectbox("Scope", ["active","prospective"],
                                     index=0 if current_scope=="active" else 1, key="scope_select")

        names = [p["name"] for p in PARTNERS[current_scope]]
        ids   = [p["id"]   for p in PARTNERS[current_scope]]
        if names:
            pick = st.selectbox("Open brand page", names, key="brand_select")
            pid  = ids[names.index(pick)]
            if st.button("Open ▶"):
                set_route(page="Partnerships", scope=current_scope, partner=pid, section="overview")

        if current_partner:
            p, _ = _partner_by_id(current_partner)
            if p:
                st.markdown(f"<div style='margin-left:10px; color:#bbb;'>↳ <b>{p['name']}</b></div>", unsafe_allow_html=True)
                sections = ["overview","tasks","files","calendar","social","presentations","data"]
                try: sec_idx = sections.index(current_section)
                except ValueError: sec_idx = 0
                sub = st.selectbox("Brand section", sections, index=sec_idx, key="brand_section_select")
                if st.button("Go"):
                    set_route(page="Partnerships", scope=current_scope, partner=current_partner, section=sub)

current_page = sel

# ------------------------
# UI bits
# ------------------------
def breadcrumb(*parts):
    st.caption(" › ".join(parts))

def toast(msg, kind="info"):
    if hasattr(st, "toast"):
        st.toast(msg)
    else:
        (st.success if kind=="success" else st.info)(msg)

# ------------------------
# Pages
# ------------------------
def render_me():
    p = profile()

    st.subheader("My Profile")
    col1, col2 = st.columns([0.18, 0.82])
    with col1:
        if p.get("photo"):
            st.image(p["photo"])
        else:
            st.image("https://placehold.co/200x200?text=ME")
    with col2:
        st.write(f"**Name:** {p['name']}")
        st.write(f"**Email:** {p['email']}")
        st.write(f"**Role:** {p['role']}")

    # ---------- Edit Profile ----------
    with st.expander("Edit profile", expanded=False):
        # Build partner choices from your PARTNERS dictionary
        all_partner_ids = []
        id_to_label = {}
        for scope in ("active", "prospective"):
            for item in PARTNERS.get(scope, []):
                all_partner_ids.append(item["id"])
                id_to_label[item["id"]] = f"{item['name']} ({scope})"

        default_ids = [pid for pid in p.get("partner_ids", []) if pid in all_partner_ids]

        with st.form("edit_profile_form", clear_on_submit=False):
            name = st.text_input("Name", value=p.get("name",""))
            email = st.text_input("Email", value=p.get("email",""))
            role = st.selectbox("Role", ["AE", "Admin", "Brand", "Agency"],
                                index=["AE","Admin","Brand","Agency"].index(p.get("role","AE")) if p.get("role","AE") in ["AE","Admin","Brand","Agency"] else 0)

            # show labels in UI; store ids
            pick_labels = [id_to_label[pid] for pid in default_ids]
            all_labels = [id_to_label[pid] for pid in all_partner_ids]
            chosen_labels = st.multiselect("Partners you work with", options=all_labels, default=pick_labels)
            chosen_ids = []
            # map back to ids
            label_to_id = {v:k for k,v in id_to_label.items()}
            for lab in chosen_labels:
                if lab in label_to_id:
                    chosen_ids.append(label_to_id[lab])

            photo_url = st.text_input("Photo URL (optional)", value=p.get("photo","") or "")
            photo_file = st.file_uploader("Or upload a new photo", type=["png","jpg","jpeg"])

            submitted = st.form_submit_button("Save changes")

        if submitted:
            # If a photo file is uploaded, save locally (or push to S3)
            final_photo = photo_url.strip()
            if photo_file is not None:
                Path("uploads").mkdir(exist_ok=True)
                dest = Path("uploads") / f"profile_{slug(name)}_{photo_file.name}"
                with open(dest, "wb") as f:
                    f.write(photo_file.getbuffer())
                final_photo = str(dest)
                # Optional S3
                if s3_enabled():
                    key = f"ssam/photos/{dest.name}"
                    if upload_bytes(key, photo_file.getbuffer(), content_type=photo_file.type):
                        s3_url = presigned_url(key)
                        if s3_url:
                            final_photo = s3_url

            updated = {
                "name": name.strip() or p["name"],
                "email": email.strip() or p["email"],
                "role": role,
                "partner_ids": chosen_ids or [],
                "photo": final_photo or None,
            }
            # Persist & refresh session profile
            save_profile(updated)
            st.session_state["profile"] = updated
            st.success("Profile updated.")
            st.rerun()

    st.markdown("---")
    st.subheader("My Partners")
    any_btn = False
    for pid in p.get("partner_ids", []):
        pr, scope = _partner_by_id(pid)
        if not pr:
            continue
        any_btn = True
        if st.button(f"Open {pr['name']} ▶", key=f"me_open_{pid}"):
            set_route(page="Partnerships", scope=scope or "active", partner=pid, section="overview")
    if not any_btn:
        st.caption("No partners assigned yet.")

    # ---------- My Tasks ----------
    st.markdown("---")
    st.subheader("My Tasks")
    my_email = p.get("email","").lower()
    rows = []
    for scope in PARTNERS:
        for pr in PARTNERS[scope]:
            pid = pr["id"]; ensure_partner_state(pid)
            for t in st.session_state["tasks"][pid]:
                if t.get("assignee","").lower() == my_email and my_email:
                    rows.append({"partner": pr["name"], **t})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.caption("No tasks assigned to you yet.")

    # My Tasks (aggregated)
    st.markdown("---")
    st.subheader("My Tasks")
    my_ids = []
    for scope in PARTNERS:
        for p in PARTNERS[scope]:
            pid = p["id"]; ensure_partner_state(pid)
            for t in st.session_state["tasks"][pid]:
                if t.get("assignee","").lower() == u["email"].lower():
                    my_ids.append((pid, p["name"], t))
    if not my_ids:
        st.caption("No tasks assigned to you yet.")
    else:
        rows = [{"partner": name, **t} for (_, name, t) in my_ids]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

def _brand_tabs(pid: str, partner_name: str, scope: str):
    """The brand page with tabs and full Tasks UX."""
    role = profile().get("role", "AE").lower()
    can_edit = role in ("ae", "admin")
            # --- Create New Task (robust inline UX, no dialogs) ---
    # Show a disabled button + help if user can't edit
    if not can_edit:
        st.button("+ New Task", use_container_width=True, key=f"newtask_{pid}_disabled", disabled=True,
                      help="Only AE/Admin can create tasks.")
    else:
        if st.button("+ New Task", use_container_width=True, key=f"newtask_{pid}"):
            st.session_state["new_task_for"] = pid
            st.rerun()

    # Render the inline create form when this partner is active
    if st.session_state.get("new_task_for") == pid and can_edit:
        cats = st.session_state["assets"][pid]
        all_assets = [f"{cat} — {a['name']}" for cat, items in cats.items() for a in items]

        with st.form(f"new_task_form_{pid}", clear_on_submit=False):
            asset_pick = st.selectbox("Asset", all_assets, key=f"nt_asset_{pid}")
            desc  = st.text_area("Task description", key=f"nt_desc_{pid}")
            specs = st.text_area("Specifications / production notes", key=f"nt_specs_{pid}")
            qty   = st.number_input("Quantity", min_value=1, step=1, value=1, key=f"nt_qty_{pid}")
            classification = st.selectbox("Type", ["contracted","value added"], key=f"nt_type_{pid}")
            assignee = st.text_input("Assign to (email)", value=profile().get("email",""), key=f"nt_asg_{pid}")

            colA, colB = st.columns([1,1])
            with colA:
                save_clicked = st.form_submit_button("Save Task")
            with colB:
                    cancel_clicked = st.form_submit_button("Cancel")

            if save_clicked:
                asset_name = asset_pick.split(" — ", 1)[1] if " — " in asset_pick else asset_pick
                new_task(pid, asset_name, desc, specs, int(qty), classification, assignee)
                st.session_state.pop("new_task_for", None)
                toast("Task created.", "success")
                st.rerun()

            if cancel_clicked:
                st.session_state.pop("new_task_for", None)
                st.rerun()

    load_assets_for(pid)
    ensure_partner_state(pid)

    st.subheader(f"{partner_name} — {scope.title()} Partnership")
    breadcrumb("Partnerships", partner_name, current_section.title())

    tabs = st.tabs(["overview","tasks","files","calendar","social","presentations","data"])
    # OVERVIEW
    with tabs[0]:
        st.markdown("**Assets in contract (by category)**")
        cats = st.session_state["assets"][pid]
        if cats:
            for cat, items in cats.items():
                with st.expander(cat, expanded=False):
                    for a in items:
                        st.write(f"• {a['name']}  ·  Season {a.get('season', CURRENT_SEASON)}")
        else:
            st.caption("No assets listed yet.")
        if st.button("← Back to all partnerships"):
            set_route(page="Partnerships", scope=scope, partner=None, section=None)

    # TASKS
    with tabs[1]:
        st.markdown("### Tasks")

        # Top actions
        c1, c2, c3, c4 = st.columns([1,1,1,1])
        with c1:
            create_clicked = st.button("+ New Task", use_container_width=True, key=f"newtask_{pid}")
        with c2:
            export_csv_clicked = st.button("Export Assets (CSV)", use_container_width=True, key=f"expassets_{pid}")
        with c3:
            export_xlsx_clicked = st.button("Export Tasks (Excel)", use_container_width=True, key=f"exptasks_{pid}")
        with c4:
            select_all_clicked = st.button("Select All Assets", use_container_width=True, key=f"selall_{pid}")

        role = profile().get("role", "AE").lower()
        can_edit = role in ("ae", "admin")

        # Bulk select assets
        if select_all_clicked:
            for cat in st.session_state["asset_sel"][pid]:
                for name in st.session_state["asset_sel"][pid][cat]:
                    st.session_state["asset_sel"][pid][cat][name] = True
            toast("All assets selected.", "success")

        # Export assets CSV
        if export_csv_clicked:
            csv_bytes = export_assets_csv(pid)
            st.download_button("Download CSV",
                               data=csv_bytes,
                               file_name=f"{partner_name}_assets_{CURRENT_SEASON}.csv",
                               mime="text/csv",
                               key=f"dl_assets_{pid}")

        # Export tasks Excel (if available)
        if export_xlsx_clicked:
            xbytes = export_tasks_xlsx(pid)
            if xbytes:
                st.download_button("Download Tasks.xlsx",
                                   data=xbytes,
                                   file_name=f"{partner_name}_tasks.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   key=f"dl_tasks_{pid}")
            else:
                st.warning("Excel export requires `openpyxl`. Falling back to CSV below.")
                df = pd.DataFrame(st.session_state["tasks"][pid])
                st.download_button("Download Tasks.csv",
                                   data=df.to_csv(index=False).encode("utf-8"),
                                   file_name=f"{partner_name}_tasks.csv",
                                   mime="text/csv",
                                   key=f"dl_tasks_csv_{pid}")

        # Create New Task
        if create_clicked and can_edit:
            st.session_state["show_new_task_modal"] = True

        if st.session_state.get("show_new_task_modal", False) and can_edit:
            cats = st.session_state["assets"][pid]
            all_assets = [f"{cat} — {a['name']}" for cat, items in cats.items() for a in items]

            def task_form(form_key: str):
                asset_pick = st.selectbox("Asset", all_assets, key=f"{form_key}_asset")
                desc  = st.text_area("Task description", key=f"{form_key}_desc")
                specs = st.text_area("Specifications / production notes", key=f"{form_key}_specs")
                qty   = st.number_input("Quantity", min_value=1, step=1, value=1, key=f"{form_key}_qty")
                classification = st.selectbox("Type", ["contracted","value added"], key=f"{form_key}_type")
                assignee = st.text_input("Assign to (email)", key=f"{form_key}_assignee")
                submitted = st.form_submit_button("Save Task")
                return submitted, asset_pick, desc, specs, qty, classification, assignee

            if hasattr(st, "experimental_dialog"):
                @st.experimental_dialog("Create New Task")
                def _new_task_dialog():
                    with st.form("new_task_form"):
                        submitted, asset_pick, desc, specs, qty, classification, assignee = task_form("nt")
                    if submitted:
                        asset_name = asset_pick.split(" — ", 1)[1] if " — " in asset_pick else asset_pick
                        new_task(pid, asset_name, desc, specs, qty, classification, assignee)
                        st.session_state["show_new_task_modal"] = False
                        toast("Task created.", "success")
                        st.rerun()
                _new_task_dialog()
            else:
                with st.expander("Create New Task", expanded=True):
                    with st.form("new_task_form_inline"):
                        submitted, asset_pick, desc, specs, qty, classification, assignee = task_form("nt2")
                    if submitted:
                        asset_name = asset_pick.split(" — ", 1)[1] if " — " in asset_pick else asset_pick
                        new_task(pid, asset_name, desc, specs, qty, classification, assignee)
                        st.session_state["show_new_task_modal"] = False
                        toast("Task created.", "success")
                        st.rerun()

        # Filters
        st.markdown("#### Filters")
        cats = st.session_state["assets"][pid]
        all_categories = list(cats.keys())
        fcol1, fcol2, fcol3 = st.columns([1,1,1])
        with fcol1:
            pick_cats = st.multiselect("Category", all_categories, default=all_categories, key=f"fcat_{pid}")
        with fcol2:
            pick_types = st.multiselect("Type", ["contracted","value added"], default=["contracted","value added"],
                                        key=f"ftype_{pid}")
        with fcol3:
            seasons = sorted({a.get("season", CURRENT_SEASON) for items in cats.values() for a in items})
            pick_season = st.selectbox("Season", seasons, index=len(seasons)-1, key=f"fseason_{pid}")

        # Tasks list with selection + per-row actions
        st.markdown("#### Task List")
        tasks = [t for t in st.session_state["tasks"][pid]
                 if t["type"] in pick_types]
        # Per-row selection
        sel_ids = []
        for t in tasks:
            row = st.container(border=True)
            with row:
                csel, cinfo, cbtns = st.columns([0.1, 0.7, 0.2])
                with csel:
                    checked = st.checkbox("", key=f"taskchk_{pid}_{t['id']}")
                    if checked: sel_ids.append(t["id"])
                with cinfo:
                    st.markdown(f"**{t['asset']}** · _{t['type']}_ · **Qty:** {t['quantity']} · **Assignee:** {t.get('assignee','') or '—'}")
                    st.caption(t.get("description",""))
                    st.caption(f"Specs: {t.get('specifications','')}")
                    st.caption(f"Status: {t.get('status','open')} · Created: {t.get('created','')}")
                with cbtns:
                    if can_edit and st.button("Edit", key=f"edit_{pid}_{t['id']}"):
                        st.session_state["edit_task_id"] = t["id"]
                    if can_edit and st.button("Delete", key=f"del_{pid}_{t['id']}"):
                        delete_tasks(pid, [t["id"]]); toast("Task deleted.", "success"); st.rerun()

        # Edit dialog for one task
        edit_id = st.session_state.get("edit_task_id")
        if can_edit and edit_id:
            ed_task = next((x for x in st.session_state["tasks"][pid] if x["id"] == edit_id), None)
            if ed_task:
                def edit_form(prefix="et"):
                    new_desc  = st.text_area("Task description", value=ed_task.get("description",""), key=f"{prefix}_desc")
                    new_specs = st.text_area("Specifications / production notes", value=ed_task.get("specifications",""), key=f"{prefix}_specs")
                    new_qty   = st.number_input("Quantity", min_value=1, step=1, value=int(ed_task.get("quantity",1)), key=f"{prefix}_qty")
                    new_type  = st.selectbox("Type", ["contracted","value added"], index=0 if ed_task.get("type")=="contracted" else 1, key=f"{prefix}_type")
                    new_assignee = st.text_input("Assignee (email)", value=ed_task.get("assignee",""), key=f"{prefix}_assignee")
                    new_status = st.selectbox("Status", ["open","complete"], index=0 if ed_task.get("status","open")=="open" else 1, key=f"{prefix}_status")
                    submitted = st.form_submit_button("Save Changes")
                    return submitted, new_desc, new_specs, new_qty, new_type, new_assignee, new_status

                if hasattr(st, "experimental_dialog"):
                    @st.experimental_dialog("Edit Task")
                    def _edit_dialog():
                        with st.form("edit_task_form"):
                            ok, d, s, q, ty, asg, stt = edit_form("et")
                        if ok:
                            update_task(pid, edit_id, description=d, specifications=s, quantity=int(q), type=ty, assignee=asg, status=stt)
                            st.session_state["edit_task_id"] = None
                            toast("Task updated.", "success"); st.rerun()
                    _edit_dialog()
                else:
                    with st.expander("Edit Task", expanded=True):
                        with st.form("edit_task_form_inline"):
                            ok, d, s, q, ty, asg, stt = edit_form("et2")
                        if ok:
                            update_task(pid, edit_id, description=d, specifications=s, quantity=int(q), type=ty, assignee=asg, status=stt)
                            st.session_state["edit_task_id"] = None
                            toast("Task updated.", "success"); st.rerun()

        # Bulk actions
        st.markdown("#### Bulk Actions")
        b1, b2, b3 = st.columns([1,1,1])
        with b1:
            if can_edit and st.button("Mark Complete", disabled=not sel_ids, key=f"bulk_complete_{pid}"):
                for tid in sel_ids: update_task(pid, tid, status="complete")
                toast("Selected tasks marked complete.", "success"); st.rerun()
        with b2:
            assign_to = st.text_input("Assign to (email)", key=f"bulk_assign_to_{pid}")
            if can_edit and st.button("Assign", disabled=not sel_ids or not assign_to, key=f"bulk_assign_{pid}"):
                for tid in sel_ids: update_task(pid, tid, assignee=assign_to)
                toast("Selected tasks assigned.", "success"); st.rerun()
        with b3:
            if can_edit and st.button("Delete Selected", disabled=not sel_ids, key=f"bulk_delete_{pid}"):
                delete_tasks(pid, sel_ids); toast("Selected tasks deleted.", "success"); st.rerun()

        st.markdown("---")
        st.markdown("### Assets (select for export)")
        for cat, items in st.session_state["assets"][pid].items():
            st.markdown(f"**{cat}**")
            for a in items:
                left, right = st.columns([0.9, 0.1])
                with left:
                    key = f"chk_{pid}_{slug(cat)}_{slug(a['name'])}"
                    cur = st.session_state["asset_sel"][pid][cat].get(a["name"], False)
                    checked = st.checkbox(a["name"], value=cur, key=key)
                    st.session_state["asset_sel"][pid][cat][a["name"]] = checked
                with right:
                    st.markdown(f"<div style='text-align:right;'>Season {a.get('season', CURRENT_SEASON)}</div>", unsafe_allow_html=True)

    # FILES
    with tabs[2]:
        st.info("Files (dummy): integrate S3/Drive later.")
        upl = st.file_uploader("Upload asset", type=["png","jpg","jpeg","pdf","pptx","docx"], key=f"files_{pid}")
        if upl and st.button("Save file", key=f"filesave_{pid}"):
            Path("uploads").mkdir(exist_ok=True)
            with open(Path("uploads")/upl.name, "wb") as f: f.write(upl.getbuffer())
            toast("File saved (local demo).", "success")

    # CALENDAR
    with tabs[3]:
        st.info("Calendar (dummy): game days, activations, deadlines.")

    # SOCIAL
    with tabs[4]:
        st.info("Social (dummy): planned & delivered posts + metrics.")

    # PRESENTATIONS
    with tabs[5]:
        st.info("Presentations (dummy): links to DigiDeck/PowerPoints.")

    # DATA
    with tabs[6]:
        st.info("Data (dummy): Tableau/QBR/engagement KPIs.")
        if TableauClient:
            st.caption("Tableau integration stub here.")

def render_partnerships():
    if current_partner:
        partner, scope = _partner_by_id(current_partner)
        if not partner:
            st.error("Partner not found."); return
        _brand_tabs(current_partner, partner["name"], scope)
        return

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

def render_prospecting():
    st.subheader("Prospecting")
    st.info("Prospecting page (coming soon). Add SponsorUnited search, etc.")

def render_selling():
    st.subheader("Selling")
    st.info("Selling page (coming soon). Add proposal gen + DigiDeck smart links.")

def render_reports():
    st.subheader("Reports")
    st.caption("Quick POP export (CSV + images zip)")
    partner = st.text_input("Partner", "Acme Beverages", key="act_partner")
    title   = st.text_input("Activation Title", "Opening Night LED", key="act_title")
    kpi     = st.text_input("KPI (e.g., Impressions)", "1.2M", key="act_kpi")
    media   = st.file_uploader("Upload photo/screenshot(s)", type=["png","jpg","jpeg"], accept_multiple_files=True, key="act_media")
    notes   = st.text_area("Notes", "", key="act_notes")
    if st.button("Export POP"):
        # Build CSV in memory
        rows = [{"partner": partner, "title": title, "kpi": kpi, "notes": notes, "filename": m.name if media else ""} for m in (media or [None])]
        csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
        st.download_button("Download POP CSV", data=csv_bytes, file_name=f"POP_{slug(partner)}.csv", mime="text/csv")
        # Future: build a zip with images + CSV

def render_users():
    st.subheader("Users")
    st.info("User-to-partnership mapping (coming soon).")

def render_presentations():
    st.subheader("Presentations")
    st.info("Links to DigiDeck / pitch decks (coming soon).")

def render_files():
    st.subheader("Files")
    st.info("Central file space (S3/Drive later).")

def render_contracts():
    st.subheader("Contracts & Terms — Q&A")
    st.info("Contract Q&A and document search (coming soon).")

def render_data():
    st.subheader("Data")
    st.info("3rd-party and internal data dashboards (coming soon).")

def render_settings():
    st.subheader("Settings")
    st.caption("Theme and asset catalogs.")
    # Theme already handled in sidebar

    st.markdown("#### Asset Catalogs")
    scope = st.selectbox("Scope", ["active","prospective"], index=0)
    names = [p["name"] for p in PARTNERS[scope]]
    ids   = [p["id"]   for p in PARTNERS[scope]]
    if not names:
        st.info("No partners in this scope.")
        return
    pick = st.selectbox("Choose partner", names)
    pid  = ids[names.index(pick)]

    # Load so we can show/download
    load_assets_for(pid)
    # Download current catalog
    cat_json = json.dumps(st.session_state["assets"][pid], ensure_ascii=False, indent=2)
    st.download_button("Download Catalog (JSON)", data=cat_json.encode("utf-8"),
                       file_name=f"assets_{pid}.json", mime="application/json")
    # Upload replacement
    up = st.file_uploader("Replace Catalog (JSON)", type=["json"], key=f"assets_up_{pid}")
    if up and st.button("Upload & Replace", key=f"assets_replace_{pid}"):
        try:
            data = json.loads(up.read().decode("utf-8"))
            st.session_state["assets"][pid] = data
            save_assets(pid)
            toast("Catalog updated.", "success"); st.rerun()
        except Exception as e:
            st.error(f"Invalid JSON: {e}")

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
else:                                   render_settings()
