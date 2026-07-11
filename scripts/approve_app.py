"""Local browser app for approving the weekly Etsy POD pipeline.

Run with:
    streamlit run scripts/approve_app.py

Reads and writes pod.db directly. Replaces the Notion approval UI.
Tabs: Prompts (Mon) → Images (Wed) → Publish (Thu) → Listings → Stats.

Also deployable to Streamlit Community Cloud for approve-from-anywhere:
set GIT_PUSH_TOKEN (a GitHub fine-grained PAT with contents:write) in the
app's secrets and the git sync buttons will push over HTTPS with it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import db as pod_db  # noqa: E402

DB_PATH = os.environ.get("POD_DB_PATH", str(PROJECT_ROOT / "pod.db"))


# ── shared helpers ────────────────────────────────────────────────────────────

@st.cache_resource
def get_conn():
    conn = pod_db.connect(DB_PATH, check_same_thread=False)
    pod_db.run_migrations(conn)
    return conn


def _refresh():
    st.cache_data.clear()
    st.rerun()


@st.cache_data(ttl=5)
def _counts() -> dict:
    conn = get_conn()
    return {
        "prompts":  len(pod_db.lineage_pending_for_stage(conn, "prompt_review")),
        "images":   len(pod_db.lineage_pending_for_stage(conn, "image_review")),
        "publish":  len(pod_db.lineage_pending_for_stage(conn, "publish_review")),
        "listings": len(pod_db.lineage_pending_for_stage(conn, "etsy_publish")),
    }


def _push_token() -> str:
    """GIT_PUSH_TOKEN from env or Streamlit secrets (cloud deployment)."""
    tok = os.environ.get("GIT_PUSH_TOKEN", "")
    if tok:
        return tok
    try:
        return st.secrets.get("GIT_PUSH_TOKEN", "")
    except Exception:
        return ""


def _git(*args: str) -> tuple[int, str]:
    env = os.environ.copy()
    tok = _push_token()
    if tok:
        # Inject the PAT into HTTPS remotes without persisting it to .git/config.
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "url.https://x-access-token:%s@github.com/.insteadOf" % tok
        env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
    proc = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _checkpoint_wal() -> None:
    """Flush WAL writes back to pod.db so git sees the latest approvals."""
    get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _run_script(script_name: str, log_area) -> int:
    """Stream a pipeline script's stdout into a Streamlit log expander."""
    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "scripts" / script_name)],
        cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    lines: list[str] = []
    for line in proc.stdout:  # type: ignore[union-attr]
        lines.append(line.rstrip())
        log_area.code("\n".join(lines[-200:]))
    proc.wait()
    return proc.returncode


# ── sidebar ───────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Etsy POD — Approvals", page_icon="🧵", layout="wide")
st.sidebar.title("Etsy POD")
st.sidebar.caption(f"DB: `{DB_PATH}`")

counts = _counts()
st.sidebar.metric("Prompts pending",   counts["prompts"])
st.sidebar.metric("Images pending",    counts["images"])
st.sidebar.metric("Publish approvals", counts["publish"])
st.sidebar.metric("Awaiting Etsy URL", counts["listings"])

with st.sidebar.expander("Git sync", expanded=False):
    if st.button("Pull latest pod.db"):
        _checkpoint_wal()
        rc, out = _git("pull", "--rebase")
        st.code(out or f"exit {rc}")
        _refresh()
    if st.button("Push approvals"):
        _checkpoint_wal()
        _git("add", "pod.db")
        rc_c, out_c = _git("commit", "-m", "approvals: local browser app")
        rc_p, out_p = _git("push")
        st.code((out_c + "\n" + out_p) or f"commit={rc_c} push={rc_p}")


# ── tabs ──────────────────────────────────────────────────────────────────────

tab_names = ["Prompts", "Images", "Publish", "Listings", "Stats"]
qp = st.query_params.get("tab")
default_idx = {"prompts": 0, "images": 1, "publish": 2,
               "drafts": 3, "listings": 3, "stats": 4}.get(qp or "", 0)
# Streamlit doesn't expose a "select tab" API, so we render all five; the deep-link
# is purely informational (the user can click the tab themselves).
tab_p, tab_i, tab_pub, tab_d, tab_s = st.tabs(tab_names)


# ── tab: Prompts ─────────────────────────────────────────────────────────────

