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
# Query params helpers (modern API, dict-like)
# ------------------------
# st.query_params behaves like a mutable dict[str, str]

def set_route(page=None, scope=None, partner=None, section=None, replace=False):
    qp = st.query_params  # dict-like proxy

    # Optionally clear everything first
    if replace:
        for k in list(qp.keys()):
            try:
                del qp[k]
            except KeyError:
                pass

    # Set / update keys
    if page is not None:
        qp["page"] = page
    if scope is not None:
        qp["scope"] = scope
    if partner is not None:
        # allow clearing partner by passing None or ""
        if partner == "" or partner is None:
            if "partner" in qp:
                del qp["partner"]
        else:
            qp["partner"] = partner
    if section is not None:
        qp["section"] = section

    # Apply route
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()  # safe fallback

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
            set_route(page=sel)  # updates ?page=... and reruns

        names = [p["name"] for p in PARTNERS[current_scope]]
        ids   = [p["id"]   for p in PARTNERS[current_scope]]
        if names:
            pick = st.selectbox("Open brand page", names, key="brand_select")
            pid  = ids[names.index(pick)]
            if st.button("Open ▶"):
                set_route(page="Partnerships", scope=current_scope, partner=pid, section="overview")

# Overwrite route with sidebar choice if user changed it
current_page = sel

# ------------------------
# Page renderers
# ------------------------
from pathlib import Path

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
        st.subheader(f"{partner['name']} — {scope.title()} Partnership")

        # Horizontal sub-tabs (band navigation)
        tabs = st.tabs(["overview","tasks","files","calendar","social","presentations","data"])
        with tabs[0]:
            st.markdown("**Assets in contract**")
            if partner["assets"]:
                for a in partner["assets"]:
                    st.write(f"• {a}")
            else:
                st.caption("No assets listed yet.")
            if st.button("← Back to all partnerships"):
                set_route(page="Partnerships", scope=scope, partner=None, section=None)

        with tabs[1]:
            st.info("Tasks (dummy): integrate task service or CRM tasks here.")
        with tabs[2]:
            st.info("Files (dummy): show S3/Qdrant/Drive links here.")
        with tabs[3]:
            st.info("Calendar (dummy): game days, activations, deadlines.")
        with tabs[4]:
            st.info("Social (dummy): planned & delivered posts + metrics.")
        with tabs[5]:
            st.info("Presentations (dummy): links to DigiDeck/PowerPoints.")
        with tabs[6]:
            st.info("Data (dummy): Tableau/QBR/engagement KPIs.")
        return

    # Otherwise, show Active / Prospective buttons
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
    st.subheader("Prospecting (Research & Proposal Building)")
    q = st.text_input("Search brands (SponsorUnited stub):", "Acme")
    su = SponsorUnitedClient()
    if st.button("Search"):
        brands = su.search_brands(q, limit=6)
        if brands:
            for b in brands:
                st.write(f"- **{b['company']}** — {b.get('industry','')} ({b.get('hq_city','')})")
        else:
            st.info("No results (add API key or try another search).")

def render_selling():
    st.subheader("Selling (Active Pitches)")
    colA, colB = st.columns(2)
    with colA:
        company = st.text_input("Company", "Acme Beverages")
        industry = st.text_input("Industry", "Beverages")
        city = st.text_input("HQ City", "Seattle")
        notes = st.text_area("Notes (segments, goals)")
        prospect = {"company": company, "industry": industry, "hq_city": city, "notes": notes}
        if st.button("Generate Insight ▶"):
            st.session_state["insight"] = propose_for_prospect(prospect)
    with colB:
        st.write("**Available team assets**")
        st.write(TEAM_ASSETS)
        if "insight" in st.session_state:
            i = st.session_state["insight"]
            st.markdown("### Suggested opener"); st.success(i.get("opener",""))
            st.markdown("### Rationale");       st.write(i.get("rationale",""))
            st.markdown("### Matching assets");  st.write(i.get("matching_assets",[]))
            st.markdown("### Next steps");       st.write(i.get("next_steps",[]))

    st.markdown("---")
    st.subheader("Smart Links (DigiDeck stub)")
    dd = DigiDeckClient()
    deck_id = st.text_input("Deck ID", "intro-2025")
    email   = st.text_input("Prospect Email", "brand@company.com")
    if st.button("Create Smart Link"):
        link = dd.create_smart_link(deck_id, email)
        st.write(link)
        st.session_state["tracking_id"] = link.get("tracking_id")
    tracking = st.text_input("Tracking ID", st.session_state.get("tracking_id", ""))
    if st.button("Get Engagement"):
        st.write(dd.get_engagement(tracking or "demo"))

