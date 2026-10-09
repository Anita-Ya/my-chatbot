from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq()  # reads GROQ_API_KEY from environment

SYSTEM = (
    "You are a helpful assistant for <describe your use case>. "
    "Keep answers short and clear. If you don't know something, say so."
)

history = [{"role": "system", "content": SYSTEM}]

def chat(user_msg):
    history.append({"role": "user", "content": user_msg})
    resp = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=history,
        max_tokens=1000,
    )
    reply = resp.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    return reply

if __name__ == "__main__":
    print("Chatbot ready. Type 'quit' to exit.")
    while True:
        msg = input("You: ")
        if msg.lower() in ("quit", "exit"):
            break
        print("Bot:", chat(msg))