import os
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

from services.providers import SponsorUnitedClient, DigiDeckClient, SalesforceClient, DynamicsClient, TableauClient
from services.reasoning import propose_for_prospect, TEAM_ASSETS
from services.storage import build_contract_store
from services.s3store import s3_enabled, upload_bytes, presigned_url

ASSETS = Path(__file__).parent / "assets"
HEADER_LOGO = ASSETS / "SSAM_Logo.png"
FAVICON = HEADER_LOGO / "favicon.png"

st.set_page_config(
    page_title="Sponsorship Sales & Activation Machine",
    page_icon=str(FAVICON) if FAVICON.exists() else "🏟️",
    layout="wide",
)

# --- Header (logo + title) ---
left, right = st.columns([0.12, 0.88])
with left:
    if HEADER_LOGO.exists():
        st.image(str(HEADER_LOGO), width=164)  # tweak width to fit
with right:
    st.title("SSAM — Sponsorship Sales & Activation Machine")

load_dotenv()
if st.secrets.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

st.caption("Prospecting → Pitch intelligence → Activation Management → Contracts Q&A → Partner dashboards")

with st.sidebar:
    st.subheader("🔧 Settings")
    provider = st.selectbox("Vector DB provider", ["pinecone","qdrant","chroma"], index=0)
    os.environ["VECTOR_DB_PROVIDER"] = provider

    st.subheader("🔑 Keys (set in Secrets/env)")
    st.code(
        "OPENAI_API_KEY=...\n"
        "S3_BUCKET=...\nAWS_ACCESS_KEY_ID=...\nAWS_SECRET_ACCESS_KEY=...\nAWS_DEFAULT_REGION=...\n"
        "PINECONE_API_KEY=...\nPINECONE_ENV=...\nPINECONE_INDEX=sports_ai\n"
        "QDRANT_URL=...\nQDRANT_API_KEY=...\nQDRANT_COLLECTION=sports_ai",
        language="bash"
    )
    st.info("No keys? The app still runs: S3 disabled, vector DB falls back to Chroma.")

tabs = st.tabs(["Prospects", "Pitches", "Activation", "Contracts Q&A", "Partner Dashboard"])

with tabs[0]:
    st.subheader("Find Prospects")
    q = st.text_input("Search brands (SponsorUnited stub):", "Acme")
    su = SponsorUnitedClient()
    if st.button("Search"):
        brands = su.search_brands(q, limit=5)
        st.write("**Results**")
        for b in brands:
            st.write(f"- **{b['company']}** — {b.get('industry','')} ({b.get('hq_city','')})")
        if not brands:
            st.info("No results (provide API key or try a different query).")

with tabs[1]:
    st.subheader("Pitch Intelligence")
    colA, colB = st.columns(2)
    with colA:
        company = st.text_input("Company", "Acme Beverages")
        industry = st.text_input("Industry", "Beverages")
        city = st.text_input("HQ City", "Seattle")
        notes = st.text_area("Notes (segments, goals)", "")
        prospect = {"company": company, "industry": industry, "hq_city": city, "notes": notes}
        if st.button("Generate Insight ▶"):
            st.session_state["insight"] = propose_for_prospect(prospect)
    with colB:
        st.write("**Available team assets**")
        st.write(TEAM_ASSETS)
        if "insight" in st.session_state:
            i = st.session_state["insight"]
            st.markdown("### Suggested opener")
            st.success(i.get("opener",""))
            st.markdown("### Rationale")
            st.write(i.get("rationale",""))
            st.markdown("### Matching assets")
            st.write(i.get("matching_assets",[]))
            st.markdown("### Next steps")
            st.write(i.get("next_steps",[]))

with tabs[2]:
    st.subheader("Activation Logging (Proof-of-Performance)")
    partner = st.text_input("Partner", "Acme Beverages", key="act_partner")
    title = st.text_input("Activation Title", "Opening Night LED", key="act_title")
    kpi = st.text_input("KPI (e.g., Impressions)", "1.2M", key="act_kpi")
    media = st.file_uploader("Upload photo/screenshot", type=["png","jpg","jpeg"], key="act_media")
    notes = st.text_area("Notes", "", key="act_notes")
    if st.button("Save Activation"):
        # Save locally for demo
        Path("pop_media").mkdir(exist_ok=True)
        fpath = None
        s3_url = None
        if media:
            fbytes = media.getbuffer()
            fpath = Path("pop_media")/media.name
            with open(fpath,"wb") as f: f.write(fbytes)
            if s3_enabled():
                key = f"pop_media/{media.name}"
                if upload_bytes(key, fbytes, content_type=media.type):
                    s3_url = presigned_url(key)
        st.success("Saved activation (demo).")
        if s3_url:
            st.markdown(f"S3 link (temporary): [{media.name}]({s3_url})")

with tabs[3]:
    st.subheader("Contracts & Terms — Ask Questions")
    uploads = st.file_uploader("Upload executed contract PDFs", type=["pdf"], accept_multiple_files=True, key="contracts")
    if uploads:
        Path("contracts").mkdir(exist_ok=True)
        pdf_paths = []
        for f in uploads:
            dest = Path("contracts")/f.name
            data = f.getbuffer()
            with open(dest,"wb") as out:
                out.write(data)
            # also push to S3 if configured
            if s3_enabled():
                upload_bytes(f"contracts/{f.name}", data, content_type="application/pdf")
            pdf_paths.append(str(dest))
        with st.spinner("Indexing contracts..."):
            retriever, n_chunks, provider_used = build_contract_store(pdf_paths, persist_dir=None)
        if retriever is None:
            st.error("No valid PDF pages loaded.")
        else:
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
                if pages:
                    st.caption("Sources: " + ", ".join(f"p.{p}" for p in pages))

with tabs[4]:
    st.subheader("Partner Dashboard (Tableau or native)")
    partner_id = st.text_input("Partner ID", "acme-001")
    view_path = st.text_input("Tableau View Path", "Partner/Overview")
    tb = TableauClient()
    url = tb.partner_view_url(view_path, partner_id)
    if url:
        st.write("**Embedded (link):**")
        st.markdown(f"[Open Tableau view]({url})")
    else:
        st.info("No Tableau credentials. Showing native KPIs (demo).")
        import random
        st.metric("Impressions (season)", f"{random.randint(8,15)}M")
        st.metric("Engagements", f"{random.randint(200,450)}k")
        st.metric("Promo Redemptions", f"{random.randint(5,25)}k")
        st.metric("SOV vs Category", f"{random.randint(12,24)}%")
