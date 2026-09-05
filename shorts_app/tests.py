from unittest.mock import patch, MagicMock
from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
import tempfile

from .models import DownloadedVideo, GeneratedShort
from .views import get_youtube_id, YOUTUBE_COOKIE_FILE

TEST_MEDIA_DIR = tempfile.mkdtemp()


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": TEST_MEDIA_DIR},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
)
class BaseStorageTestCase(TestCase):
    pass


class GetYoutubeIdTest(TestCase):
    def test_standard_url(self):
        self.assertEqual(get_youtube_id('https://www.youtube.com/watch?v=dQw4w9WgXcQ'), 'dQw4w9WgXcQ')

    def test_short_url(self):
        self.assertEqual(get_youtube_id('https://youtu.be/dQw4w9WgXcQ'), 'dQw4w9WgXcQ')

    def test_embed_url(self):
        self.assertEqual(get_youtube_id('https://www.youtube.com/embed/dQw4w9WgXcQ'), 'dQw4w9WgXcQ')

    def test_none_url(self):
        self.assertIsNone(get_youtube_id(None))

    def test_empty_url(self):
        self.assertIsNone(get_youtube_id(''))

    def test_invalid_url(self):
        self.assertIsNone(get_youtube_id('https://example.com'))

    def test_mobile_url(self):
        self.assertEqual(get_youtube_id('https://m.youtube.com/watch?v=abc123'), 'abc123')


class AuthenticationTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='testpass123'
        )

    def test_unauthenticated_dashboard_redirects(self):
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_authenticated_dashboard_access(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 200)

    def test_login_success(self):
        response = self.client.post('/login/', {
            'username': 'testuser',
            'password': 'testpass123',
        })
        self.assertEqual(response.status_code, 302)

    def test_login_failure(self):
        response = self.client.post('/login/', {
            'username': 'testuser',
            'password': 'wrongpass',
        })
        self.assertEqual(response.status_code, 200)

    def test_register_success(self):
        response = self.client.post('/register/', {
            'first_name': 'New User',
            'email': 'new@example.com',
            'username': 'newuser',
            'password1': 'ComplexPass123!',
            'password2': 'ComplexPass123!',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='newuser').exists())

    def test_logout(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/logout/')
        self.assertEqual(response.status_code, 302)

    def test_landing_page_public(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_pricing_page_public(self):
        response = self.client.get('/pricing/')
        self.assertEqual(response.status_code, 200)


class UserIsolationTest(BaseStorageTestCase):
    def setUp(self):
        self.client = Client()
        self.user_a = User.objects.create_user(
            username='user_a', email='a@test.com', password='pass_a_123'
        )
        self.user_b = User.objects.create_user(
            username='user_b', email='b@test.com', password='pass_b_123'
        )
        self.video_a = DownloadedVideo.objects.create(
            video_id='vid_a', user=self.user_a, title='Video A', duration=100,
            file_path='videos/vid_a.mp4'
        )
        self.video_b = DownloadedVideo.objects.create(
            video_id='vid_b', user=self.user_b, title='Video B', duration=200,
            file_path='videos/vid_b.mp4'
        )
        self.short_a = GeneratedShort.objects.create(
            parent_video=self.video_a, user=self.user_a, title='Short A',
            short_path='shorts/short_a.mp4', start_time='00:00', end_time='00:30'
        )
        self.short_b = GeneratedShort.objects.create(
            parent_video=self.video_b, user=self.user_b, title='Short B',
            short_path='shorts/short_b.mp4', start_time='00:00', end_time='00:30'
        )

    def test_user_a_cannot_see_user_b_videos(self):
        self.client.login(username='user_a', password='pass_a_123')
        response = self.client.get('/dashboard/')
        self.assertContains(response, 'Video A')
        self.assertNotContains(response, 'Video B')

    def test_user_b_cannot_see_user_a_videos(self):
        self.client.login(username='user_b', password='pass_b_123')
        response = self.client.get('/dashboard/')
        self.assertContains(response, 'Video B')
        self.assertNotContains(response, 'Video A')

    def test_user_a_cannot_delete_user_b_video(self):
        self.client.login(username='user_a', password='pass_a_123')
        response = self.client.post(f'/delete_video/vid_b/')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DownloadedVideo.objects.filter(video_id='vid_b').exists())

    def test_user_b_cannot_delete_user_a_video(self):
        self.client.login(username='user_b', password='pass_b_123')
        response = self.client.post(f'/delete_video/vid_a/')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DownloadedVideo.objects.filter(video_id='vid_a').exists())

    def test_user_a_cannot_delete_user_b_short(self):
        self.client.login(username='user_a', password='pass_a_123')
        response = self.client.post(f'/delete_short/{self.short_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_user_b_cannot_delete_user_a_short(self):
        self.client.login(username='user_b', password='pass_b_123')
        response = self.client.post(f'/delete_short/{self.short_a.id}/')
        self.assertEqual(response.status_code, 404)

    def test_user_b_cannot_generate_short_from_user_a_video(self):
        self.client.login(username='user_b', password='pass_b_123')
        response = self.client.post('/generate_short/', data={
            'video_id': 'vid_a',
            'clip_data': {'start_time': '00:00', 'end_time': '00:30', 'title': 'Stolen'},
            'aspect_ratio': '9:16',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_user_cannot_process_video(self):
        response = self.client.post('/process_video/', {'video_url': 'https://youtu.be/abc'})
        self.assertEqual(response.status_code, 302)

    def test_unauthenticated_user_cannot_generate_short(self):
        response = self.client.post('/generate_short/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 302)

    def test_unauthenticated_user_cannot_delete(self):
        response = self.client.post('/delete_video/vid_a/')
        self.assertEqual(response.status_code, 302)

    def test_user_a_cannot_download_user_b_short(self):
        self.client.login(username='user_a', password='pass_a_123')
        # User A cannot access user B's short -> 404
        response = self.client.get(f'/download_short/{self.short_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_owner_can_delete_own_video(self):
        self.client.login(username='user_a', password='pass_a_123')
        response = self.client.post('/delete_video/vid_a/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DownloadedVideo.objects.filter(video_id='vid_a').exists())


class CookieFileTest(TestCase):
    def test_cookie_file_empty_when_not_configured(self):
        with patch.dict('os.environ', {'YOUTUBE_COOKIE_FILE': ''}, clear=False):
            from importlib import reload
            import shorts_app.views as views_module
            reload(views_module)
            self.assertEqual(views_module.YOUTUBE_COOKIE_FILE, '')

    def test_cookie_file_invalid_path_does_not_crash(self):
        with patch.dict('os.environ', {'YOUTUBE_COOKIE_FILE': '/nonexistent/path/cookies.txt'}, clear=False):
            from importlib import reload
            import shorts_app.views as views_module
            reload(views_module)
            self.assertEqual(views_module.YOUTUBE_COOKIE_FILE, '')


class StorageConfigurationTest(TestCase):
    def test_default_storage_is_configured(self):
        from django.core.files.storage import default_storage
        self.assertIsNotNone(default_storage)

    def test_video_model_file_fields(self):
        video = DownloadedVideo(
            video_id='test123', title='Test', duration=60, file_path='videos/test.mp4'
        )
        self.assertEqual(video.file_path.name, 'videos/test.mp4')

    def test_short_model_file_fields(self):
        video = DownloadedVideo.objects.create(
            video_id='test_parent', title='Parent', duration=60, file_path='videos/test.mp4'
        )
        short = GeneratedShort(
            parent_video=video, title='Test Short',
            short_path='shorts/test.mp4', start_time='00:00', end_time='00:30'
        )
        self.assertEqual(short.short_path.name, 'shorts/test.mp4')


class ViewsSecurityTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser', password='testpass123'
        )

    def test_process_video_requires_post(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/process_video/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'error')

    def test_generate_short_requires_post(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/generate_short/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'error')

    def test_process_video_invalid_url(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.post('/process_video/', {'video_url': 'not-a-url'})
        data = response.json()
        self.assertEqual(data['status'], 'error')

    def test_check_progress_unknown_task(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/check_progress/nonexistent/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'PENDING')
