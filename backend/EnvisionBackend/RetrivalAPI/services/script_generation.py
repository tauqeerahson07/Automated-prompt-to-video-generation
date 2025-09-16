import os
import requests
import re
from typing import Dict, List, Any, TypedDict, Optional
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

# Define Scene structure
class SceneData(TypedDict):
    scene_number: int
    title: str
    actors: List[str]
    story: str
    script: str
    dialogue_lines: List[Dict[str, str]]  

# Enhanced WorkflowState for type hints
class WorkflowState(TypedDict):
    concept: str
    num_scenes: int
    creativity_level: str
    script: str
    characters: Dict[str, Dict[str, str]]
    scenes: List[SceneData]
    group_prompt: str
    character_prompts: Dict[str, str]
    scene_to_edit: Optional[int]
    scene_prompt: Optional[str]
    image_prompts: List[str]
    scene_images: List[List[str]]
    errors: List[str]
    status: str
    project_type: str


# def extract_product_details(script: str) -> Dict[str, str]:
#     """
#     Extract product metadata from the script if STEP 3: PRODUCT DETAILS is present.
#     """
#     product = {}

#     # Look for STEP 3: PRODUCT DETAILS section
#     step3_start = script.find("**STEP 3: PRODUCT DETAILS**")
#     if step3_start == -1:
#         step3_start = script.find("STEP 3: PRODUCT DETAILS")
#     if step3_start == -1:
#         step3_start = script.find("**PRODUCT DETAILS**")
    
#     if step3_start != -1:
#         # Extract everything after the product details header
#         section = script[step3_start:]
        
#         # Split into lines for processing
#         lines = section.split('\n')
        
#         for line_idx, line in enumerate(lines):
#             line = line.strip()
#             if not line or line.startswith('**') or line.startswith('---'):
#                 continue
            
#             # Look for field patterns
#             if line.startswith('- **Name:**') or line.startswith('- Name:'):
#                 value = line.replace('- **Name:**', '').replace('- Name:', '').strip()
#                 # Clean up any markdown formatting
#                 value = value.replace('**', '').strip()
#                 if value:
#                     product['name'] = value
                    
#             elif line.startswith('- **Type:**') or line.startswith('- Type:'):
#                 value = line.replace('- **Type:**', '').replace('- Type:', '').strip()
#                 value = value.replace('**', '').strip()
#                 if value:
#                     product['type'] = value
                    
#             elif line.startswith('- **Features:**') or line.startswith('- Features:'):
#                 # For features, we need to collect multiple lines
#                 value = line.replace('- **Features:**', '').replace('- Features:', '').strip()
#                 value = value.replace('**', '').strip()
                
#                 # Collect subsequent feature lines
#                 features_list = []
#                 if value:  # If there's content on the same line
#                     features_list.append(value)
                
#                 # Look ahead for more feature lines (indented with spaces or dashes)
#                 for next_line in lines[line_idx + 1:]:
#                     next_line = next_line.strip()
#                     if not next_line:
#                         continue
#                     if next_line.startswith('- **') or (next_line.startswith('- ') and ':' in next_line):
#                         break  # Next field found
#                     if next_line.startswith('-') or next_line.startswith('  '):
#                         # This is a feature bullet point
#                         feature = next_line.lstrip('- ').strip()
#                         if feature:
#                             features_list.append(feature)
                
#                 if features_list:
#                     product['features'] = '; '.join(features_list)
                    
#             elif line.startswith('- **Visual Style:**') or line.startswith('- Visual Style:'):
#                 value = line.replace('- **Visual Style:**', '').replace('- Visual Style:', '').strip()
#                 value = value.replace('**', '').strip()
#                 if value:
#                     product['visual style'] = value
                    
#             elif line.startswith('- **Brand Feel:**') or line.startswith('- Brand Feel:'):
#                 value = line.replace('- **Brand Feel:**', '').replace('- Brand Feel:', '').strip()
#                 value = value.replace('**', '').strip()
#                 if value:
#                     product['brand feel'] = value

#     return product


def extractScenes(script: str) -> List[SceneData]:
    """
    Extract scene details from the script text and return structured scene data
    """
    scenes = []

    # Updated pattern to handle colon and newlines
    # Pattern for title and summary format
    scene_pattern = (
        r'\*\*Scene\s+(\d+):\s*"([^"]+)"\*\*\s*'               # Scene number & title
        r'(.*?)(?=\*\*Scene|\Z)'                                # Scene content until next scene or end
    )

    matches = re.findall(scene_pattern, script, re.DOTALL | re.IGNORECASE)

    for match in matches:
        scene_number = int(match[0])
        title = match[1].strip()
        content = match[2].strip()

        scenes.append({
            "scene_number": scene_number,
            "title": title,
            "actors": ["{character}"],  # Single character placeholder
            "story": content,
            "script": content,
            "dialogue_lines": []  # No dialogue parsing needed for story format
        })

    return scenes


