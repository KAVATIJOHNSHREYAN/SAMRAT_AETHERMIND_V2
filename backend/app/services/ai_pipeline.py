import os
import asyncio
import time
from typing import AsyncGenerator, List, Dict, Optional
import google.generativeai as genai

from app.db.vector_store import similarity_search

# Global dictionary to track last request execution times to avoid API rate limits
_user_last_request_time = {}


async def generate_response_stream(
    query: str,
    chat_history: List[Dict[str, str]],
    chat_mode: str = "general",
    model_name: str = "gemini-2.5-flash",
    temperature: float = 0.7,
    system_prompt: str = None,
    enable_rag: bool = True,
    rag_k: int = 3,
    openai_key: str = None,
    gemini_key: str = None,
    attachments: Optional[List[dict]] = None,
    user_id: Optional[str] = None
) -> AsyncGenerator[str, None]:

    # 0. Pre-emptive Rate Limiting
    REQUEST_DELAY = 4.0  # seconds
    tracking_key = user_id or "anonymous"
    current_time = time.time()
    last_allowed_time = _user_last_request_time.get(tracking_key, 0.0)

    if current_time < last_allowed_time + REQUEST_DELAY:
        next_allowed_time = last_allowed_time + REQUEST_DELAY
        wait_time = next_allowed_time - current_time
        yield f"Please wait a moment ({wait_time:.1f}s) before sending another message to avoid API rate limits...\n\n"
        _user_last_request_time[tracking_key] = next_allowed_time
        await asyncio.sleep(wait_time)
    else:
        _user_last_request_time[tracking_key] = current_time

    context_str = ""

    # 1. RAG Search
    if enable_rag and chat_mode in ["general", "voice"]:
        api_key = gemini_key or openai_key
        docs = similarity_search(query, k=rag_k, api_key=api_key)
        if docs:
            context_str = "\n".join(
                [f"- {doc.page_content}" for doc in docs]
            )

    # 2. System Instructions
    system_instructions = (
        "You are AetherMind, an advanced full-stack AI orchestrator. "
        "Mister Samrat created me for assistance. If anyone asks who created you or who your creator is, "
        "always reply clearly that 'Mister Samrat created me for assistance'. "
        "Provide professional, clean, and concise answers."
    )

    if system_prompt:
        system_instructions = system_prompt
    else:
        if chat_mode == "coding":
            system_instructions += " Focus on writing clean code with proper syntax and comments."
        elif chat_mode == "debug":
            system_instructions += " Focus on debugging errors and fixing issues."
        elif chat_mode == "voice":
            system_instructions += " Give short and conversational responses."

    # 3. Provider Detection
    is_openai_model = model_name.startswith("gpt-")
    effective_openai_key = openai_key or os.getenv("OPENAI_API_KEY")
    effective_gemini_key = gemini_key or os.getenv("GEMINI_API_KEY")

    print("Gemini Key Loaded:", effective_gemini_key)

    # ---------------- OPENAI ----------------
    if is_openai_model and effective_openai_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=effective_openai_key)

            messages = [{"role": "system", "content": system_instructions}]

            user_content = query
            if context_str:
                user_content = f"Background Context:\n{context_str}\n\nUser Query: {query}"

            for msg in chat_history[-5:]:
                role = "assistant" if msg["sender"] == "assistant" else "user"
                messages.append({
                    "role": role,
                    "content": msg["content"]
                })

            messages.append({
                "role": "user",
                "content": user_content
            })

            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model_name,
                messages=messages,
                temperature=temperature,
                stream=True
            )

            for chunk in response:
                if chunk.choices:
                    content = chunk.choices[0].delta.content
                    if content:
                        yield content
            return

        except Exception as e:
            print("OpenAI Error:", e)

    # ---------------- GEMINI ----------------
    elif effective_gemini_key:
        try:
            genai.configure(api_key=effective_gemini_key)

            generation_config = genai.GenerationConfig(
                temperature=temperature
            )

            selected_model = model_name

            print("Using Gemini Model:", selected_model)

            model = genai.GenerativeModel(
                model_name=selected_model,
                system_instruction=system_instructions
            )

            prompt_parts = []

            if context_str:
                prompt_parts.append(
                    f"Background Context:\n{context_str}\n"
                )

            if chat_history:
                prompt_parts.append("Conversation History:")

                for msg in chat_history[-5:]:
                    sender = "User" if msg["sender"] == "user" else "Assistant"
                    prompt_parts.append(
                        f"{sender}: {msg['content']}"
                    )

            prompt_parts.append(f"User Query: {query}")

            prompt = "\n".join(prompt_parts)

            response = await asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config=generation_config,
                stream=True
            )

            for chunk in response:
                if chunk.text:
                    yield chunk.text

            return

        except Exception as e:
            error_str = str(e)
            print("Gemini Error:", error_str)
            if "429" in error_str or "quota" in error_str.lower():
                yield "AetherMind (Rate Limit): You have temporarily hit the free tier API rate limit (usually 15 requests per minute). Please wait about 30 seconds before sending your next message!"
                return

    # ---------------- FALLBACK ----------------
    fallback_replies = {
        "hello": "Hello! AetherMind is online.",
        "who created you": "I was developed as a portfolio chatbot using FastAPI and Next.js by my creator Mister John Shreyan.",
        "what is your name": "I am AetherMind."
    }

    matched_reply = None
    query_lower = query.lower()

    for key, value in fallback_replies.items():
        if key in query_lower:
            matched_reply = value
            break
    if not matched_reply:
        try:
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(query)
            matched_reply = response.text
        except Exception as e:
            matched_reply = f"AI Error: {str(e)}"
    words = matched_reply.split()

    for i, word in enumerate(words):
        if i < len(words) - 1:
            yield word + " "
        else:
            yield word
        await asyncio.sleep(0.05)