import replicate
from replicate import Client
from dotenv import load_dotenv
import os
import requests
import base64
from typing import Optional
load_dotenv()

REPLICATE_KEY = os.getenv('REPLICATE_KEY')
class VideoGenerator:
    def generate_video(self, prompt: str, ref_image: str) -> str:
        client = Client(api_token=REPLICATE_KEY)
        # model = 'bytedance/seedance-1-pro'
        model = "kwaivgi/kling-v2.5-turbo-pro"
        if not model:
            raise ValueError("Model not found")
        if not prompt:
            raise ValueError("Prompt is required")
        if not ref_image:
            raise ValueError("Reference image is required")
        
        # Ensure the Base64 string has the correct prefix
        if not ref_image.startswith("data:image"):
            ref_image = f"data:image/png;base64,{ref_image}"
        
        input = {
            'prompt': prompt + ", high quality, no skew, no distortion, detailed, cinematic lighting",
            "starting_image": ref_image,
            # 'image': ref_image,
            # 'duration': 3,
            'duration': 5,
        }
        
        # Log the input for debugging
        print("DEBUG: Input to replicate API:", input)
        
        output = client.run(
            model,
            input=input
        )
        if not output:
            raise ValueError("Failed to generate video")
        
        if output:
            if isinstance(output, list):
                video_url = str(output[0])
            else:
                video_url = str(output)
        else:
            print("❌ Failed to generate image")
            return None
        video = self._download_and_encode_video(video_url)
        return video
    def _download_and_encode_video(self, video_url: str) -> Optional[str]:
        """Download a video from a URL and return it as a Base64-encoded string."""
        try:
            # Download the video as bytes
            response = requests.get(video_url, timeout=30)
            response.raise_for_status()
            encoded = base64.b64encode(response.content).decode('utf-8')
            result = f"data:video/mp4;base64,{encoded}"
            print("✅ Video downloaded and encoded:", video_url)
            return result
        except Exception as e:
            print(f"❌ Error downloading/encoding video: {e}")
            return None