def generate_script(concept: str,num_scenes: int = 5,creativity_level: str = 'balanced',previous_context: str = None) -> Dict[str, Any]:
    try:
        if creativity_level == "factual":
            temperature = 0.5
            description = "factual and realistic"
        elif creativity_level == "creative":
            temperature = 0.9
            description = "creative and imaginative"
        else:
            temperature = 0.7
            description = "balanced blend of realism and creativity"

        project_type = "story"
        is_commercial = detect_project_type(concept) == 'commercial'
        if is_commercial:
            project_type = 'commercial'

        # System prompt for single character stories or commercials
        if is_commercial:
            system_prompt = """You are a creative director making commercials, adverts, or promos for products. Your task is to create scene summaries for a visual commercial, always using the {product} keyword instead of any real product name.

🚨 CRITICAL RULES 🚨
1. The commercial must focus on a single product, always referenced as {product}
2. Use {product} in ALL scene summaries - NEVER use actual product names
3. Focus on visual storytelling for advertising - show the product in action, benefits, emotions, and appeal
4. No reference sheets or product details section
5. Each scene should be cinematic and suitable for video generation

✅ SCENE FORMAT:
**Scene #: "TITLE"**
[Summary of what happens in the scene using {product} placeholder - focus on actions, emotions, and visual elements]
"""
            generation_prompt = f"""
Create a visual commercial titled "{concept}" with exactly {num_scenes} scenes.

Create exactly {num_scenes} scenes. Each scene should be a visual summary using {{product}} placeholder.

IMPORTANT RULES:
- Use {{product}} instead of any actual product name
- Focus on visual actions, benefits, and emotions
- Describe what can be seen in each scene
- NO dialogue, just visual storytelling
- Each scene should be suitable for image/video generation
- The commercial must be {description} and focus on the concept: {concept}
"""
            if previous_context:
                generation_prompt = (
                    f"Previous context (IMPORTANT: carry over any environmental, setting, or character changes, such as weather, ground conditions, mood or environment, into this next scene):\n{previous_context}\n\n"
                    "Continue the story with the next scene, making sure that any changes (for example, if the ground became damp, or the weather changed, or a character was injured) are reflected and persist in this and all following scenes.\n"
                    + generation_prompt
                )
        else:
            system_prompt = """You are a master storyteller creating visual narratives for single character stories. Your task is to create scene summaries that will be used for image and video generation.

🚨 CRITICAL RULES 🚨
1. Create stories with exactly ONE character
2. Use {character} placeholder in ALL scene summaries - NEVER use actual character names
3. Focus on visual storytelling - describe what happens, not dialogue
4. Keep scenes cinematic and suitable for video generation
5. NO character reference sheets needed

✅ SCENE FORMAT:
**Scene #: "TITLE"**
[Summary of what happens in the scene using {character} placeholder - focus on actions, emotions, and visual elements]
"""
            generation_prompt = f"""
Create a visual story titled "{concept}" with exactly {num_scenes} scenes.

Create exactly {num_scenes} scenes. Each scene should be a visual summary using {{character}} placeholder.

IMPORTANT RULES:
- Use {{character}} instead of any actual character name
- Focus on visual actions and emotions
- Describe what can be seen in each scene
- NO dialogue, just visual storytelling
- Each scene should be suitable for image/video generation
- Include only ONE character throughout the story

Format:
**Scene 1: "Title of Scene"**  
{{character}} [describe what the character is doing, feeling, or experiencing visually]. [Describe the environment, actions, and visual elements without using any actual character name].

**Scene 2: "Title of Scene"**  
{{character}} [continue the story visually]...

Continue for all {num_scenes} scenes, maintaining visual continuity and using {{character}} placeholder throughout.

The story should be {description} and focus on the concept: {concept}
"""
            if previous_context:
                generation_prompt = (
                    f"Previous context:\n{previous_context}\n\n"
                    "Continue the story with the next scene, ensuring it is consistent with the previous context.\n"
                    + generation_prompt
                )

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
            "temperature": temperature,
            "max_tokens": 4000
        }

        response = requests.post(
            f"{NEBIUS_API_BASE}/chat/completions",
            headers=headers,
            json=payload,
        )

        if response.status_code == 200:
            result = response.json()
            script_text = result["choices"][0]["message"]["content"]
            scene_details = extractScenes(script_text)
            return {
                "script": script_text, 
                "temperature": temperature,
                "scene_details": scene_details,
                "product_details": {},
                "project_type": project_type
            }
        else:
            return {
                "script": f"API Error {response.status_code}: {response.text}", 
                "character_details": {},
                "scene_details": [],
                "product_details": {},
                "temperature": temperature,
                "project_type": project_type
            }

    except Exception as e:
        return {
            "temperature": temperature,
            "script": f"Error generating script: {str(e)}", 
            "character_details": {},
            "scene_details": [],
            "product_details": {},
            "project_type": project_type
        }

def detect_project_type(concept: str) -> str:
    """
    Infer if the project is a 'story' or 'commercial' based on concept and script content.
    """
    concept_lower = concept.lower()

    commercial_keywords = ["advert", "advertisement", "commercial", "promo", "promotional", "product", "sale", "buy now", "features"]

    for keyword in commercial_keywords:
        if keyword in concept_lower:
            return "commercial"

    return "story"