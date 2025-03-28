# youtube_uploader.py
import os
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow

class YouTubeUploader:
    def __init__(self):
        flow = InstalledAppFlow.from_client_secrets_file(
            'client_secrets.json',
            scopes=['https://www.googleapis.com/auth/youtube.upload']
        )
        self.youtube = build('youtube', 'v3', credentials=flow.run_local_server())

    def upload_video(self, video_path: str):
        """Upload the video to YouTube"""
        request = self.youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": "AI Generated Video",
                    "description": "Automatically created with Python AI pipeline"
                },
                "status": {"privacyStatus": "public"}
            },
            media_body=video_path
        )
        
        return request.execute()
