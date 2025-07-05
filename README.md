
# 🧠 AI-Powered Video Tools

This repository contains **two Django-based AI projects**:

- 🎬 **YouTube Shorts Generator**  
- 🖼️ **VideoCraft (Image to Video)**  

---

## 📁 Project 1: YouTube Shorts Generator

Automatically generates YouTube Shorts from long-form YouTube videos using subtitles and Gemini AI.

### ⚙️ Key Features

- 📥 Download YouTube videos and subtitles using `yt-dlp`
- 🧠 Extract interesting short-form clips using Gemini AI
- ✂️ Crop/rescale video into Shorts format (9:16 or 16:9)
- ☁️ Upload videos to AWS S3
- 📄 Display progress and video management in the web UI

### 📌 Code Explanation

#### 🔽 Step 1: Download and Process Video

```python
def process_video(request):
    video_url = request.POST.get("video_url")
    video_id = get_youtube_id(video_url)
    thread = threading.Thread(target=long_running_task, args=(video_id,))
    thread.start()
```
- Extracts the YouTube video ID
- Runs processing in a background thread

#### 🔄 Background Task

```python
def long_running_task(video_id):
    subprocess.run(["yt-dlp", "--write-sub", "--convert-subs", "vtt", video_url])
    transcript = read_vtt_transcript(file_path)
    suggestions = get_ai_suggested_clips(transcript, duration)
```
- Downloads the video and subtitles
- Extracts transcript and uses AI for suggestions

#### 🧠 Gemini AI Prompt

```python
def get_ai_suggested_clips(transcript, duration):
    prompt = "Pick 3 viral short-form clips under 60 seconds..."
    response = model.generate_content(prompt)
```
- Uses Gemini to suggest time ranges, titles, and tags

#### ✂️ Generate Short

```python
clip = VideoFileClip(input_path).subclip(start_time, end_time)
clip_resized = clip.resize(height=1080)
background = ColorClip(size=(1920, 1080), color=(0, 0, 0))
final = CompositeVideoClip([background.set_duration(clip.duration), clip_resized.set_position("center")])
```
- Crops, resizes, adds black background

#### ☁️ Upload to S3

```python
short_instance.short_file.save(file_name, ContentFile(video_buffer.getvalue()))
```

---

## 📁 Project 2: VideoCraft (Image to Video Generator)

Generates cinematic videos from uploaded images with transitions, music, and captions.

### ⚙️ Key Features

- 🖼️ Upload multiple images
- 🎵 Add background music (upload or link)
- 🧠 Auto-generate image captions using Gemini
- 🔄 Apply transitions (fade)
- 🎞️ Compile everything into a video using `moviepy`
- ☁️ Upload results to S3

### 📌 Code Explanation

#### 🧱 ProjectImage Model

```python
class ProjectImage(models.Model):
    image_file = models.ImageField(...)
    text_overlay = models.CharField(max_length=200)
    duration = models.FloatField(default=3.0)
```
- Stores uploaded image + caption + duration

#### 📥 Upload Image

```python
@csrf_exempt
def upload_image(request, project_id):
    for f in request.FILES.getlist('images'):
        ProjectImage.objects.create(project=project, image_file=f)
```

#### ✍️ Update Image Details

```python
@csrf_exempt
def update_image_details(request, image_id):
    data = json.loads(request.body)
    image.text_overlay = data['text_overlay']
    image.duration = float(data['duration'])
    image.save()
```

#### 🧠 Gemini Captioning

```python
@csrf_exempt
def generate_text_overlay(request, image_id):
    prompt = "Suggest a caption under 15 words..."
    response = model.generate_content(prompt)
```

#### 🎬 Generate Final Video

```python
def generate_video_core(project_id, ...):
    for image in project.images.order_by('order'):
        img_clip = ImageClip(image.path).set_duration(image.duration)
        txt_clip = TextClip(image.text_overlay, fontsize=50, color='white')
        img_clip = CompositeVideoClip([img_clip, txt_clip.set_position("center")])
        clips.append(fadein(fadeout(img_clip, 1), 1))
    video = concatenate_videoclips(clips)
    video.write_videofile("final.mp4")
```

#### 🎵 Add Music

```python
audio = AudioFileClip(audio_file_url)
video = video.set_audio(audio)
```

#### ☁️ Upload to S3

```python
video_instance.video_file.save("final_video.mp4", ContentFile(buffer.getvalue()))
```

---

## 📦 How to Run Locally

```bash
pip install -r requirements.txt
python manage.py runserver
```

---

## ✅ Technologies Used for this projects

| Tool            | Purpose                |
|-----------------|------------------------|
| Django          | Web framework          |
| yt-dlp          | YouTube download       |
| MoviePy         | Video editing          |
| Gemini AI       | Caption generation     |
| boto3 + S3      | Cloud file storage     |
| threading       | Async background tasks |

---