with tab_p:
    st.header("Prompts to review (Monday)")
    conn = get_conn()
    rows = pod_db.lineage_pending_for_stage(conn, "prompt_review")
    if not rows:
        st.success("Nothing pending. Either Monday's research hasn't run yet or "
                   "all prompts are already reviewed.")
    else:
        c1, c2 = st.columns(2)
        if c1.button(f"✅ Approve all ({len(rows)})", key="approve_all_prompts"):
            for r in rows:
                pod_db.lineage_set_prompt_status(conn, r["lineage_id"], "approved")
            _refresh()
        if c2.button(f"❌ Reject all ({len(rows)})", key="reject_all_prompts"):
            for r in rows:
                pod_db.lineage_set_prompt_status(conn, r["lineage_id"], "rejected")
            _refresh()

        for r in rows:
            with st.container(border=True):
                top = st.columns([3, 1, 1])
                top[0].markdown(f"**{r['category'] or '—'}**  ·  `{r['lineage_id'][:8]}`")
                if top[1].button("✅ Approve", key=f"a_p_{r['lineage_id']}"):
                    pod_db.lineage_set_prompt_status(conn, r["lineage_id"], "approved")
                    _refresh()
                if top[2].button("❌ Reject", key=f"r_p_{r['lineage_id']}"):
                    pod_db.lineage_set_prompt_status(conn, r["lineage_id"], "rejected")
                    _refresh()
                st.text_area("Prompt", r["prompt_text"] or "", height=120,
                             key=f"pt_{r['lineage_id']}", disabled=True,
                             label_visibility="collapsed")

        st.divider()
        if st.button("▶ Generate images for all approved prompts now"):
            log = st.expander("Image generation log", expanded=True).empty()
            rc = _run_script("03_generate_images.py", log)
            (st.success if rc == 0 else st.error)(f"Script exited {rc}")
            _refresh()


# ── tab: Images ──────────────────────────────────────────────────────────────

with tab_i:
    st.header("Images to review (Wednesday)")
    conn = get_conn()
    rows = pod_db.lineage_pending_for_stage(conn, "image_review")
    if not rows:
        st.success("No images pending review.")
    else:
        c1, c2 = st.columns(2)
        if c1.button(f"✅ Approve all ({len(rows)})", key="approve_all_imgs"):
            for r in rows:
                pod_db.lineage_set_image_status(conn, r["lineage_id"], "approved")
            _refresh()
        if c2.button(f"❌ Reject all ({len(rows)})", key="reject_all_imgs"):
            for r in rows:
                pod_db.lineage_set_image_status(conn, r["lineage_id"], "rejected")
            _refresh()

        # Best AI pre-screen scores first so the human reviews winners quickly;
        # unscored images sink to the end.
        rows = sorted(rows, key=lambda r: (r["ai_score"] is None, -(r["ai_score"] or 0)))
        cols = st.columns(3)
        for i, r in enumerate(rows):
            with cols[i % 3]:
                with st.container(border=True):
                    if r["image_url"]:
                        st.image(r["image_url"], use_container_width=True)
                    score_bit = (f" · AI {r['ai_score']:.0f}/10"
                                 if r["ai_score"] is not None else "")
                    st.caption(f"{r['category'] or '—'} · `{r['lineage_id'][:8]}`{score_bit}")
                    if r["ai_feedback"] and r["ai_feedback"] != "clean":
                        st.caption(f"⚠️ {r['ai_feedback']}")
                    with st.expander("Prompt"):
                        st.text(r["prompt_text"] or "")
                    bc = st.columns(2)
                    if bc[0].button("✅", key=f"a_i_{r['lineage_id']}"):
                        pod_db.lineage_set_image_status(conn, r["lineage_id"], "approved")
                        _refresh()
                    if bc[1].button("❌", key=f"r_i_{r['lineage_id']}"):
                        pod_db.lineage_set_image_status(conn, r["lineage_id"], "rejected")
                        _refresh()

        st.divider()
        if st.button("▶ Generate copy + Printify drafts for approved images now"):
            log = st.expander("Copy + Printify log", expanded=True).empty()
            rc = _run_script("04_generate_copy.py", log)
            if rc == 0:
                rc = _run_script("06_printify_upload.py", log)
            (st.success if rc == 0 else st.error)(f"Script chain exited {rc}")
            _refresh()


# ── tab: Publish ─────────────────────────────────────────────────────────────

