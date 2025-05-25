# shorts_app/youtube_uploader.py

import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# This file should be in the same directory as manage.py
CLIENT_SECRETS_FILE = "client_secret.json"
API_NAME = 'youtube'
API_VERSION = 'v3'
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']


def get_authenticated_service():
    credentials = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first time.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            credentials = pickle.load(token)

    # If there are no (valid) credentials available, let the user log in.
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            # This will run a local server and print a URL for the user to authorize
            credentials = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(credentials, token)

    return build(API_NAME, API_VERSION, credentials=credentials)


def upload_video(file_path, title, description, tags, privacy_status="private"):
    youtube = get_authenticated_service()

    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': '22'  # Default to 'People & Blogs'. See API docs for other categories.
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': False
        }
    }

    try:
        media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media
        )
        response = request.execute()
        return {'status': 'success', 'video_id': response.get('id')}
    except HttpError as e:
        # Specifically check for quota exhausted error
        if e.resp.status == 403 and 'quotaExceeded' in str(e.content):
            return {'status': 'error', 'message': 'YouTube API daily upload quota exceeded.'}
        else:
            return {'status': 'error', 'message': f'An API error occurred: {e}'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}