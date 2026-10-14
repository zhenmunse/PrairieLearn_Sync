"""Shared user-facing notice, rendered on every Streamlit page."""
import streamlit as st


def render_compliance_footer() -> None:
    # Render before page guards so st.stop() cannot suppress the notice.
    # Streamlit's main vertical layout is flex; keep this block after page content.
    st.markdown("""<style>
    .st-key-compliance_footer { order: 99999; margin-top: 3rem; }
    [data-testid="stLayoutWrapper"]:has(> .st-key-compliance_footer) { order: 99999; }
    </style>""", unsafe_allow_html=True)
    with st.container(key="compliance_footer"):
        st.divider()
        st.markdown("### Standard Open-Source Compliance / Disclaimer")
        st.markdown('''**Disclaimer & Data Privacy Notice:**

This utility is an independent, open-source interface provided strictly "AS IS"
without warranty of any kind. It is **not** an official service operated,
endorsed, or audited by the University of California, Davis.

- **Data handling:** API tokens, repository settings and payloads are processed
  in memory for the active session. The application does not persist them in its
  local audit log. Audit records contain only an operation type and UTC timestamp,
  without identities, token fragments or fingerprints, repository details,
  assessment identifiers, IP addresses, or monitoring content. Live CCTV fetches
  data from PrairieLearn on each view/refresh without a server-side exam-data
  cache. Monitoring records are displayed in the browser; downloads occur only
  when the user explicitly exports them. User-requested GitHub commits/PRs and
  browser downloads remain stored at their destinations. These application-level
  controls do not describe retention by hosting infrastructure or external services.
- **User Compliance Responsibility:** By entering credentials or transmitting
  data through this interface, the user acknowledges that data will transit
  through third-party cloud infrastructure (Alibaba Cloud) when using the hosted
  deployment, and assumes sole responsibility for ensuring that such transmission
  complies with applicable institutional policies, vendor risk assessments, and
  federal regulations (including FERPA).

Copyright © Zonglin Han 2025-2026. All rights reserved. | MIT License
''')
