# main.py
import os
import streamlit as st
from gpt_handler import GPTHandler
from image_generator import ImageGenerator
from audio_generator import AudioGenerator
from video_creator import VideoCreator
#from youtube_uploader import YouTubeUploader

def process_prompt(user_input: str):
    gpt_handler = GPTHandler()
    scenes = gpt_handler.generate_scenes(user_input)

    image_generator = ImageGenerator()
    image_paths = image_generator.generate_images(scenes)

    audio_generator = AudioGenerator()
    audio_path = audio_generator.generate_audio(scenes)

    video_creator = VideoCreator()
    video_path = video_creator.create_video(image_paths, audio_path)

    #uploader = YouTubeUploader()
    #result = uploader.upload_video(video_path)
    
    #return result

if __name__ == "__main__":
    user_prompt = st.text_input("Enter your video concept: ")
    result = process_prompt(user_prompt)
    st.write(f"Video uploaded: {result['id']}")
