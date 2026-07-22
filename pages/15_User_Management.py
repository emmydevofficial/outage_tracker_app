"""
### FILE: pages/15_User_Management.py
Super Admin only: create, update, delete users and assign their region.

Any logged-in user sees this page listed in the sidebar (Streamlit's classic
multipage nav can't hide entries per-role), but require_super_admin() denies
everyone else immediately -- no data is rendered for non-admins.
"""

import streamlit as st
from utils.auth import login, require_super_admin, hash_password

login()
require_super_admin()

import pandas as pd
from utils.db import list_users, create_user, update_user, delete_user, count_super_admins
from utils.regions import REGIONS

st.set_page_config(page_title="User Management", layout="wide")

st.title("👥 User Management")
st.caption("Super Admin only. Regional users can view, operate on, and upload data only for their assigned region.")

ROLE_LABELS = {"super_admin": "Super Admin", "regional_user": "Regional User"}


def _refresh():
    st.rerun()


st.subheader("All Users")
users_df = list_users()
display_df = users_df.copy()
display_df["user_role"] = display_df["user_role"].map(ROLE_LABELS).fillna(display_df["user_role"])
display_df["region"] = display_df["region"].fillna("— (all regions) —")
st.dataframe(
    display_df.rename(columns={"user_role": "Role", "region": "Region", "username": "Username", "created_at": "Created"}),
    use_container_width=True,
)

st.divider()

st.subheader("Create User")
with st.form("create_user_form", clear_on_submit=True):
    c1, c2 = st.columns(2)
    new_username = c1.text_input("Username")
    new_role_label = c2.selectbox("Role", ["Regional User", "Super Admin"])

    c3, c4 = st.columns(2)
    new_password = c3.text_input("Password", type="password")
    new_password_confirm = c4.text_input("Confirm Password", type="password")

    new_region = None
    if new_role_label == "Regional User":
        new_region = st.selectbox("Region", REGIONS)

    submitted = st.form_submit_button("Create User", type="primary")
    if submitted:
        new_role = "super_admin" if new_role_label == "Super Admin" else "regional_user"
        if not new_username or not new_password:
            st.error("Username and password are required.")
        elif new_password != new_password_confirm:
            st.error("Passwords do not match.")
        elif new_username in users_df["username"].values:
            st.error(f"Username '{new_username}' already exists.")
        else:
            try:
                create_user(new_username, hash_password(new_password), new_role, new_region)
                st.success(f"User '{new_username}' created as {ROLE_LABELS[new_role]}"
                           + (f" for {new_region}." if new_region else "."))
                _refresh()
            except Exception as e:
                st.error(f"Could not create user: {e}")

st.divider()

st.subheader("Edit User")
if users_df.empty:
    st.info("No users to edit.")
else:
    edit_username = st.selectbox("Select user", users_df["username"], key="edit_user_select")
    current = users_df[users_df["username"] == edit_username].iloc[0]
    current_role_label = ROLE_LABELS.get(current["user_role"], current["user_role"])

    with st.form("edit_user_form"):
        e1, e2 = st.columns(2)
        edit_role_label = e1.selectbox(
            "Role", ["Regional User", "Super Admin"],
            index=["Regional User", "Super Admin"].index(current_role_label),
        )
        edit_region_default = current["region"] if pd.notna(current["region"]) else REGIONS[0]
        edit_region = None
        if edit_role_label == "Regional User":
            edit_region = e2.selectbox(
                "Region", REGIONS,
                index=REGIONS.index(edit_region_default) if edit_region_default in REGIONS else 0,
            )

        st.markdown("Leave password fields blank to keep the current password.")
        e3, e4 = st.columns(2)
        new_pw = e3.text_input("New Password", type="password", key="edit_pw")
        new_pw_confirm = e4.text_input("Confirm New Password", type="password", key="edit_pw_confirm")

        edit_submitted = st.form_submit_button("Save Changes", type="primary")
        if edit_submitted:
            edit_role = "super_admin" if edit_role_label == "Super Admin" else "regional_user"

            if edit_role != "super_admin" and current["user_role"] == "super_admin" and count_super_admins() <= 1:
                st.error("Cannot demote the last remaining Super Admin.")
            elif new_pw or new_pw_confirm:
                if new_pw != new_pw_confirm:
                    st.error("New password fields do not match.")
                else:
                    update_user(
                        edit_username, role=edit_role, region=edit_region,
                        region_explicit=True, password_hash=hash_password(new_pw),
                    )
                    st.success(f"'{edit_username}' updated (password reset).")
                    _refresh()
            else:
                update_user(edit_username, role=edit_role, region=edit_region, region_explicit=True)
                st.success(f"'{edit_username}' updated.")
                _refresh()

st.divider()

st.subheader("Delete User")
if users_df.empty:
    st.info("No users to delete.")
else:
    del_username = st.selectbox("Select user", users_df["username"], key="delete_user_select")
    confirm = st.checkbox(f"I understand this will permanently delete '{del_username}'.", key="delete_user_confirm")
    if st.button("Delete User", type="primary"):
        target = users_df[users_df["username"] == del_username].iloc[0]
        if del_username == st.session_state.get("username"):
            st.error("You cannot delete your own logged-in account.")
        elif not confirm:
            st.error("Please tick the confirmation checkbox first.")
        elif target["user_role"] == "super_admin" and count_super_admins() <= 1:
            st.error("Cannot delete the last remaining Super Admin.")
        else:
            delete_user(del_username)
            st.success(f"'{del_username}' deleted.")
            _refresh()
