import hashlib
import hmac
import os
import sqlite3
import uuid

import streamlit as st
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq()

MODEL = "openai/gpt-oss-20b"
SYSTEM = (
    "You are a helpful assistant for <describe your use case>. "
    "Keep answers short and clear. If you don't know something, say so."
)

st.set_page_config(page_title="My Chatbot", page_icon="💬")
st.title("💬 My Chatbot")

# ---------- Database ----------
DB = "chat.db"

def conn():
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, "
        "salt TEXT, pw_hash TEXT)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS chats (chat_id TEXT PRIMARY KEY, "
        "username TEXT, title TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY "
        "AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT)"
    )
    return c

# ---------- Auth ----------
def hash_pw(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 200_000
    ).hex()

def create_user(username, password):
    salt = os.urandom(16).hex()
    try:
        with conn() as c:
            c.execute(
                "INSERT INTO users (username, salt, pw_hash) VALUES (?, ?, ?)",
                (username, salt, hash_pw(password, salt)),
            )
        return True
    except sqlite3.IntegrityError:
        return False  # username already taken

def verify_user(username, password):
    with conn() as c:
        row = c.execute(
            "SELECT salt, pw_hash FROM users WHERE username=?", (username,)
        ).fetchone()
    if not row:
        return False
    return hmac.compare_digest(hash_pw(password, row[0]), row[1])

# ---------- Chat storage ----------
def list_chats(user):
    with conn() as c:
        return c.execute(
            "SELECT chat_id, title FROM chats WHERE username=? "
            "ORDER BY created DESC",
            (user,),
        ).fetchall()

def create_chat(chat_id, user, title):
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO chats (chat_id, username, title) VALUES (?, ?, ?)",
            (chat_id, user, title),
        )

def load_messages(chat_id, user):
    # the join on username makes sure users can only open their own chats
    with conn() as c:
        rows = c.execute(
            "SELECT m.role, m.content FROM messages m "
            "JOIN chats ch ON ch.chat_id = m.chat_id "
            "WHERE m.chat_id=? AND ch.username=? ORDER BY m.id",
            (chat_id, user),
        ).fetchall()
    return [{"role": r, "content": t} for r, t in rows]

def save_message(chat_id, role, content):
    with conn() as c:
        c.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )

# ---------- Login / Sign up screen ----------
if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    tab_login, tab_signup = st.tabs(["Login", "Sign up"])

    with tab_login:
        with st.form("login_form"):
            u = st.text_input("Username").strip().lower()
            p = st.text_input("Password", type="password")
            if st.form_submit_button("Login"):
                if verify_user(u, p):
                    st.session_state.user = u
                    st.session_state.chat_id = uuid.uuid4().hex[:8]
                    st.session_state.messages = []
                    st.rerun()
                else:
                    st.error("Wrong username or password.")

    with tab_signup:
        with st.form("signup_form"):
            u = st.text_input("Choose a username").strip().lower()
            p1 = st.text_input("Choose a password", type="password")
            p2 = st.text_input("Confirm password", type="password")
            if st.form_submit_button("Create account"):
                if len(u) < 3:
                    st.error("Username must be at least 3 characters.")
                elif len(p1) < 6:
                    st.error("Password must be at least 6 characters.")
                elif p1 != p2:
                    st.error("Passwords do not match.")
                elif create_user(u, p1):
                    st.success("Account created. Go to the Login tab.")
                else:
                    st.error("That username is already taken.")
    st.stop()

# ---------- From here on, the user is logged in ----------
user = st.session_state.user

with st.sidebar:
    st.write(f"👤 **{user}**")
    if st.button("Logout", use_container_width=True):
        st.session_state.clear()
        st.rerun()
    st.divider()
    if st.button("➕ New chat", use_container_width=True):
        st.session_state.chat_id = uuid.uuid4().hex[:8]
        st.session_state.messages = []
    st.caption("Your chats")
    for cid, title in list_chats(user):
        if st.button(title[:30], key=cid, use_container_width=True):
            st.session_state.chat_id = cid
            st.session_state.messages = load_messages(cid, user)

# ---------- Show old messages ----------
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# ---------- Handle new message ----------
if prompt := st.chat_input("Type your message..."):
    chat_id = st.session_state.chat_id
    st.session_state.messages.append({"role": "user", "content": prompt})
    if len(st.session_state.messages) == 1:
        create_chat(chat_id, user, prompt)  # first message becomes the title
    save_message(chat_id, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            stream = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": SYSTEM}]
                + st.session_state.messages[-20:],
                max_tokens=1000,
                stream=True,
            )
            reply = st.write_stream(
                chunk.choices[0].delta.content or "" for chunk in stream
            )
            failed = False
        except Exception as e:
            reply = f"Error: {e}"
            st.error(reply)
            failed = True

    if not failed:
        st.session_state.messages.append({"role": "assistant", "content": reply})
        save_message(chat_id, "assistant", reply)
        if len(st.session_state.messages) == 2:
            st.rerun()  # so the new chat shows up in the sidebar