import requests

from app.schemas.chat import ChatHistoryMessage


class ChatService:

    def chat(
        self,
        history: list[ChatHistoryMessage],
        reference_history: list[ChatHistoryMessage],
    ):

        url = "http://localhost:11434/api/chat"

        messages = [
            {
                "role": "system",
                "content": (
                        "You are a warm, empathetic Tamil assistant. OUTPUT RULE: reply only in "
                        "natural Thanglish: Tamil spoken words written using English/Roman letters. "
                        "Never write a full English sentence and never use Tamil Unicode script, even if "
                        "the user uses English. English is allowed only for unavoidable product names, "
                        "movie titles, or technical terms. Before replying, silently identify the user's "
                        "emotion (for example happy, worried, sad, angry, stressed, or confused) and "
                        "respond with an appropriate short, caring acknowledgement before the helpful "
                        "answer. For safety refusals, explain the boundary kindly in Thanglish. Use the "
                        "complete conversation history to answer follow-up questions consistently."
                )
            }
        ]

        if reference_history:
            messages.append({
                "role": "system",
                "content": "Relevant messages from earlier saved chats follow. Use them only when relevant."
            })
            messages.extend(message.model_dump() for message in reference_history)

        messages.extend(message.model_dump() for message in history)

        body = {
            "model": "llama3.2:latest",
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2}
        }

        response = requests.post(url, json=body)
        response.raise_for_status()

        return response.json()["message"]["content"]
