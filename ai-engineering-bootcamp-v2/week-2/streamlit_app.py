"""Streamlit UI for the Week 2 RAG service.

A thin client. It holds no API keys, talks to no vector store, and contains no
retrieval logic -- it posts JSON to the FastAPI service and renders what comes
back. That split is deliberate:

- Credentials stay server-side. A Streamlit page runs code that the browser can
  reach, so an OpenAI key here would be a key handed to whoever opens the page.
  The only configuration this file needs is the address of the API.
- One implementation of RAG. If chunking or retrieval lived here as well as in
  the service, the demo and the deployed endpoint could drift apart, and the
  screenshot would stop being evidence about the thing that was graded.

Run:
    .venv/bin/streamlit run streamlit_app.py

Point it at a service with the sidebar box, or set API_BASE_URL before running:
    API_BASE_URL=https://week2-rag-api.onrender.com .venv/bin/streamlit run streamlit_app.py
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

# Default target. Read from the environment so the deployed URL is not hardcoded
# into the file, and overridable in the sidebar so the same page can be pointed
# at a local server without an edit. Not a secret -- a public URL is public.
DEFAULT_API_BASE = os.getenv("API_BASE_URL", "https://week2-rag-api.onrender.com")

# Free-tier Render sleeps after ~15 minutes idle and takes ~45s to wake. A
# shorter timeout would make a sleeping service look like a broken one, which is
# the single most confusing thing this page could do to someone demoing it.
REQUEST_TIMEOUT = 120.0

st.set_page_config(page_title="Week 2 — RAG /ask", layout="wide")


# --- Sidebar ------------------------------------------------------------------

st.sidebar.title("Connection")

api_base = st.sidebar.text_input(
    "API base URL",
    value=DEFAULT_API_BASE,
    help="The FastAPI service this page talks to. Change it to point at a local "
    "server instead of the deployed one.",
).rstrip("/")

st.sidebar.caption(
    "This page stores no keys. The API holds the OpenAI and Pinecone "
    "credentials and does all retrieval."
)

if st.sidebar.button("Check service health", width="stretch"):
    with st.sidebar:
        with st.spinner("Contacting the service (may take ~45s if asleep)..."):
            try:
                response = httpx.get(f"{api_base}/health/pinecone", timeout=REQUEST_TIMEOUT)
                report = response.json()
            except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
                st.error(f"Could not reach {api_base}\n\n{type(exc).__name__}: {exc}")
            else:
                if report.get("ok"):
                    pinecone = report["pinecone"]
                    st.success("Vector store reachable")
                    st.metric("Chunks stored", f"{pinecone.get('total_vectors', 0):,}")
                    st.caption(
                        f"index `{pinecone.get('index_name')}` · "
                        f"{pinecone.get('dimension')} dimensions · "
                        f"`{report['config'].get('embedding_model')}`"
                    )
                else:
                    st.error(report.get("error", "Unknown problem"))


st.title("Northwind RAG — ingest and ask")
st.caption(f"Calling `{api_base}` · retrieval and generation happen in the API, not here")

ingest_tab, ask_tab = st.tabs(["Ingest a document", "Ask a question"])


# --- Ingest -------------------------------------------------------------------

with ingest_tab:
    st.subheader("POST /ingest")
    st.write(
        "Paste text and give it an id. The service chunks it, embeds each chunk "
        "and stores the vectors. Re-using an id replaces that document rather "
        "than adding a second copy."
    )

    text = st.text_area("Document text", height=240, placeholder="Paste document text here...")

    left, right = st.columns(2)
    document_id = left.text_input(
        "document_id",
        placeholder="employee-handbook",
        help="Stable identifier. Re-ingesting the same id replaces the document.",
    )
    source = right.text_input(
        "source (optional)",
        placeholder="employee_handbook_2026.pdf",
        help="Shown in citations. Defaults to the document_id.",
    )

    with st.expander("Chunking options"):
        st.caption(
            "Overridden per request, so settings can be compared without "
            "restarting the service. Leave as-is to use the server defaults."
        )
        c1, c2 = st.columns(2)
        chunk_size = c1.number_input("chunk_size", min_value=100, max_value=4000, value=800, step=50)
        chunk_overlap = c2.number_input(
            "chunk_overlap", min_value=0, max_value=1000, value=100, step=25
        )

    if st.button("Ingest", type="primary", width="stretch"):
        if not text.strip() or not document_id.strip():
            # Caught here as well as by the API so the obvious mistake gets an
            # instant answer rather than a round trip.
            st.warning("Both document text and document_id are required.")
        else:
            payload = {
                "text": text,
                "document_id": document_id.strip(),
                "chunk_size": int(chunk_size),
                "chunk_overlap": int(chunk_overlap),
            }
            if source.strip():
                payload["source"] = source.strip()

            with st.spinner("Chunking, embedding and storing..."):
                try:
                    response = httpx.post(
                        f"{api_base}/ingest", json=payload, timeout=REQUEST_TIMEOUT
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not reach the service.\n\n{type(exc).__name__}: {exc}")
                else:
                    if response.status_code == 200:
                        result = response.json()
                        st.success(
                            f"Stored **{result['chunks_indexed']}** chunks "
                            f"as `{result['document_id']}`"
                        )
                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric("Chunks", result["chunks_indexed"])
                        m2.metric("Replaced", result["replaced_existing_chunks"])
                        m3.metric("Tokens", f"{result['embedding_tokens']:,}")
                        m4.metric("Cost", f"${result['cost_usd']:.6f}")
                        st.caption(
                            "Newly stored chunks can take a few seconds to become "
                            "searchable — the vector store indexes asynchronously."
                        )
                        with st.expander("Full response"):
                            st.json(result)
                    else:
                        # The API distinguishes "you sent something unusable" (400)
                        # from "an upstream service failed" (502); pass that
                        # distinction through rather than flattening it to "error".
                        detail = response.json().get("detail", response.text)
                        if response.status_code in (400, 422):
                            st.warning(f"Rejected ({response.status_code}): {detail}")
                        else:
                            st.error(f"Failed ({response.status_code}): {detail}")


# --- Ask ----------------------------------------------------------------------

with ask_tab:
    st.subheader("POST /ask")
    st.write(
        "The service embeds the question, retrieves the most similar chunks, and "
        "answers from those alone — citing the documents it used, or declining "
        "when the answer is not in them."
    )

    question = st.text_input(
        "Question",
        placeholder="What is the remote work policy?",
    )

    opt1, opt2 = st.columns([1, 2])
    top_k = opt1.slider("Chunks to retrieve (top_k)", 1, 10, 5)
    use_rag = opt2.toggle(
        "Use retrieval",
        value=True,
        help="Turn off to run the Week 1 path — straight to the model with no "
        "documents. Useful for showing what retrieval actually changes.",
    )

    if st.button("Ask", type="primary", width="stretch"):
        if not question.strip():
            st.warning("Enter a question.")
        else:
            with st.spinner("Retrieving and answering..."):
                try:
                    response = httpx.post(
                        f"{api_base}/ask",
                        json={
                            "question": question.strip(),
                            "top_k": int(top_k),
                            "use_rag": use_rag,
                        },
                        timeout=REQUEST_TIMEOUT,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not reach the service.\n\n{type(exc).__name__}: {exc}")
                else:
                    if response.status_code != 200:
                        detail = response.json().get("detail", response.text)
                        st.error(f"Failed ({response.status_code}): {detail}")
                    else:
                        result = response.json()
                        answer = result["answer"]

                        # Refusal is a correct outcome, not a failure, so it is
                        # shown as a distinct state rather than as an error. The
                        # API reports it as a boolean because the prompt fixes an
                        # exact refusal sentence -- no string matching here.
                        if result.get("refused"):
                            st.warning("**Refused — the answer is not in the documents**")
                            st.write(answer["answer"])
                            st.caption(
                                f"Retrieval still returned {len(result['retrieved_chunk_ids'])} "
                                "chunks. It always does — it ranks whatever is stored and "
                                "cannot report 'nothing relevant'. The model read them, found "
                                "no answer, and declined rather than inventing one."
                            )
                        else:
                            st.success("**Answer**")
                            st.write(answer["answer"])

                        # Citations.
                        citations = answer.get("citations", [])
                        if citations:
                            verified = result.get("citations_verified", True)
                            st.markdown("**Cited documents**")
                            st.markdown(" ".join(f"`{c}`" for c in citations))
                            if verified:
                                st.caption(
                                    "Every cited id was checked against the chunks actually "
                                    "retrieved."
                                )
                            else:
                                st.error(
                                    "Unverified citations: "
                                    f"{result.get('unknown_citations')} — the model named "
                                    "documents it was not given."
                                )
                        elif not result.get("refused"):
                            st.info(
                                "No citations returned."
                                + (
                                    "  Retrieval was off, so the answer came from the model's "
                                    "training data rather than your documents."
                                    if not result.get("rag")
                                    else ""
                                )
                            )

                        # Cost and timing, carried over from the Week 1 service.
                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric("Confidence", f"{answer['confidence']:.2f}")
                        m2.metric("Tokens", f"{result['tokens_used']:,}")
                        m3.metric("Cost", f"${result['cost_usd']:.6f}")
                        m4.metric("Latency", f"{result['latency_ms']:,} ms")

                        retrieved = result.get("retrieved", [])
                        if retrieved:
                            st.markdown("**Retrieved chunks**")
                            st.caption(
                                "Cosine similarity: 1.0 is identical in meaning, 0.0 unrelated. "
                                "Read the scores against each other — a clear top hit means the "
                                "search found something; scores bunched low mean it did not."
                            )
                            st.dataframe(
                                [
                                    {
                                        "score": round(chunk["score"], 4),
                                        "chunk": chunk["id"],
                                        "document_id": chunk["document_id"],
                                        "source": chunk["source"],
                                    }
                                    for chunk in retrieved
                                ],
                                width="stretch",
                                hide_index=True,
                            )

                        with st.expander("Full response"):
                            st.json(result)
