from typing import Dict, Any, List, Optional
import os
import requests
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from .services.checkpoints import checkpointer
from .services.script_generation import generate_script
from .services.image_prompt_generation import ImagePromptGenerator
from .models import WorkflowCheckpoint

load_dotenv()

State = Dict[str, Any]

# ---------- Helpers ----------
def _read_multiline_input(prompt: str) -> str:
    """Read multi-line user input; stop on empty line."""
    print(prompt)
    print("(Finish with an empty line)")
    lines: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()

def _get_user_choice(options: List[str], prompt: str) -> str:
    """Get user choice from a list of options."""
    while True:
        print(f"\n{prompt}")
        for i, option in enumerate(options, 1):
            print(f"{i}. {option}")
        try:
            choice = input("Enter your choice (number): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            print("Invalid choice. Please try again.")
        except (ValueError, KeyboardInterrupt):
            print("Invalid input. Please try again.")
            
def cleanup_checkpoints(state):
        # Clean up checkpoints for this thread if possible
        thread_id = state.get("thread_id")
        if not thread_id and "user_id" in state and "project_id" in state:
            thread_id = f"user-{state['user_id']}-{state['project_id']}"
        if thread_id:
            WorkflowCheckpoint.objects.filter(thread_id=thread_id).delete()

# ---------- Nodes ----------
def node_generate_script(state: State) -> State:
    """Generate the initial script based on user concept."""
    concept = state["concept"]
    num_scenes = state["num_scenes"]
    creativity = state["creativity"]
    trigger_word = state.get("trigger_word")

    print("🎬 Generating script...")
    try:
        res = generate_script(concept, num_scenes, creativity, trigger_word=trigger_word)
        
        state["script"] = res["script"]
        state["scenes"] = res["scene_details"]
        state["product"] = res.get("product_details", {})
        state['temperature'] = res.get('temperature', 0.7)
        state["project_type"] = res.get("project_type")

        print("\n✅ Script generated successfully!")
        print(f"\n📋 Project Type: {state['project_type']}")
        
        print(f"\n🎭 Scenes ({len(state['scenes'])}):")
        for s in state["scenes"]:
            print(f"  • Scene {s['scene_number']}: {s['title']}")
            print(f"    Story: {s['story']}")
        
        if state["project_type"] == "commercial":
            print("\n🛍️ Product Details:")
            for k, v in state["product"].items():
                print(f"  • {k}: {v}")
                
    except Exception as e:
        print(f"❌ Error generating script: {e}")
        state["error"] = str(e)
        
    return state

def node_decide_rewrite(state: State) -> State:
    """Handle user decision on whether to rewrite any scenes."""
    scenes = state.get("scenes", [])
    if not scenes:
        state["scene_to_edit"] = None
        state["needs_rewrite"] = False
        return state

    # Check if this is a programmatic call with pre-set decisions
    if "rewrite_decision" in state:
        decision = state["rewrite_decision"]
        if decision == "accept":
            state["needs_rewrite"] = False
            state["scene_to_edit"] = None
        elif decision == "edit":
            state["needs_rewrite"] = True
            # scene_to_edit should already be set
        return state

    # Interactive mode - ask user
    print("\n" + "="*50)
    print("📝 SCRIPT REVIEW")
    print("="*50)
    
    # Show abbreviated script
    print("\n📖 Current Script Overview:")
    for i, scene in enumerate(scenes, 1):
        story = scene.get('story', '')
        print(f"Scene {i}: {scene.get('title', '')}")
        print(f"  {story}")
        print()
    
    # Get user choice
    choices = ["Continue with current script", "Edit a specific scene"]
    choice = _get_user_choice(choices, "\nWhat would you like to do?")
    
    if choice == "Continue with current script":
        state["needs_rewrite"] = False
        state["scene_to_edit"] = None
        print("✅ Proceeding with current script.")
    else:
        # Show scenes for selection
        scene_options = [f"Scene {s['scene_number']}: {s['title']}" for s in scenes]
        selected = _get_user_choice(scene_options, "\nWhich scene would you like to edit?")
        scene_num = int(selected.split(":")[0].split()[-1])
        state["needs_rewrite"] = True
        state["scene_to_edit"] = scene_num
        print(f"🎯 Selected Scene {scene_num} for editing.")
    
    return state

def node_rewrite_scene(state: "State") -> "State":
    """
    Rewrites the selected scene(s) and regenerates subsequent scenes.
    Can handle single scene edit or all scenes edit based on state flags.
    """
    import re
    import os
    import requests

    print("node_rewrite_scene called with state:", state)
    try:
        # Check if we're editing all scenes
        edit_all_scenes = state.get("edit_all_scenes", False)
        
        if edit_all_scenes:
            return rewrite_all_scenes(state)
        else:
            return rewrite_single_scene(state)
            
    except Exception as e:
        state["error"] = f"An unexpected error occurred during rewrite: {e}"
        state["scene_to_edit"] = None
        state["needs_rewrite"] = False
        cleanup_checkpoints(state)  
        return state

def rewrite_single_scene(state: "State") -> "State":
    """
    Rewrites the selected scene and regenerates subsequent scenes.
    """
    import re
    import os
    import requests

    print("node_rewrite_scene called with state:", state)
    try:
        try:
            target = state.get("scene_to_edit")
            if not target:
                # Reset rewrite flags if nothing to edit
                state["scene_to_edit"] = None
                state["needs_rewrite"] = False
                return state
    
            scenes = state.get("scenes", [])
            scene_map = {s["scene_number"]: s for s in scenes}
            current = scene_map.get(target)
            if not current:
                state["scene_to_edit"] = None
                state["needs_rewrite"] = False
                state["error"] = f"Scene {target} not found."
                return state
    
            user_notes = state.get("rewrite_instructions", "").strip()
            if not user_notes:
                state["scene_to_edit"] = None
                state["needs_rewrite"] = False
                state["error"] = "No rewrite instructions provided."
                return state
    
            api_key = os.getenv("NEBIUS_API_KEY")
            api_base = os.getenv("NEBIUS_API_BASE")
            if not api_key or not api_base:
                state["scene_to_edit"] = None
                state["needs_rewrite"] = False
                state["error"] = "Nebius API not configured."
                return state
            
            trigger_word = state.get("trigger_word", "")
            
            # 1. Rewrite the selected scene using LLM
            system_prompt = f"""
You are a master storyteller and expert scene editor. Your job is to make scene edits that OBEY the user's EDIT REQUEST, even if it means changing the setting, weather, or time of day. 
You MUST prioritize the EDIT REQUEST over the original context. If the edit request requires a new setting, change it completely.

IMPORTANT: Always use the character name "{trigger_word}" (exactly as written) in your scenes instead of placeholders.
"""
            context_scenes = []
            for scene in state.get("scenes", []):
                if scene["scene_number"] == target:
                    context_scenes.append(f"**Scene {scene['scene_number']}: \"{scene['title']}\"** [TO BE EDITED]\n{scene['story']}")
                else:
                    context_scenes.append(f"**Scene {scene['scene_number']}: \"{scene['title']}\"**\n{scene['story']}")
            context = "\n\n".join(context_scenes)
    
            user_prompt = f"""
🚨 WARNING: If you do not change the scene according to the EDIT REQUEST, your output will be rejected.

STORY CONCEPT: "{state['concept']}"
EDIT REQUEST: "{user_notes}"
CHARACTER NAME: "{trigger_word}" (use this exact name, not placeholders)

CURRENT STORY CONTEXT:
{context}

Your task:
- You MUST rewrite Scene {target} so that it reflects the edit request above.
- The new scene should CLEARLY show the changes described in the edit request, even if it means changing the setting, weather, or time of day.
- Do NOT simply repeat the previous scene. Make the changes OBVIOUS and VISIBLE.
- If the edit request says "character is walking on beautiful sunny morning day", the scene MUST be set in a sunny morning, NOT a forest or rainy setting, unless the user specifically requests a forest.

IMPORTANT REQUIREMENTS:
- Use the character name "{trigger_word}" (exactly as written) throughout the scene
- DO NOT use {{{{character}}}} or any placeholders - use the actual name "{trigger_word}"
- Ensure the edited scene flows naturally from the previous scene
- Make sure the edited scene sets up the next scene appropriately
- Maintain the visual storytelling approach

Return ONLY the edited scene in this exact format:
**Scene {target}: "Title"**
[Scene content using the character name "{trigger_word}"]
"""
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": state.get('temperature', 1),
                "max_tokens": 1000,
            }
    
            resp = requests.post(f"{api_base}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
    
            scene_pattern = r'\*\*Scene\s+(\d+):\s*"?([^"\n]+?)"?\*\*\s*(.*?)(?=\*\*Scene|\Z)'
            match = re.search(scene_pattern, content, re.DOTALL | re.IGNORECASE)
    
            if not match:
                state["error"] = "Could not parse rewritten scene. Content was: " + content
                state["scene_to_edit"] = None
                state["needs_rewrite"] = False
                return state
    
            new_title = match[2].strip()
            new_content = match[3].strip()
    
            scene_map[target]["title"] = new_title
            scene_map[target]["story"] = new_content
            scene_map[target]["script"] = new_content
            scene_map[target]["story_context"] = new_content
    
            # 2. Regenerate all subsequent scenes for cohesion, using accumulated context
            accumulated_context = []
            last_context = ""
            for sn in sorted(scene_map.keys()):
                current_scene = scene_map[sn]
                context_line = f"**Scene {current_scene['scene_number']}: \"{current_scene['title']}\"**\n{current_scene['story']}"
    
                if sn == target:
                    accumulated_context.append(context_line)
                elif sn > target:
                    # Only regenerate scenes after the edited one
                    regen_result = generate_script(
                        state["concept"],
                        1,
                        state.get("creativity", "balanced"),
                        previous_context=last_context,
                        trigger_word=trigger_word
                    )
                    if not regen_result or not regen_result.get("scene_details"):
                        state["error"] = "Scene regeneration failed."
                        state["scene_to_edit"] = None
                        state["needs_rewrite"] = False
                        return state
    
                    regen_scene = regen_result["scene_details"][0]
    
                    current_scene["title"] = regen_scene["title"]
                    current_scene["story"] = regen_scene["story"]
                    current_scene["script"] = regen_scene["script"]
                    current_scene["story_context"] = regen_scene["story"]
    
                    context_line = f"**Scene {current_scene['scene_number']}: \"{current_scene['title']}\"**\n{current_scene['story']}"
                    accumulated_context.append(context_line)
                else:
                    # For scenes before the edited one, just add their context
                    accumulated_context.append(context_line)
    
                last_context = "\n\n".join(accumulated_context)
    
            # 3. Update state
            state["scenes"] = [scene_map[sn] for sn in sorted(scene_map.keys())]
            state["script"] = "\n\n".join(
                f"**Scene {scene['scene_number']}: \"{scene['title']}\"**\n{scene['story']}"
                for scene in state["scenes"]
            )
            state["scene_to_edit"] = None
            state["needs_rewrite"] = False
            print("node_rewrite_scene returning state:", state)
            return state
    
        except Exception as e:
            state["error"] = f"An unexpected error occurred during rewrite: {e}"
            state["scene_to_edit"] = None
            state["needs_rewrite"] = False
            print("node_rewrite_scene returning error state:", state)
            return state
    except Exception as e:
        state["error"] = f"An unexpected error occurred during rewrite: {e}"
        state["scene_to_edit"] = None
        state["needs_rewrite"] = False
        cleanup_checkpoints(state)  
        print("node_rewrite_scene returning error state:", state)
        return state

def rewrite_all_scenes(state: "State") -> "State":
    """New function to rewrite all scenes coherently"""
    import re
    import os
    import requests
    
    try:
        scenes = state.get("scenes", [])
        if not scenes:
            state["error"] = "No scenes found to edit."
            state["needs_rewrite"] = False
            return state

        user_notes = state.get("rewrite_instructions", "").strip()
        if not user_notes:
            state["error"] = "No rewrite instructions provided."
            state["needs_rewrite"] = False
            return state

        api_key = os.getenv("NEBIUS_API_KEY")
        api_base = os.getenv("NEBIUS_API_BASE")
        if not api_key or not api_base:
            state["error"] = "Nebius API not configured."
            state["needs_rewrite"] = False
            return state
        
        trigger_word = state.get("trigger_word", "")
        
        print(f"Rewriting all {len(scenes)} scenes with instructions: {user_notes}")
        
        # Prepare context of all current scenes
        current_story_context = []
        for scene in scenes:
            current_story_context.append(
                f"**Scene {scene['scene_number']}: \"{scene['title']}\"**\n{scene['story']}"
            )
        context = "\n\n".join(current_story_context)

        # System prompt for rewriting all scenes
        system_prompt = f"""
You are a master storyteller and expert script editor. Your job is to rewrite ALL scenes in the story to incorporate the user's edit request while maintaining narrative coherence.

IMPORTANT RULES:
1. You MUST apply the edit request to ALL scenes, not just one
2. Always use the character name "{trigger_word}" (exactly as written) instead of placeholders
3. Maintain story flow and coherence between scenes
4. Make the changes OBVIOUS and VISIBLE in all scenes
5. Each scene should clearly reflect the edit request while building upon the previous scene

The edit request should transform the ENTIRE story, not just individual scenes.
"""

        # User prompt for all scenes rewrite
        user_prompt = f"""
🚨 CRITICAL: Rewrite ALL scenes to incorporate the edit request. Do not leave any scene unchanged.

STORY CONCEPT: "{state['concept']}"
EDIT REQUEST FOR ALL SCENES: "{user_notes}"
CHARACTER NAME: "{trigger_word}" (use this exact name, not placeholders)

CURRENT STORY (ALL SCENES):
{context}

Your task:
- Rewrite ALL {len(scenes)} scenes to incorporate the edit request
- Each scene must clearly show the changes described in the edit request
- Maintain narrative flow between scenes
- If edit request changes setting/weather/time, apply it to ALL scenes consistently
- Make sure each scene builds upon the previous one naturally

IMPORTANT REQUIREMENTS:
- Use the character name "{trigger_word}" throughout all scenes
- DO NOT use {{{{character}}}} or any placeholders
- Return ALL scenes, even if slightly modified
- Ensure visual storytelling approach for each scene

Return all scenes in this exact format:
**Scene 1: "Title"**
[Scene 1 content using character name "{trigger_word}"]

**Scene 2: "Title"**  
[Scene 2 content using character name "{trigger_word}"]

[Continue for all scenes...]
"""

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": state.get('temperature', 1),
            "max_tokens": 3000,  # Increased for multiple scenes
        }

        resp = requests.post(f"{api_base}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        print("LLM Response for all scenes rewrite:")
        print(content[:500] + "..." if len(content) > 500 else content)

        # Parse all scenes from the response
        scene_pattern = r'\*\*Scene\s+(\d+):\s*"?([^"\n]+?)"?\*\*\s*(.*?)(?=\*\*Scene|\Z)'
        matches = re.findall(scene_pattern, content, re.DOTALL | re.IGNORECASE)

        if not matches:
            state["error"] = "Could not parse any rewritten scenes from LLM response."
            state["needs_rewrite"] = False
            return state

        print(f"Found {len(matches)} scenes in LLM response")

        # Update all scenes with new content
        scene_map = {s["scene_number"]: s for s in scenes}

        for match in matches:
            scene_num = int(match[0])
            new_title = match[1].strip()
            new_content = match[2].strip()

            if scene_num in scene_map:
                # Update existing scene
                scene_map[scene_num]["title"] = new_title
                scene_map[scene_num]["story"] = new_content
                scene_map[scene_num]["script"] = new_content
                scene_map[scene_num]["story_context"] = new_content
                print(f"Updated scene {scene_num}: {new_title}")
            else:
                print(f"Warning: Scene {scene_num} not found in original scenes")

        # Rebuild scenes list in order
        state["scenes"] = [scene_map[sn] for sn in sorted(scene_map.keys())]
        
        # Update script
        state["script"] = "\n\n".join(
            f"**Scene {scene['scene_number']}: \"{scene['title']}\"**\n{scene['story']}"
            for scene in state["scenes"]
        )

        # Reset rewrite flags
        state["edit_all_scenes"] = False
        state["needs_rewrite"] = False
        state.pop("scene_to_edit", None)
        
        print(f"Successfully rewrote all {len(state['scenes'])} scenes")
        return state

    except Exception as e:
        state["error"] = f"Error in all scenes rewrite: {e}"
        state["needs_rewrite"] = False
        state["edit_all_scenes"] = False
        return state
    
def node_generate_image_prompts(state: State) -> State:
    """Generate image prompts from the finalized scenes."""
    try:
        scenes = state.get("scenes", [])
        if not scenes:
            state["error"] = "No scenes available for image prompt generation"
            return state
        
        print("🎨 Generating image prompts...")
        
        # Prepare scenes data in the format expected by ImagePromptGenerator
        scenes_data = []
        for scene in scenes:
            scene_prompt = scene.get("story_context") or scene.get("story") or scene.get("script", "")
            scenes_data.append({
                "scene_number": scene.get("scene_number", 1),
                "scene_title": scene.get("title", f"Scene {scene.get('scene_number', 1)}"),
                "final_prompt": scene_prompt,
                "trigger_word": state.get("trigger_word", "")
            })
        
        # Format data as expected by ImagePromptGenerator
        formatted_data = {
            "status": "success",
            "data": {
                "project_id": state.get("project_id", "workflow_generated"),
                "project_title": state.get("project_title", "Generated Project"),
                "original_prompt": state.get("concept", ""),
                "trigger_word": state.get("trigger_word", ""),
                "total_scenes": len(scenes_data),
                "scenes": scenes_data
            }
        }
        
        # Generate image prompts
        generator = ImagePromptGenerator()
        result = generator.generate_image_prompt(formatted_data)
        
        if result.get("success", False):
            state["image_prompts"] = result.get("data", {})
            print("✅ Image prompts generated successfully!")
            
            # Display generated prompts
            image_prompts_data = result.get("data", {})
            scenes_with_prompts = image_prompts_data.get("scenes", [])
            
            print(f"\n🖼️ Generated Image Prompts ({len(scenes_with_prompts)} scenes):")
            for scene_prompt in scenes_with_prompts:
                scene_num = scene_prompt.get("scene_number", "Unknown")
                scene_title = scene_prompt.get("scene_title", "Untitled")
                image_prompt = scene_prompt.get("image_prompt", "No prompt generated")
                
                print(f"  • Scene {scene_num}: {scene_title}")
                print(f"    Image Prompt: {image_prompt[:100]}{'...' if len(image_prompt) > 100 else ''}")
                
        else:
            error_msg = result.get("error", "Unknown error in image prompt generation")
            state["error"] = f"Image prompt generation failed: {error_msg}"
            print(f"❌ Image prompt generation failed: {error_msg}")
            
    except Exception as e:
        error_msg = f"Error in image prompt generation: {str(e)}"
        state["error"] = error_msg
        print(f"❌ {error_msg}")
        
    return state

def node_finalize_output(state: State) -> State:
    """Finalize and present the complete output."""
    print("\n" + "="*60)
    print("🎉 PROJECT COMPLETED!")
    print("="*60)
    
    print(f"\n📊 PROJECT SUMMARY:")
    print(f"  • Concept: {state['concept']}")
    print(f"  • Project Type: {state.get('project_type', 'Unknown')}")
    print(f"  • Total Scenes: {len(state.get('scenes', []))}")
    
    if state.get("error"):
        print(f"\n⚠️ Errors encountered: {state['error']}")
    else:
        print("🎉 Script generation completed successfully!")
    
    return state

# ---------- Routing Functions ----------
def route_after_decide(state: dict) -> str:
    # Route to rewrite_scene if needs_rewrite is True, else generate image prompts
    if state.get("needs_rewrite", False):
        return "rewrite_scene"
    else:
        return "generate_image_prompts"

def route_after_rewrite(state: dict) -> str:
    if state.get("needs_rewrite", False):
        return "decide_rewrite"
    else:
        return "decide_rewrite"
# ---------- Workflow Builder ----------
def build_workflow(entry_point="generate_script"):
    g = StateGraph(State)
    if entry_point == "generate_script":
        g.add_node("generate_script", node_generate_script)
    g.add_node("decide_rewrite", node_decide_rewrite)
    g.add_node("rewrite_scene", node_rewrite_scene)
    g.add_node("generate_image_prompts", node_generate_image_prompts)
    g.add_node("finalize_output", node_finalize_output)

    g.set_entry_point(entry_point)
    if entry_point == "generate_script":
        g.add_edge("generate_script", "decide_rewrite")
    g.add_conditional_edges(
        "decide_rewrite",
        route_after_decide,
        {
            "rewrite_scene": "rewrite_scene",
            "generate_image_prompts": "generate_image_prompts"
        },
    )
    g.add_conditional_edges(
        "rewrite_scene",
        route_after_rewrite,
        {
            "decide_rewrite": "decide_rewrite",
            "generate_image_prompts": "generate_image_prompts"
        },
    )
    g.add_edge("generate_image_prompts", "finalize_output")
    g.add_edge("finalize_output", END)

    return g.compile(checkpointer=checkpointer)



def validate_inputs(concept: str, num_scenes: str, creativity: str) -> tuple[str, int, str]:
    """Validate and normalize user inputs."""
    # Validate concept
    if not concept.strip():
        raise ValueError("Concept cannot be empty")
    
    # Validate num_scenes
    try:
        num_scenes_int = int(num_scenes) if num_scenes.strip() else 5
        if num_scenes_int < 1 or num_scenes_int > 20:
            print("⚠️ Number of scenes should be between 1-20. Using default: 5")
            num_scenes_int = 5
    except ValueError:
        print("⚠️ Invalid number of scenes. Using default: 5")
        num_scenes_int = 5
    
    # Validate creativity
    valid_creativity = ["factual", "creative", "balanced"]
    creativity_clean = creativity.strip().lower() if creativity.strip() else "balanced"
    if creativity_clean not in valid_creativity:
        print(f"⚠️ Invalid creativity level. Using default: balanced")
        creativity_clean = "balanced"
    
    return concept.strip(), num_scenes_int, creativity_clean

def main():
    """Main execution function."""
    print("🎬 Welcome to the Script Generator!")
    print("=" * 50)
    
    try:
        # Get user inputs
        concept = input("💡 Please enter your story/commercial idea: ").strip()
        num_scenes_input = input("🎭 Number of scenes [default: 5]: ").strip()
        creativity_input = input("🎨 Creativity level (factual/creative/balanced) [default: balanced]: ").strip()
        
        # Validate inputs
        concept, num_scenes, creativity = validate_inputs(concept, num_scenes_input, creativity_input)
        
        print(f"\n🚀 Starting project with:")
        print(f"  • Concept: {concept}")
        print(f"  • Scenes: {num_scenes}")
        print(f"  • Creativity: {creativity}")
        
        # Initialize state
        init_state: State = {
            "concept": concept,
            "num_scenes": num_scenes,
            "creativity": creativity,
            "script": "",
            "scenes": [],
            "project_type": "story",
            "scene_to_edit": None,
            "needs_rewrite": False,
        }

        # Build and run workflow
        print("\n⚙️ Initializing workflow...")
        app = build_workflow()
        
        print("🔄 Running generation pipeline...\n")
        final_state = app.invoke(init_state)

        # Final summary
        print("\n" + "="*60)
        print("📋 FINAL SUMMARY")
        print("="*60)
        print(f"✅ Project: {final_state['concept']}")
        print(f"📁 Type: {final_state.get('project_type', 'Unknown')}")
        print(f"🎭 Scenes: {len(final_state.get('scenes', []))}")
        
        if final_state.get("error"):
            print(f"⚠️ Errors: {final_state['error']}")
        else:
            print("🎉 Script generation completed successfully!")
            
        return final_state
        
    except KeyboardInterrupt:
        print("\n\n⏹️ Process interrupted by user.")
        return None
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return None

if __name__ == "__main__":
    main()