"""User Management -- this app's own accounts. Admin-only (defence-in-depth;
the router leaves it out of the menu for operators). Rendered by app.py's router."""
import streamlit as st

import db
from auth import require_admin
from activity_log import log_activity
from branding import page_header, one_indexed

ALL_REGIONS = [
    "ABUJA", "BAUCHI", "BENIN", "ENUGU", "KADUNA", "KANO",
    "LAGOS", "OSOGBO", "PORTHARCOURT", "SHIRORO",
]

page_header("User Management", "Accounts for the Incident & Outage Request app")
require_admin()

users_df = db.list_users()
display_df = users_df.copy()
display_df["region"] = display_df["region"].fillna("All")
st.dataframe(
    one_indexed(display_df.rename(
        columns={"username": "Username", "name": "Name", "role": "Role", "region": "Region", "created_at": "Created"}
    )),
    use_container_width=True,
)

st.subheader("Add User")
with st.form("add_user"):
    c1, c2 = st.columns(2)
    with c1:
        uname = st.text_input("Username")
        pw = st.text_input("Password", type="password")
    with c2:
        name = st.text_input("Full Name")
        role = st.selectbox("Role", ["operator", "admin"])
    region = st.selectbox("Region scope (operators)", ["All"] + ALL_REGIONS)
    if st.form_submit_button("Create User", type="primary"):
        if not uname or not pw:
            st.error("Username and password are required.")
        elif uname.strip() in users_df["username"].values:
            st.error("Username already exists.")
        else:
            db.create_user(
                uname.strip(), db.hash_password(pw), name or uname, role,
                None if region == "All" or role == "admin" else region,
            )
            log_activity("create_user", f"Created '{uname}' as {role}" + (f" ({region})" if region != "All" else ""))
            st.success(f"User '{uname}' created.")
            st.rerun()

st.subheader("Reset Password")
with st.form("reset_pw"):
    target_user = st.selectbox("User", options=list(users_df["username"]))
    new_pw = st.text_input("New password", type="password")
    if st.form_submit_button("Reset Password"):
        if not new_pw:
            st.error("Enter a new password.")
        else:
            db.update_user(target_user, password_hash=db.hash_password(new_pw))
            log_activity("reset_password", f"Reset password for '{target_user}'")
            st.success(f"Password reset for '{target_user}'.")

st.subheader("Delete User")
me = st.session_state.get("username")
deletable = [u for u in users_df["username"] if u != me]
if deletable:
    target = st.selectbox("Select user", deletable, key="del_user")
    if st.button("Delete User"):
        db.delete_user(target)
        log_activity("delete_user", f"Deleted '{target}'")
        st.success(f"User '{target}' deleted.")
        st.rerun()