with tab_pub:
    st.header("Approve drafts for Etsy publishing (Thursday)")
    st.caption("Approving here is the cost gate: publishing lists on Etsy "
               "($0.20/listing). Script 08 publishes approved drafts through "
               "the Printify API — no clicking around Printify needed.")
    conn = get_conn()
    rows = pod_db.lineage_pending_for_stage(conn, "publish_review")
    if not rows:
        st.success("No drafts awaiting publish approval.")
    else:
        c1, c2 = st.columns(2)
        if c1.button(f"✅ Approve all ({len(rows)})", key="approve_all_pub"):
            for r in rows:
                pod_db.lineage_set_publish_status(conn, r["lineage_id"], "approved")
            _refresh()
        if c2.button(f"❌ Reject all ({len(rows)})", key="reject_all_pub"):
            for r in rows:
                pod_db.lineage_set_publish_status(conn, r["lineage_id"], "rejected")
            _refresh()

        for r in rows:
            with st.container(border=True):
                cols = st.columns([2, 3, 1, 1])
                if r["image_url"]:
                    cols[0].image(r["image_url"], use_container_width=True)
                cols[1].markdown(f"**{r['etsy_title'] or '(no title)'}**")
                cols[1].caption(r["etsy_description"] or "")
                if r["printify_draft_url"]:
                    cols[1].markdown(f"[Open Printify draft]({r['printify_draft_url']})")
                if cols[2].button("✅ Publish", key=f"a_pub_{r['lineage_id']}"):
                    pod_db.lineage_set_publish_status(conn, r["lineage_id"], "approved")
                    _refresh()
                if cols[3].button("❌ Reject", key=f"r_pub_{r['lineage_id']}"):
                    pod_db.lineage_set_publish_status(conn, r["lineage_id"], "rejected")
                    _refresh()

    approved = pod_db.lineage_pending_for_stage(conn, "publish")
    st.divider()
    if st.button(f"▶ Publish {len(approved)} approved draft(s) to Etsy now"):
        log = st.expander("Publish log", expanded=True).empty()
        rc = _run_script("08_publish_etsy.py", log)
        (st.success if rc == 0 else st.error)(f"Script exited {rc}")
        _refresh()


# ── tab: Listings ────────────────────────────────────────────────────────────

with tab_d:
    st.header("Published drafts awaiting an Etsy URL")
    conn = get_conn()
    rows = pod_db.lineage_pending_for_stage(conn, "etsy_publish")
    if not rows:
        st.success("No drafts awaiting an Etsy URL.")
    else:
        st.caption("Sunday's stats sync auto-detects Etsy URLs by title. Use the "
                   "manual paste only if you need it before then.")
        for r in rows:
            with st.container(border=True):
                cols = st.columns([3, 2])
                cols[0].markdown(f"**{r['etsy_title'] or '(no title)'}**")
                cols[0].markdown(
                    f"[Open Printify draft]({r['printify_draft_url']})"
                    if r["printify_draft_url"] else "_no draft URL_"
                )
                pasted = cols[1].text_input("Etsy listing URL",
                                            key=f"etsy_{r['lineage_id']}",
                                            placeholder="https://www.etsy.com/listing/...")
                if cols[1].button("Save URL", key=f"save_{r['lineage_id']}") and pasted:
                    pod_db.lineage_upsert(conn, r["lineage_id"],
                                          etsy_listing_url=pasted)
                    pod_db.lineage_set_draft_status(conn, r["lineage_id"], "published")
                    _refresh()

        st.divider()
        if st.button("▶ Run stats sync now (also auto-detects Etsy URLs)"):
            log = st.expander("Stats sync log", expanded=True).empty()
            rc = _run_script("07_track_stats.py", log)
            (st.success if rc == 0 else st.error)(f"Script exited {rc}")
            _refresh()


# ── tab: Stats ───────────────────────────────────────────────────────────────

with tab_s:
    st.header("Feedback signal (last 4 weeks)")
    conn = get_conn()
    sig = pod_db.load_feedback_signal(conn)

    if sig["is_cold_start"]:
        st.info("Cold start — no listing_stats yet.")
    else:
        st.subheader("Top winning briefs")
        if sig["top_winning_briefs"]:
            st.dataframe([
                {"brief_id": b["brief_id"][:8], "category": b["category"],
                 "headline": b["headline_text"],
                 "favorites_delta_total": b["favorites_delta_total"],
                 "themes": ", ".join(b["themes"])}
                for b in sig["top_winning_briefs"]
            ], use_container_width=True, hide_index=True)
        else:
            st.write("_no winners with positive favorites delta yet_")

        st.subheader("Underrepresented categories")
        st.dataframe(sig["underrepresented_categories"],
                     use_container_width=True, hide_index=True)

        st.subheader("Winning style tags")
        st.dataframe(sig["winning_style_tags"],
                     use_container_width=True, hide_index=True)

        st.subheader("Recently explored themes (last 8 weeks)")
        st.dataframe(sig["recently_explored_themes"],
                     use_container_width=True, hide_index=True)

    st.divider()
    if st.button("▶ Run weekly research now (creates Monday's prompts)"):
        log = st.expander("Research log", expanded=True).empty()
        rc = _run_script("01_research.py", log)
        if rc == 0:
            rc = _run_script("02_generate_prompts.py", log)
        (st.success if rc == 0 else st.error)(f"Script chain exited {rc}")
        _refresh()
