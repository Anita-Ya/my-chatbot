import base64
import hashlib
import hmac
import os
import uuid
from contextlib import contextmanager

import psycopg2
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

# ---------- Database (Postgres) ----------
def get_db_url():
    url = os.getenv("DATABASE_URL")  # from .env locally
    if url:
        return url
    try:
        return st.secrets["DATABASE_URL"]  # from Streamlit Cloud secrets
    except Exception:
        return None

DATABASE_URL = get_db_url()
if not DATABASE_URL:
    st.error("DATABASE_URL is not set. Add it to .env (local) or Streamlit Secrets (cloud).")
    st.stop()

@contextmanager
def db():
    """Open a connection, give a cursor, commit at the end, always close."""
    c = psycopg2.connect(DATABASE_URL)
    try:
        with c:
            with c.cursor() as cur:
                yield cur
    finally:
        c.close()

@st.cache_resource
def init_db():
    with db() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, "
            "salt TEXT, pw_hash TEXT)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS chats (chat_id TEXT PRIMARY KEY, "
            "username TEXT, title TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, "
            "chat_id TEXT, role TEXT, content TEXT)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS chat_files (id SERIAL PRIMARY KEY, "
            "chat_id TEXT, name TEXT, text TEXT)"
        )
    return True

init_db()

# ---------- Auth ----------
def hash_pw(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 200_000
    ).hex()

def create_user(username, password):
    salt = os.urandom(16).hex()
    try:
        with db() as cur:
            cur.execute(
                "INSERT INTO users (username, salt, pw_hash) VALUES (%s, %s, %s)",
                (username, salt, hash_pw(password, salt)),
            )
        return True
    except psycopg2.IntegrityError:
        return False  # username already taken

def verify_user(username, password):
    with db() as cur:
        cur.execute(
            "SELECT salt, pw_hash FROM users WHERE username=%s", (username,)
        )
        row = cur.fetchone()
    if not row:
        return False
    return hmac.compare_digest(hash_pw(password, row[0]), row[1])

# ---------- Chat storage ----------
def list_chats(user):
    with db() as cur:
        cur.execute(
            "SELECT chat_id, title FROM chats WHERE username=%s "
            "ORDER BY created DESC",
            (user,),
        )
        return cur.fetchall()

def create_chat(chat_id, user, title):
    with db() as cur:
        cur.execute(
            "INSERT INTO chats (chat_id, username, title) VALUES (%s, %s, %s) "
            "ON CONFLICT (chat_id) DO NOTHING",
            (chat_id, user, title),
        )

def rename_chat(chat_id, user, new_title):
    # the username check makes sure users can only rename their own chats
    with db() as cur:
        cur.execute(
            "UPDATE chats SET title=%s WHERE chat_id=%s AND username=%s",
            (new_title, chat_id, user),
        )

def delete_chat(chat_id, user):
    with db() as cur:
        cur.execute(
            "SELECT 1 FROM chats WHERE chat_id=%s AND username=%s",
            (chat_id, user),
        )
        if not cur.fetchone():
            return  # not this user's chat, do nothing
        cur.execute("DELETE FROM messages WHERE chat_id=%s", (chat_id,))
        cur.execute("DELETE FROM chat_files WHERE chat_id=%s", (chat_id,))
        cur.execute("DELETE FROM chats WHERE chat_id=%s", (chat_id,))

def load_messages(chat_id, user):
    # the join on username makes sure users can only open their own chats
    with db() as cur:
        cur.execute(
            "SELECT m.role, m.content FROM messages m "
            "JOIN chats ch ON ch.chat_id = m.chat_id "
            "WHERE m.chat_id=%s AND ch.username=%s ORDER BY m.id",
            (chat_id, user),
        )
        rows = cur.fetchall()
    return [{"role": r, "content": t} for r, t in rows]

def save_message(chat_id, role, content):
    with db() as cur:
        cur.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (%s, %s, %s)",
            (chat_id, role, content),
        )

def save_file_text(chat_id, name, text):
    with db() as cur:
        cur.execute(
            "INSERT INTO chat_files (chat_id, name, text) VALUES (%s, %s, %s)",
            (chat_id, name, text),
        )

def load_file_texts(chat_id, user):
    # newest files first; join on username so users only see their own
    with db() as cur:
        cur.execute(
            "SELECT f.name, f.text FROM chat_files f "
            "JOIN chats ch ON ch.chat_id = f.chat_id "
            "WHERE f.chat_id=%s AND ch.username=%s ORDER BY f.id DESC LIMIT %s",
            (chat_id, user, MAX_FILES_IN_MEMORY),
        )
        return cur.fetchall()

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
        col_open, col_menu = st.columns([4, 1])
        with col_open:
            if st.button(title[:30], key=f"open_{cid}", use_container_width=True):
                st.session_state.chat_id = cid
                st.session_state.messages = load_messages(cid, user)
        with col_menu:
            with st.popover("⋮"):
                new_title = st.text_input("Chat name", value=title, key=f"name_{cid}")
                if st.button("Save name", key=f"save_{cid}", use_container_width=True):
                    if new_title.strip():
                        rename_chat(cid, user, new_title.strip()[:50])
                        st.rerun()
                if st.button("🗑️ Delete chat", key=f"del_{cid}", use_container_width=True):
                    delete_chat(cid, user)
                    if st.session_state.chat_id == cid:
                        st.session_state.chat_id = uuid.uuid4().hex[:8]
                        st.session_state.messages = []
                    st.rerun()

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

    # plain text version that gets shown and saved in the database
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