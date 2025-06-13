import torch
from diffusers import AnimateDiffPipeline, DDIMScheduler, MotionAdapter
from diffusers.utils import export_to_gif

# Low-memory configuration
model_id = "cagliostrolab/animagine-xl-3.1"  # Anime-optimized model
motion_adapter = MotionAdapter.from_pretrained(
    "guoyww/animatediff-motion-adapter-v1-5-2",
    torch_dtype=torch.float16
)

pipe = AnimateDiffPipeline.from_pretrained(
    model_id,
    motion_adapter=motion_adapter,
    torch_dtype=torch.float16,
    variant="fp16",
    use_safetensors=True
)

# Memory optimization techniques
pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config)
pipe.enable_vae_slicing()
pipe.enable_model_cpu_offload()
pipe.enable_attention_slicing(1)

# Generation parameters
prompt = "anime style, magical girl transformation, sparkles, vibrant colors, high quality"
negative_prompt = "low quality, bad anatomy, blurry, shaky camera"

with torch.inference_mode():
    output = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        num_frames=12,  # Reduced frames for lower VRAM
        height=512,
        width=512,
        guidance_scale=7.0,
        num_inference_steps=20,
        generator=torch.Generator().manual_seed(42),
    )

export_to_gif(output.frames, "anime_magic.gif")
