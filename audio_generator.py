# audio_generator.py
import os
from transformers import AutoProcessor, AutoModel


class AudioGenerator:
    def __init__(self):
        self.processor = AutoProcessor.from_pretrained("suno/bark-small")
        self.model = AutoModel.from_pretrained("suno/bark-small")

    def generate_audio(self, scenes: list) -> str:
        """Generate audio narration for the scenes"""
        inputs = self.processor(text=" ".join(scenes), return_tensors="pt")
        audio_array = self.model.generate(**inputs)

        audio_path = "narration.wav"
        audio_array.export(audio_path, format="wav")
        return audio_path
