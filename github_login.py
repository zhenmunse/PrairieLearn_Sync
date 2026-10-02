"""Browser-owned credentials, read atomically by a native form on submit."""
from pathlib import Path
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components


_login = components.declare_component(
    "github_login", path=str(Path(__file__).parent / "components" / "github_login")
)


def github_login_submission() -> dict | None:
    # A new component instance after logout discards its previous payload/DOM.
    if "_github_form_id" not in st.session_state:
        st.session_state["_github_form_id"] = uuid4().hex
    result = _login(key=st.session_state["_github_form_id"], default=None)
    if not isinstance(result, dict):
        return None
    submission_id = result.get("submission_id")
    if not isinstance(submission_id, str) or not submission_id:
        return None
    if submission_id == st.session_state.get("_github_form_handled"):
        return None  # Unrelated reruns must not reconnect using an old submit.
    st.session_state["_github_form_handled"] = submission_id
    if not all(isinstance(result.get(k), str) for k in ("repo_url", "pat")):
        return None
    return result
