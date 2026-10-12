import base64
import hashlib
import hmac
import os
import sqlite3
import uuid

import streamlit as st
from dotenv import load_dotenv
from groq import Groq
from pypdf import PdfReader

load_dotenv()
client = Groq()

TEXT_MODEL = "openai/gpt-oss-20b"
VISION_MODEL = "qwen/qwen3.8-27b"
MAX_FILE_CHARS = 4000
MAX_FILES_IN_MEMORY = 2
SYSTEM = (
    "You are a helpful assistant for <for study>. "
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
    c.execute(
        "CREATE TABLE IF NOT EXISTS chat_files (id INTEGER PRIMARY KEY "
        "AUTOINCREMENT, chat_id TEXT, name TEXT, text TEXT)"
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

def save_file_text(chat_id, name, text):
    with conn() as c:
        c.execute(
            "INSERT INTO chat_files (chat_id, name, text) VALUES (?, ?, ?)",
            (chat_id, name, text),
        )

def load_file_texts(chat_id, user):
    # newest files first; join on username so users only see their own
    with conn() as c:
        return c.execute(
            "SELECT f.name, f.text FROM chat_files f "
            "JOIN chats ch ON ch.chat_id = f.chat_id "
            "WHERE f.chat_id=? AND ch.username=? ORDER BY f.id DESC LIMIT ?",
            (chat_id, user, MAX_FILES_IN_MEMORY),
        ).fetchall()

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
def build_content(text, files):
    """Returns (content for Groq, has_image, file names, file texts)."""
    images = []
    names = []
    file_texts = []
    for f in files:
        names.append(f.name)
        if f.type.startswith("image/"):
            b64 = base64.b64encode(f.getvalue()).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{f.type};base64,{b64}"},
            })
        elif f.type == "application/pdf":
            reader = PdfReader(f)
            doc = "\n".join(p.extract_text() or "" for p in reader.pages)
            file_texts.append((f.name, doc[:MAX_FILE_CHARS]))
        else:
            doc = f.getvalue().decode("utf-8", errors="ignore")
            file_texts.append((f.name, doc[:MAX_FILE_CHARS]))

    full_text = text or "Please look at the attached file(s)."
    if images:
        content = [{"type": "text", "text": full_text}] + images
    else:
        content = full_text
    return content, bool(images), names, file_texts


prompt = st.chat_input(
    "Type your message...",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "pdf", "txt", "csv"],
)

if prompt:
    chat_id = st.session_state.chat_id
    user_text = prompt.text or ""
    files = prompt.files

    content, has_image, names, file_texts = build_content(user_text, files)
    model = VISION_MODEL if has_image else TEXT_MODEL

    # plain text version that gets shown and saved in chat.db
    saved_text = user_text
    if names:
        saved_text += "\n\n📎 " + ", ".join(names)

    st.session_state.messages.append({"role": "user", "content": saved_text})
    if len(st.session_state.messages) == 1:
        create_chat(chat_id, user, (user_text or names[0])[:50])  # first message = title
    save_message(chat_id, "user", saved_text)

    # remember uploaded file text for the rest of this chat
    for name, text in file_texts:
        save_file_text(chat_id, name, text)

    system = SYSTEM
    docs = load_file_texts(chat_id, user)
    if docs:
        system += "\n\nThe user uploaded these files in this chat. Use them to answer questions:"
        for name, text in docs:
            system += f"\n\n[File: {name}]\n{text}"

    with st.chat_message("user"):
        st.markdown(saved_text)
        for f in files:
            if f.type.startswith("image/"):
                st.image(f, width=250)

    keep = 4 if has_image else 10
    history = st.session_state.messages[-keep:-1] + [
        {"role": "user", "content": content}
    ]

    with st.chat_message("assistant"):
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}] + history,
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