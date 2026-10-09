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

# Streamlit reruns the script on every message, so history lives in session_state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Show past messages
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# Handle new input
if prompt := st.chat_input("Type your message..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            stream = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": SYSTEM}]
                         + st.session_state.messages[-20:],  # keep last 20 only
                max_tokens=1000,
                stream=True,
            )
            reply = st.write_stream(
                chunk.choices[0].delta.content or "" for chunk in stream
            )
        except Exception as e:
            reply = f"Error: {e}"
            st.error(reply)

    st.session_state.messages.append({"role": "assistant", "content": reply})