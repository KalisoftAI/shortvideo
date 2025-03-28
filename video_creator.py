# video_creator.py
from moviepy.editor import *

class VideoCreator:
    def create_video(self, image_paths: list, audio_path: str) -> str:
        """Combine images and audio into a video"""
        clips = [ImageClip(img).set_duration(5) for img in image_paths]
        video = concatenate_videoclips(clips, method="compose")
        
        video_audio = AudioFileClip(audio_path)
        video = video.set_audio(video_audio)
        
        output_path = "output.mp4"
        video.write_videofile(output_path, fps=24, codec="libx264")
        
        return output_path
