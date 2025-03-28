# gpt_handler.py
import os
import toml
from transformers import pipeline
SECRETS_FILE_PATH = os.path.join(os.getcwd(), "secrete.toml")
secrets = toml.load(SECRETS_FILE_PATH)
HUGGINGFACE_API_TOKEN = secrets["api_token"]
class GPTHandler:
    def __init__(self):
        self.repo_id = "HuggingFaceH4/zephyr-7b-beta"
        self.huggingfacehub_api_token = HUGGINGFACE_API_TOKEN

    def generate_scenes(self, prompt: str) -> list:
        """Generate video scenes using Zephyr-7B-β"""


        llm = pipeline(
            "text-generation",
            model=self.repo_id,
            use_auth_token=self.huggingfacehub_api_token,
            max_new_tokens=512,
        )

        response = llm(prompt)
        scenes = response[0]["generated_text"].split("\n")
        return [line.split("-")[0].strip() for line in scenes if line]