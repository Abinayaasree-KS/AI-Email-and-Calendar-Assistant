import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()  
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Create the model with desired settings
generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
)

# Start a chat session with history
chat_session = model.start_chat(history=[])

# Start an infinite loop for conversation
print("AI Assistant: Hello! You can chat with me. Type 'exit' to end the conversation.")

while True:
    user_input = input("You: ")  # Get user input

    if user_input.lower() in ["exit", "quit", "bye"]:  # Exit condition
        print("AI Assistant: Goodbye! Have a great day! 😊")
        break

    response = chat_session.send_message(user_input)  # Send input to AI
    print("AI Assistant:", response.text)  # Print AI response
