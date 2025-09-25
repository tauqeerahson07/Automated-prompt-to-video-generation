import re
import json
import os
import requests
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Nebius API credentials
NEBIUS_API_KEY = os.getenv('NEBIUS_API_KEY')
NEBIUS_API_BASE = os.getenv('NEBIUS_API_BASE')

if not NEBIUS_API_KEY:
    raise ValueError("Nebius_key not found in environment variables")

# Model to use
LLAMA_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"

class ImagePromptGenerator:
    """
    Service class for generating detailed image prompts from scene data using LLM
    """
    
    def __init__(self):
        # Default settings - no user input needed
        self.default_style = "cinematic"
        self.default_quality = "high"
        
        self.style_templates = {
            "cinematic": "cinematic lighting, professional photography, high quality, detailed",
            "artistic": "artistic composition, painterly style, beautiful colors, creative lighting",
            "realistic": "photorealistic, ultra-detailed, sharp focus, natural lighting",
            "fantasy": "fantasy art style, magical atmosphere, ethereal lighting, mystical"
        }

    def generate_image_prompt(self, scenes_response: Dict) -> Dict:
        """
        Generate image prompts from the scenes API response using LLM
        
        Args:
            scenes_response (Dict): The complete API response containing scenes data
        
        Returns:
            Dict: Contains all scene prompts ready for image generation
        """
        try:
            # Extract data from the API response
            if scenes_response.get("status") != "success":
                return {
                    "error": "Invalid scenes data - status not success",
                    "success": False
                }
            
            data = scenes_response.get("data", {})
            scenes = data.get("scenes", [])
            
            if not scenes:
                return {
                    "error": "No scenes found in the response",
                    "success": False
                }
            
            # Generate prompts for each scene using LLM
            scene_prompts = []
            
            for scene in scenes:
                scene_data = {
                    "final_prompt": scene.get("final_prompt", ""),
                    "trigger_word": scene.get("trigger_word", ""),
                    "scene_number": scene.get("scene_number"),
                    "scene_title": scene.get("scene_title", f"Scene {scene.get('scene_number', 'Unknown')}")
                }
                
                # Generate image prompt using LLM (use self, not generator)
                prompt_result = self._generate_image_prompt_with_llm(scene_data)
                prompt_result["scene_number"] = scene_data["scene_number"]
                prompt_result["scene_title"] = scene_data["scene_title"]
                
                scene_prompts.append(prompt_result)
            
            return {
                "success": True,
                "data": {
                    "project_title": data.get("project_title", ""),
                    "original_prompt": data.get("original_prompt", ""),
                    "total_scenes": data.get("total_scenes", len(scene_prompts)),
                    "character_name": data.get("character_name"),
                    "character_exists": data.get("character_exists", False),
                    "scenes": scene_prompts
                }
            }
            
        except Exception as e:
            return {
                "error": f"Error processing scenes data: {str(e)}",
                "success": False
            }

    def _generate_image_prompt_with_llm(self, scene_data: Dict) -> Dict:
        """
        Generate a detailed image prompt from scene description using LLM
        """
        try:
            final_prompt = scene_data.get("final_prompt", "")
            trigger_word = scene_data.get("trigger_word", "")
            scene_title = scene_data.get("scene_title", "")
            
            # System prompt for image prompt generation
            system_prompt = """You are an expert AI image prompt engineer specializing in creating detailed, cinematic prompts for high-quality image generation. 

Your task: Transform scene descriptions into comprehensive image generation prompts.

STYLING GUIDELINES - Always incorporate these elements:
- Cinematic lighting and composition (dramatic lighting, golden hour, soft shadows, etc.)
- Professional photography terms (8K resolution, shallow depth of field, bokeh, etc.)
- Camera angles and perspectives (low-angle, wide shot, close-up, etc.)
- High-quality modifiers (ultra-detailed, masterpiece, professional photography)
- Atmospheric descriptors (moody, ethereal, immersive, etc.)
- Technical excellence terms (sharp focus, perfect composition, HDR)

RULES:
1. Create detailed, visual descriptions suitable for AI image generation
2. Include lighting, composition, camera angles, and artistic style details
3. If a trigger word is provided, incorporate it naturally into the prompt
4. Focus on visual elements: colors, textures, atmosphere, mood
5. Add technical photography and cinematic terms for better quality
6. Always include quality and style modifiers naturally within the description

IMPORTANT: Return ONLY the detailed image prompt text with integrated styling - no JSON, no explanations, no formatting, just the complete cinematic prompt."""

            # Generation prompt
            generation_prompt = f"""Transform this scene description into a detailed, cinematic image generation prompt:

Scene Title: "{scene_title}"
Scene Description: {final_prompt}
Trigger Word: {trigger_word if trigger_word else "none"}

Requirements:
- Make it highly visual and cinematic with professional photography styling
- Include lighting, camera angle, and composition details naturally
- Integrate quality modifiers (8K, professional photography, etc.) seamlessly
- If trigger word is provided, incorporate it naturally in the prompt
- Focus on what can be visually seen in the image
- Add atmospheric and mood descriptors
- Include cinematic and technical terms throughout the description

Create a complete, cinematic image prompt that includes all styling elements naturally integrated into the description."""

            headers = {
                "Authorization": f"Bearer {NEBIUS_API_KEY}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": LLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": generation_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1000
            }

            response = requests.post(
                f"{NEBIUS_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
            )

            if response.status_code == 200:
                result = response.json()
                llm_response = result["choices"][0]["message"]["content"].strip()
                
                # Clean up any unwanted formatting
                # Remove JSON code blocks if present
                llm_response = re.sub(r'```json.*?```', '', llm_response, flags=re.DOTALL)
                # Remove any markdown formatting
                llm_response = re.sub(r'```.*?```', '', llm_response, flags=re.DOTALL)
                # Remove explanatory text
                llm_response = re.sub(r'Here is.*?prompt:', '', llm_response, flags=re.IGNORECASE)
                llm_response = re.sub(r'This prompt.*$', '', llm_response, flags=re.DOTALL)
                
                llm_response = llm_response.strip('"\'')
                
                # Remove escape characters
                llm_response = llm_response.replace('\\"', '"').replace("\\'", "'")
                
                # Clean up extra whitespace
                llm_response = ' '.join(llm_response.split())
                
                final_image_prompt = llm_response if llm_response else f"{final_prompt}, cinematic, high quality, detailed, professional photography, 8K resolution"
                
                return {
                    "image_prompt": final_image_prompt,
                    "negative_prompt": self._generate_negative_prompt(),
                    "original_scene_prompt": final_prompt,
                    "trigger_word": trigger_word,
                    "success": True
                }
            else:
                # Fallback to original method if LLM fails
                return self._generate_fallback_prompt(scene_data)
                
        except Exception as e:
            # Fallback to original method if any error occurs
            return self._generate_fallback_prompt(scene_data)
    def _generate_fallback_prompt(self, scene_data: Dict) -> Dict:
        """
        Fallback method using the original template-based approach
        """
        final_prompt = scene_data.get("final_prompt", "")
        trigger_word = scene_data.get("trigger_word", "")

        # Use default settings
        style = self.default_style
        quality_level = self.default_quality

        # Extract key elements from the scene
        elements = self._extract_scene_elements(final_prompt)

        # Build the detailed prompt (which integrates styles naturally)
        detailed_prompt = self._build_detailed_prompt(
            final_prompt, trigger_word, elements, style, quality_level
        )

        # Generate negative prompt
        negative_prompt = self._generate_negative_prompt()

        return {
            "image_prompt": detailed_prompt,
            "negative_prompt": negative_prompt,
            "original_scene_prompt": final_prompt,
            "trigger_word": trigger_word,
            "success": True
        }
    def _extract_scene_elements(self, scene_description: str) -> Dict:
        """Extract key visual elements from scene description"""
        elements = {
            "character": [],
            "environment": [],
            "lighting": [],
            "objects": [],
            "atmosphere": [],
            "colors": [],
            "textures": [],
            "weather": []
        }
        
        text = scene_description.lower()
        
        # Extract lighting information
        lighting_keywords = [
            "light", "shadow", "bright", "dark", "glow", "shimmer", "filtering", 
            "dappled", "harsh", "soft", "ambient", "dramatic", "sunlight", "mist", "spray"
        ]
        for keyword in lighting_keywords:
            if keyword in text:
                elements["lighting"].append(keyword)
        
        # Extract environment information
        environment_keywords = [
            "forest", "jungle", "desert", "mountain", "ocean", "city", "room", 
            "cave", "valley", "field", "river", "lake", "beach", "garden", "clearing",
            "canopy", "undergrowth", "waterfall", "trees", "thicket"
        ]
        for keyword in environment_keywords:
            if keyword in text:
                elements["environment"].append(keyword)
        
        # Extract colors
        color_pattern = r'\b(green|blue|red|yellow|purple|orange|black|white|brown|golden|silver|emerald|crimson|azure|crystal-clear)\b'
        colors = re.findall(color_pattern, text)
        elements["colors"].extend(colors)
        
        # Extract textures and materials
        texture_pattern = r'\b(leather|metal|wood|stone|fabric|silk|rough|smooth|worn|weathered|damp|wet|dry|thick|intricate)\b'
        textures = re.findall(texture_pattern, text)
        elements["textures"].extend(textures)
        
        # Extract objects and props
        object_pattern = r'\b(jacket|boots|machete|leaves|vines|flowers|waterfall|canopy|shadows|earth|roots|fungi|trunk|tendrils|spray|droplets)\b'
        objects = re.findall(object_pattern, text)
        elements["objects"].extend(objects)
        
        # Extract weather/atmosphere
        weather_pattern = r'\b(humidity|mist|misty|fog|rain|wind|humid|cool|warm|deafening)\b'
        weather = re.findall(weather_pattern, text)
        elements["weather"].extend(weather)
        
        return elements

    def _build_detailed_prompt(self, original_prompt: str, trigger_word: str, 
                            elements: Dict, style: str, quality_level: str) -> str:
        """Build a comprehensive image generation prompt"""
        
        prompt_parts = []
        
        # Add character/trigger word emphasis
        if trigger_word:
            prompt_parts.append(f"{trigger_word}")
        
        # Add main scene description
        prompt_parts.append(original_prompt)
        
        # Add style modifiers
        if style in self.style_templates:
            prompt_parts.append(self.style_templates[style])
        
        # Add quality modifiers
        quality_modifiers = {
            "low": "good quality",
            "medium": "high quality, detailed",
            "high": "ultra high quality, extremely detailed, 8k resolution",
            "ultra": "masterpiece, ultra high quality, extremely detailed, 8k resolution, professional photography"
        }
        
        if quality_level in quality_modifiers:
            prompt_parts.append(quality_modifiers[quality_level])
        
        # Add technical parameters
        technical_params = [
            "sharp focus",
            "perfect composition",
            "trending on artstation",
            "masterpiece"
        ]
        prompt_parts.extend(technical_params)
        
        # Enhance with extracted elements
        if elements.get("lighting") and len(elements["lighting"]) > 0:
            lighting_desc = ", ".join(set(elements["lighting"]))  # Remove duplicates
            prompt_parts.append(f"beautiful {lighting_desc}")
        
        if elements.get("colors") and len(elements["colors"]) > 0:
            color_desc = ", ".join(set(elements["colors"]))
            prompt_parts.append(f"rich {color_desc} tones")
        
        if elements.get("atmosphere") and len(elements["atmosphere"]) > 0:
            atmosphere_desc = ", ".join(set(elements["atmosphere"]))
            prompt_parts.append(f"atmospheric {atmosphere_desc}")
        
        return ", ".join(prompt_parts)

    def _generate_negative_prompt(self) -> str:
        """Generate negative prompt to avoid unwanted elements"""
        negative_elements = [
            "blurry",
            "low quality",
            "pixelated",
            "distorted",
            "ugly",
            "bad anatomy",
            "deformed",
            "duplicate",
            "cropped",
            "out of frame",
            "watermark",
            "text",
            "logo",
            "signature",
            "low resolution",
            "worst quality",
            "bad hands",
            "extra limbs",
            "mutated"
        ]
        
        return ", ".join(negative_elements)