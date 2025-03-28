import torch
from diffusers import StableDiffusionPipeline


class ImageGenerator:
    def __init__(self):
        # Load the Stable Diffusion 2 model from Hugging Face
        self.pipe = StableDiffusionPipeline.from_pretrained(
            "stabilityai/stable-diffusion-2",
            torch_dtype=torch.float16  # Use FP16 for better performance
        ).to("cuda")  # Ensure the model runs on GPU for faster generation

        # Enable memory-efficient attention if GPU VRAM is limited
        self.pipe.enable_attention_slicing()

    def generate_images(self, scenes: list) -> list:
        """Generate images for each scene"""
        image_paths = []
        for idx, scene in enumerate(scenes):
            print(f"Generating image for scene {idx + 1}: {scene}")
            # Generate the image based on the text prompt
            image = self.pipe(scene).images[0]

            # Save the generated image locally
            image_path = f"scene_{idx}.png"
            image.save(image_path)
            image_paths.append(image_path)

        return image_paths
