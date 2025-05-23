# gpt_handler.py
import os
import toml
from transformers import pipeline
SECRETS_FILE_PATH = os.path.join(os.getcwd(), "secrete.toml")
secrets = toml.load(SECRETS_FILE_PATH)
HUGGINGFACE_API_TOKEN = secrets["api_token"]

# class GPTHandler:
#     def __init__(self):
#         self.repo_id = "HuggingFaceH4/zephyr-7b-beta"
#         self.huggingfacehub_api_token = HUGGINGFACE_API_TOKEN
#
#     def generate_scenes(self, prompt: str) -> list:
#         """Generate video scenes using Zephyr-7B-β"""
#
#
#         llm = pipeline(
#             "text-generation",
#             model=self.repo_id,
#             use_auth_token=self.huggingfacehub_api_token,
#             max_new_tokens=512,
#         )
#
#         response = llm(prompt)
#         scenes = response[0]["generated_text"].split("\n")
#         return [line.split("-")[0].strip() for line in scenes if line]


from transformers import pipeline

class GPTHandler:
    def __init__(self):
        # Use the facebook/opt-iml-max-1.3b model for text generation
        self.repo_id = "facebook/opt-iml-max-1.3b"
        # You can add authentication token logic here if needed

    def generate_scenes(self, prompt: str) -> str:
        """Generate an answer to the prompt using OPT-IML Max 1.3B."""
        generator = pipeline(
            "text-generation",
            model=self.repo_id,
            device=0,
            max_new_tokens=30  # Limit output length for concise answers
        )
        response = generator(prompt)
        # Return the generated text from the response dictionary
        return response[0]["generated_text"]

# Example usage:
# handler = GPTHandler()
# answer = handler.generate_answer("What is the capital of USA?")
# print(answer)