def render_reports():
    st.subheader("Reports (Proof-of-Performance)")
    partner = st.text_input("Partner", "Acme Beverages", key="act_partner")
    title   = st.text_input("Activation Title", "Opening Night LED", key="act_title")
    kpi     = st.text_input("KPI (e.g., Impressions)", "1.2M", key="act_kpi")
    media   = st.file_uploader("Upload photo/screenshot", type=["png","jpg","jpeg"], key="act_media")
    notes   = st.text_area("Notes", "", key="act_notes")
    if st.button("Save Activation"):
        Path("pop_media").mkdir(exist_ok=True)
        s3_url = None
        if media:
            data = media.getbuffer()
            local = Path("pop_media")/media.name
            with open(local,"wb") as f: f.write(data)
            if s3_enabled():
                key = f"pop_media/{media.name}"
                if upload_bytes(key, data, content_type=media.type):
                    s3_url = presigned_url(key)
        st.success("Saved activation (demo).")
        if s3_url: st.markdown(f"S3 link (temporary): [{media.name}]({s3_url})")

def render_users():
    st.subheader("Users & Partner Access (dummy)")
    st.info("Map team/brand/agency users to partnerships here (CRM/IdP integration).")
    st.table({
        "User": ["You","AE #2","Brand Manager (Coke)","Agency Lead"],
        "Role": ["AE","AE","Brand","Agency"],
        "Partnerships": ["Coke, ZipPay", "Acme", "Coke", "ZipPay"]
    })

def render_presentations():
    st.subheader("Presentations (dummy)")
    st.info("List DigiDeck links or uploaded pitch decks here.")
    st.write("• Coke: 2025 Renewal Pitch (Smart Link)")
    st.write("• ZipPay: Category Entry Pitch")

def render_files():
    st.subheader("Files (dummy)")
    st.info("Central file space for images/copy/creative. Back with S3/Drive later.")
    upl = st.file_uploader("Upload asset", type=["png","jpg","jpeg","pdf","pptx","docx"])
    if upl and st.button("Save file"):
        Path("uploads").mkdir(exist_ok=True)
        with open(Path("uploads")/upl.name, "wb") as f: f.write(upl.getbuffer())
        st.success("Saved (local demo).")

def render_contracts():
    st.subheader("Contracts & Terms — Q&A")
    uploads = st.file_uploader("Upload executed contract PDFs", type=["pdf"], accept_multiple_files=True, key="contracts")
    if not uploads: return
    Path("contracts").mkdir(exist_ok=True)
    pdf_paths = []
    for f in uploads:
        dest = Path("contracts")/f.name
        data = f.getbuffer()
        with open(dest,"wb") as out: out.write(data)
        if s3_enabled():
            upload_bytes(f"contracts/{f.name}", data, content_type="application/pdf")
        pdf_paths.append(str(dest))
    with st.spinner("Indexing contracts..."):
        retriever, n_chunks, provider_used = build_contract_store(pdf_paths, persist_dir=None)
    if retriever is None:
        st.error("No valid PDF pages loaded."); return
    st.success(f"Indexed {n_chunks} chunks via {provider_used}.")
    from langchain_openai import ChatOpenAI
    from langchain.chains import RetrievalQA
    from langchain.prompts import ChatPromptTemplate
    prompt = ChatPromptTemplate.from_messages([
        ("system","Answer using ONLY the contract context. If unknown, say so."),
        ("human","Q: {question}\nContext:\n{context}\nA:")
    ])
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    qa = RetrievalQA.from_chain_type(llm=llm, retriever=retriever, chain_type="stuff",
                                     chain_type_kwargs={"prompt": prompt}, return_source_documents=True)
    q = st.text_input("Ask about terms (e.g., 'What are the makegoods?')")
    if st.button("Ask ▶"):
        res = qa({"query": q})
        st.write(res["result"])
        pages = sorted(set(int(s.metadata.get("page",-1))+1 for s in res.get("source_documents",[]) if s.metadata.get("page",-1)>=0))
        if pages: st.caption("Sources: " + ", ".join(f"p.{p}" for p in pages))

def render_data():
    st.subheader("Data (dummy)")
    st.info("3rd-party + user data to support POP (Tableau/Qdrant/etc).")
    st.metric("Season Impressions", "12.4M")
    st.metric("Engagements", "342k")
    st.metric("Promo Redemptions", "18.7k")
    st.metric("SOV vs Category", "19%")

def render_settings():
    st.subheader("Settings")
    st.caption("Provider selection is kept here (no secrets displayed).")
    provider = st.selectbox("Vector DB provider", ["pinecone","qdrant","chroma"],
                            index={"pinecone":0,"qdrant":1,"chroma":2}.get(os.getenv("VECTOR_DB_PROVIDER","pinecone"),0))
    os.environ["VECTOR_DB_PROVIDER"] = provider
    st.write("Current:", provider)

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
