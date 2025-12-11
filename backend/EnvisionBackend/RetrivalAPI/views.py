from django.shortcuts import render
from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework.response import Response
from typing import Dict, Any, List, Optional
from rest_framework.decorators import api_view, permission_classes,authentication_classes
from rest_framework import status
import json
import re
from .services.checkpoints import checkpointer
from . import models, serializers
from .services.script_generation import detect_project_type
from .services.image_prompt_generation import ImagePromptGenerator,CreateVideoPrompt
from .services.comfyUIservices import fetch_image_from_comfy
from .main import build_workflow
from dotenv import load_dotenv
from .models import WorkflowCheckpoint
import base64
import requests
import time
from moviepy import VideoFileClip
from moviepy import concatenate_videoclips
import io
import os
import tempfile
load_dotenv()

RUNPOD_API_KEY = os.getenv("RunPod_API_KEY")

################# Helper Functions #################
def is_base64(data):
    try:
        # Check if the string can be decoded
        base64.b64decode(data, validate=True)
        return True
    except Exception:
        return False
def normalize_base64(data_url):
    # 1) Remove header (data:image/png;base64,)
    clean = re.sub(r'^data:.*;base64,', '', data_url)

    # 2) Remove whitespace
    clean = clean.strip().replace('\n', '').replace(' ', '')

    # 3) Fix padding
    missing = len(clean) % 4
    if missing:
        clean += "=" * (4 - missing)

    return clean

def enforce_character_placeholder(text):
    # Replace "the character's" or "character’s" with "{character}'s"
    text = re.sub(r"\b(the )?character[’']s\b", r"{character}'s", text, flags=re.IGNORECASE)
    # Replace "the character" or "character" with "{character}"
    text = re.sub(r"\b(the )?character\b", r"{character}", text, flags=re.IGNORECASE)
    return text

def poll_status_and_hit_api(response_id, max_retries=200, delay=5):
    """
    Poll the status of the API until it is 'COMPLETED' or a maximum number of retries is reached.
    """
    status_url = f"https://api.runpod.ai/v2/ztc333122svqcf/status/{response_id}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RUNPOD_API_KEY}"
    }

    retries = 0

    while retries < max_retries:
        try:
            # Check the status
            status_response = requests.get(status_url, headers=headers)
            print("DEBUG: API response content:", status_response.text)
            print("DEBUG: API response status code:", status_response.status_code)

            if status_response.status_code == 200:
                try:
                    status_data = status_response.json()
                except ValueError:
                    raise Exception("Failed to parse API response as JSON.")

                status = status_data.get("status")
                print(f"Current status: {status}")

                if status == "COMPLETED":
                    print("Status is COMPLETED. Returning the final result.")
                    return status_data  # Return the final result directly
                elif status in ["FAILED", "CANCELLED"]:
                    raise Exception(f"Process failed or cancelled with status: {status}")
                else:
                    print(f"Status is {status}. Retrying after {delay} seconds...")
            else:
                raise Exception(f"Failed to fetch status. HTTP Status Code: {status_response.status_code}")

        except Exception as e:
            print(f"Error while polling status: {str(e)}")
            raise

        # Wait before polling again
        retries += 1
        time.sleep(delay)

    # If the maximum number of retries is reached
    raise TimeoutError("Polling timed out before the status became COMPLETED.")

def get_user_selected_character(request):
    """Get the user's selected character trigger_word from session"""
    return request.session.get('selected_character', '')

def validate_character_selection(request):
    """Validate that user has selected a character"""
    selected_character = get_user_selected_character(request)
    if not selected_character:
        return None, Response({
            "status": "error",
            "message": "No character selected. Please call setCharacter first.",
            "error_code": "no_character_selected"
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        character = models.Character.objects.get(trigger_word=selected_character)
        return character, None
    except models.Character.DoesNotExist:
        # Clear invalid selection
        request.session.pop('selected_character', None)
        return None, Response({
            "status": "error",
            "message": "Previously selected character no longer exists. Please select a character again.",
            "error_code": "invalid_character_selection"
        }, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def getCharacters(request):
    characters = models.Character.objects.all()
    serializer = serializers.CharacterSerializer(characters, many=True)
    return Response(serializer.data)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def setCharacter(request):
    """
    Set or update character for the user
    Expects: { "trigger_word": "merida"}
    """
    try:
        data = json.loads(request.body)
        trigger_word = data.get('trigger_word', '').strip()

        if not trigger_word:
            return Response({
                "status": "error",
                "message": "trigger_word is required.",
                "error_code": "trigger_word_required"
            }, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            character = models.Character.objects.get(trigger_word=trigger_word)
            
            # Store the selected character in session for persistence
            request.session['selected_character'] = trigger_word
            request.session.save()
            
            return Response({
                "status": "success",
                "message": f"Character '{character.name}' with trigger_word '{trigger_word}' selected successfully.",
                "data": {
                    "character": serializers.CharacterSerializer(character).data
                }
            }, status=status.HTTP_200_OK)
            
        except models.Character.DoesNotExist:
            return Response({
                "status": "error",
                "message": f"Character with trigger_word '{trigger_word}' does not exist.",
                "error_code": "character_not_found"
            }, status=status.HTTP_404_NOT_FOUND)
            
    except json.JSONDecodeError:
        return Response({
            "status": "error",
            "message": "Invalid JSON format in request body.",
            "error_code": "invalid_json"
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        import traceback
        print("Exception in setCharacter:", traceback.format_exc())
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "error_code": "internal_error"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def generateScenes(request):
    """
    Generate scenes using existing script generation workflow
    Expects: { "trigger_word": "merida", "num_scenes": 3, "prompt": "a man walking in a deep dark jungle" }
    """
    try:
        data = json.loads(request.body)
        num_scenes = data.get('num_scenes', 3)
        prompt = data.get('prompt', '').strip()
        trigger_word = data.get('trigger_word').strip()


        try:
            num_scenes = int(num_scenes)
            if num_scenes < 1 or num_scenes > 7:
                num_scenes = 3
        except (ValueError, TypeError):
            num_scenes = 3

        # Use the prompt as concept for script generation
        concept = prompt
        project_title = f"Generated from: {prompt}"
        
        # Create project using existing logic
        project, created = models.Project.objects.get_or_create(
            user=request.user,
            concept=concept,
            defaults={
                "num_scenes": num_scenes,
                "creativity_level": "balanced",
                "title": project_title,
                "project_type": detect_project_type(concept),
                "trigger_word": trigger_word
            }
        )
        
        if not created:
            project.num_scenes = num_scenes
            project.title = project_title
            project.trigger_word = trigger_word
            project.save()
            project.scenes.all().delete()

        # Initialize state for workflow with trigger_word
        init_state = {
            "concept": concept,
            "num_scenes": num_scenes,
            "creativity": "balanced",
            "script": "",
            "scenes": [],
            "project_title": project_title,
            "project_type": detect_project_type(concept),
            "trigger_word": trigger_word  # Add trigger_word to init_state
        }

        # Run existing script generation workflow
        app = build_workflow()
        thread_id = f"user-{request.user.id}-{project.id}"  
        config = {"configurable": {"thread_id": thread_id}} 
        state_after_script = app.invoke(init_state, config=config, interrupt_before="decide_rewrite")
        
        if state_after_script is None:
            return Response({
                "status": "error",
                "message": "Script generation failed. Please try again.",
                "error_code": "script_generation_failed"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        # Create scenes in database
        created_scenes = []
        for scene_data in state_after_script.get("scenes", []):
            scene_title = scene_data.get("title", f"Scene {scene_data.get('scene_number', 1)}")
            scene = models.Scene.objects.create(
                project=project,
                scene_number=scene_data.get("scene_number", 1),
                script=scene_data.get("script", ""),
                story_context=scene_data.get("story", ""),
                title=scene_title
            )
            
            # Prepare scene data - the script should already contain the trigger_word

            created_scenes.append({
                "id": str(scene.id),
                "scene_number": scene.scene_number,
                "scene_title": scene.title,
                "script": scene.script,
                "story_context": scene.story_context,
                "trigger_word": trigger_word,
                
            })

        # Check if character exists
        character = None
        try:
            character = models.Character.objects.get(trigger_word=trigger_word)
        except models.Character.DoesNotExist:
            pass
        
        WorkflowCheckpoint.objects.filter(thread_id=thread_id).delete()
        return Response({
            "status": "success",
            "message": f"Generated {len(created_scenes)} scenes from script generation workflow.",
            "data": {
                "project_id": str(project.id),
                "project_title": project.title,
                "original_prompt": prompt,
                "trigger_word": trigger_word,
                "character_exists": character is not None,
                "character_name": character.name if character else None,
                "total_scenes": len(created_scenes),
                "scenes": created_scenes
            }
        }, status=status.HTTP_200_OK)

    except json.JSONDecodeError:
        return Response({
            "status": "error",
            "message": "Invalid JSON format in request body.",
            "error_code": "invalid_json"
        }, status=status.HTTP_400_BAD_REQUEST)
    
    except Exception as e:
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "error_code": "internal_error"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
def generate_image_prompts(p_id, u_id):
    """
    API endpoint to generate image prompts from scenes data
    
    Expects: { "project_id": "..." }
    Retrieves scenes from database and generates image prompts
    """
    try:
        project_id = p_id
        
        if not project_id:
            return Response({
                "error": "project_id is required",
                "success": False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get project and verify ownership
        try:
            project = models.Project.objects.get(id=project_id, user=u_id)
        except models.Project.DoesNotExist:
            return Response({
                "error": "Project not found or access denied",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Get all scenes for this project
        scenes = models.Scene.objects.filter(project=project).order_by('scene_number')
        
        if not scenes.exists():
            return Response({
                "error": "No scenes found for this project",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)
        
        trigger_word = project.trigger_word 
        # Convert scenes queryset to a list of dictionaries
        scenes_data = []
        for scene in scenes:
            scenes_data.append({
                "scene_number": scene.scene_number,
                "scene_title": scene.title,
                "story_context": scene.story_context,
                "script": scene.script,
            })
        
        thread_id = f"user-{u_id.id}-{project.id}"
        config = {"configurable": {"thread_id": thread_id}}
        app = build_workflow(entry_point="generate_image_prompts")
        init_state = {
            "project_id": str(project.id),
            "project_title": project.title,
            "concept": project.concept,
            "trigger_word": trigger_word,
            "scenes": scenes_data
        }
        
        print("DEBUG: Invoking workflow with state:", init_state)
        state_after_prompt_gen = app.invoke(init_state, config=config)
        print("DEBUG: State after prompt generation:", state_after_prompt_gen)
        
        # Get the image_prompts data from state
        image_prompts_data = state_after_prompt_gen.get("image_prompts", {})
        updated_scenes = image_prompts_data.get("scenes", [])
        
        print(f"DEBUG: Found {len(updated_scenes)} scenes with image prompts")
        
        if not updated_scenes:
            # Fallback: try getting scenes directly from state
            updated_scenes = state_after_prompt_gen.get("scenes", [])
            print(f"DEBUG: Fallback - Found {len(updated_scenes)} scenes in state")
        
        # Update database with generated prompts
        response_scenes_data = []
        for scene_dict in updated_scenes:
            scene_number = scene_dict.get("scene_number")
            final_prompt = scene_dict.get("image_prompt")
            
            if not scene_number:
                print(f"DEBUG: Skipping scene without scene_number: {scene_dict}")
                continue
                
            if not final_prompt:
                print(f"DEBUG: No image_prompt found for scene {scene_number}")
                continue
            
            try:
                # Get the database object
                scene_obj = models.Scene.objects.get(project=project, scene_number=scene_number)
                
                # Update the image prompt
                scene_obj.image_prompt = final_prompt
                scene_obj.save()
                
                print(f"DEBUG: Saved image prompt for scene {scene_number}: {final_prompt[:100]}...")
                
                # Add to response using dictionary values
                response_scenes_data.append({
                    "scene_number": scene_dict.get("scene_number"),
                    "scene_title": scene_dict.get("scene_title", scene_obj.title),
                    "image_prompt": final_prompt
                })
            except models.Scene.DoesNotExist:
                print(f"DEBUG: Scene {scene_number} not found in database")
                continue
        
        # Clean up checkpoints
        WorkflowCheckpoint.objects.filter(thread_id=thread_id).delete()
        
        # Format data as complete API response structure
        formatted_data = {
            "status": "success",
            "data": {
                "project_id": str(project.id),
                "project_title": project.title,
                "original_prompt": project.concept,
                "trigger_word": trigger_word,
                "total_scenes": len(response_scenes_data),
                "scenes": response_scenes_data
            }
        }
        
        print(f"DEBUG: Returning response with {len(response_scenes_data)} scenes")
        return Response(formatted_data, status=status.HTTP_200_OK)
            
    except json.JSONDecodeError:
        return Response({
            "error": "Invalid JSON format in request body",
            "success": False
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        import traceback
        print("ERROR in generate_image_prompts:", traceback.format_exc())
        return Response({
            "error": f"Internal server error: {str(e)}",
            "success": False
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def generate_images(request):
    """
    API endpoint to generate images from existing prompts
    
    Expects: { "project_id": "..." }
    Uses prompts from the database to generate images
    """
    try:
        data = json.loads(request.body)
        
        if not data:
            return Response({
                "error": "Request body is required",
                "success": False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        project_id = data.get('project_id')
        
        if not project_id:
            return Response({
                "error": "project_id is required",
                "success": False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get project and verify ownership
        try:
            scenes_prompt_data = generate_image_prompts(project_id,request.user)
        except Exception as e:
            return Response({
                "error": f"Failed to generate image prompts: {str(e)}",
                "success": False
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        try:
            project = models.Project.objects.get(id=project_id, user=request.user)
        except models.Project.DoesNotExist:
            return Response({
                "error": "Project not found or access denied",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Get all scenes for this project
        scenes = models.Scene.objects.filter(project=project).order_by('scene_number')
        
        if not scenes.exists():
            return Response({
                "error": "No scenes found for this project",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)
        
        scenes_data = []
        for scene in scenes:
            if not scene.image_prompt:
                return Response({
                    "error": f"Image prompt not found for scene {scene.scene_number}",
                    "success": False
                }, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                # Use the image prompt to generate the image
                image_data = fetch_image_from_comfy(scene.image_prompt)
                
                # Save the generated image as base64 in the database
                scene.image = f"data:image/png;base64,{base64.b64encode(image_data).decode('utf-8')}"
                
                # sec_image_prompt = f"{scene.image_prompt} make an image prompt of the closing scene using this image prompt"
                # sec_image_data = fetch_image_from_comfy(sec_image_prompt)
                
                # scene.sec_image = f"data:image/png;base64,{base64.b64encode(sec_image_data).decode('utf-8')}"
                
                scene.save()
                
                # Add scene data to the response
                scenes_data.append({
                    "scene_number": scene.scene_number,
                    "scene_title": scene.title,
                    "image": scene.image  # Base64-encoded image 
                })
            except Exception as e:
                return Response({
                    "error": f"Failed to generate image for scene {scene.scene_number}: {str(e)}",
                    "success": False
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Format data as complete API response structure
        formatted_data = {
            "status": "success",
            "data": {
                "project_id": str(project.id),
                "project_title": project.title,
                "total_scenes": len(scenes_data),
                "scenes": scenes_data
            }
        }
        
        return Response(formatted_data, status=status.HTTP_200_OK)
            
    except json.JSONDecodeError:
        return Response({
            "error": "Invalid JSON format in request body",
            "success": False
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            "error": f"Internal server error: {str(e)}",
            "success": False
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def edit_image(request):
    """
    API endpoint to edit images based on user instructions
    
    Expects: { "project_id": "...", "edit_instructions": "..." }
    Uses existing images and edit instructions to generate edited images
    """
    try:
        data = json.loads(request.body)
        project_id = data.get('project_id')
        edit_instructions = data.get('edit_instructions', '').strip()
        scene_number = data.get('scene_number')
        style = data.get('style', 'realistic').strip()
        if not project_id or not edit_instructions:
            return Response({
                "status": "error",
                "message": "project_id and edit_instructions are required.",
                "success": False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        project = models.Project.objects.get(id=project_id, user=request.user)
        scene = models.Scene.objects.get(project=project, scene_number=scene_number)
        if not project:
            return Response({
                "status": "error",
                "message": "Project not found.",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)
        if not scene:
            return Response({
                "status": "error",
                "message": "Scene not found.",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)
            
        # Use ComfyUI service to edit the image based on instructions
        # image_prompt =f"Following the story context: {scene.story_context}. Edit the image to reflect: {edit_instructions} in a {style} style."
        # image_prompt = (
        #     f"Following the story context: {scene.story_context}. "
        #     f"Craft the image to reflect: {edit_instructions} and apply the following style: {style}."
        #     f"Ensure there is no repetition of characters in the frame and there are no hallucinations."
        # )

        image_prompt = (
            f"Following the story context: {scene.story_context}. "
            f"Edit the existing scene to: {edit_instructions}. "
            f"Render the final image in a {style} visual style. "
            f"Ensure consistency with previous scene elements, "
            f"avoid duplicated characters or hallucinations."
        )
        # sec_image_prompt = (
        #     f"Following the story context: {scene.story_context}. "
        #     f"Edit the existing scene to: {edit_instructions}. "
        #     f"Create a closing scene image in a {style} visual style. "
        #     f"Ensure consistency with previous scene elements, "
        #     f"avoid duplicated characters or hallucinations."
        # )
        print(image_prompt)
        image = fetch_image_from_comfy(image_prompt)
        scene.image = f"data:image/png;base64,{base64.b64encode(image).decode('utf-8')}"
        # sec_image = fetch_image_from_comfy(sec_image_prompt)
        # scene.sec_image = f"data:image/png;base64,{base64.b64encode(sec_image).decode('utf-8')}"
        scene.save()
        return Response({
            "status": "success",
            "message": f"Image for scene {scene.scene_number}/{scene.title} edited successfully.",
            "data": {
                "scene_number": scene.scene_number,
                "scene_title": scene.title,
                "edited_image": scene.image
            }
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "success": False
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def edit_all_images(request):
    """
    API endpoint to edit all images in a project based on user instructions
    
    Expects: { "project_id": "...", "edit_instructions": "..." }
    Uses existing images and edit instructions to generate edited images
    """
    try:
        data = json.loads(request.body)
        project_id = data.get('project_id')
        edit_instructions = data.get('edit_instructions', '').strip()
        style = data.get('style', 'realistic').strip()
        if not project_id or not edit_instructions:
            return Response({
                "status": "error",
                "message": "project_id and edit_instructions are required.",
                "success": False
            }, status=status.HTTP_400_BAD_REQUEST)

        project = models.Project.objects.get(id=project_id, user=request.user)
        if not project:
            return Response({
                "status": "error",
                "message": "Project not found.",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)

        scenes = models.Scene.objects.filter(project=project)
        edited_scenes = []
        aggregated_context = " ".join([scene.story_context for scene in scenes])
        # aggregated_instructions = (
        #     f"Generate visuals in a {style} style. "
        #     f"Incorporate the following edits: {edit_instructions}. "
        #     f"Base the visuals on the overall story context: {aggregated_context}."
        #     f"Ensure there is no repetition of characters in the frame and there are no hallucinations."
        # )
        for scene in scenes:
            # image_prompt = (
            #     f"Scene {scene.scene_number}: {scene.title}. "
            #     f"Story context: {scene.story_context}. "
            #     f"Edit instructions: {aggregated_instructions}"
            # )
            image_prompt = (
                f"Scene {scene.scene_number}: {scene.title}. "
                f"Story context: {scene.story_context}. "
                f"Modify the image to reflect the following edit instructions: {edit_instructions}. "
                f"Render this scene in a {style} style. "
                f"Ensure consistency with the story context: {aggregated_context}. "
                f"Do not repeat characters or create visual artifacts."
            )
            
            # sec_image_prompt = (
            #     f"Scene {scene.scene_number}: {scene.title}. "
            #     f"Story context: {scene.story_context}. "
            #     f"Create a closing scene image reflecting the following edit instructions: {edit_instructions}."
            #     f"Render this scene in a {style} style. "
            #     f"Ensure consistency with the story context: {aggregated_context}. "
            #     f"Do not repeat characters or create visual artifacts."
            # )
    
            image = fetch_image_from_comfy(image_prompt)
            scene.image = f"data:image/png;base64,{base64.b64encode(image).decode('utf-8')}"
            # sec_image = fetch_image_from_comfy(sec_image_prompt)
            # scene.sec_image = f"data:image/png;base64,{base64.b64encode(sec_image).decode('utf-8')}"
            scene.save()
            edited_scenes.append({
                "scene_number": scene.scene_number,
                "scene_title": scene.title,
                "edited_image": scene.image
            })
        
        return Response({
            "status": "success",
            "message": f"All images for project '{project.title}' edited successfully.",
            "data": {
                "project_id": str(project.id),
                "project_title": project.title,
                "edited_scenes": edited_scenes
            }
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "success": False
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def CreateVideo(request):
    try:
        # Parse the request body
        data = json.loads(request.body)
        if not data:
            return Response({
                "error": "Request body is required",
                "success": False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate project_id
        project_id = data.get('project_id')
        if not project_id:
            return Response({
                "error": "project_id is required",
                "success": False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Fetch the project and its scenes
        try:
            project = models.Project.objects.get(id=project_id)
        except models.Project.DoesNotExist:
            return Response({
                "error": "Project not found",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)
        
        scenes = models.Scene.objects.filter(project=project)
        if not scenes.exists():
            return Response({
                "error": "No scenes found for the project",
                "success": False
            }, status=status.HTTP_404_NOT_FOUND)
        
        videos = []
        for scene in scenes:
            edit_instruction = scene.image_prompt
            modified_edit_instruction = CreateVideoPrompt(edit_instruction)
            scene_image = scene.image
            
            if is_base64(scene_image) is False:
                clean_scene_image = normalize_base64(scene_image)
            
            # # Validate and fix the Base64 image
            # if not scene_image.startswith("data:image"):
            #     return Response({
            #         "status": "error",
            #         "message": f"Scene {scene.scene_number} has an invalid Base64 image format."
            #     }, status=status.HTTP_400_BAD_REQUEST)
                
            
            # Prepare the request body for the API
            request_body = {
                "input": {
                    "generation_type": "textImage_to_video",
                    "model": "wan22",
                    "prompt": modified_edit_instruction,
                    "input_image": clean_scene_image
                }
            }
            post_api = "https://api.runpod.ai/v2/ztc333122svqcf/run"
            authorization = RUNPOD_API_KEY
            
            print(request_body)
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {authorization}"
            }
            
            # Send the POST request
            response = requests.post(post_api, data=json.dumps(request_body), headers=headers)
            
            # Validate the API response
            if response.status_code != 200:
                return Response({
                    "status": "error",
                    "message": f"API request failed with status code {response.status_code}: {response.text}"
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            try:
                response_data = response.json()
            except ValueError:
                return Response({
                    "status": "error",
                    "message": "Failed to parse API response as JSON."
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # Extract the response ID
            response_id = response_data.get("id")
            if not response_id:
                return Response({
                    "status": "error",
                    "message": "API response does not contain 'id'."
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            
            # Poll the status and retrieve the final result
            final_result = poll_status_and_hit_api(response_id)
            print("DEBUG: Final result from poll_status_and_hit_api:", final_result)

            # Extract the video URL from the output list
            output_list = final_result.get("output", [])
            video_url = output_list[0] if len(output_list) > 0 else None
            if not video_url:
                return Response({
                    "status": "error",
                    "message": "Video URL is missing in the API response."
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Download the video from the URL
            video_content = requests.get(video_url).content
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video_file:
                temp_video_file.write(video_content)
                temp_video_file.flush()
                videos.append(temp_video_file.name)
            
        # Stitch videos together using moviepy
        video_clips = []
        for video_path in videos:
            video_clip = VideoFileClip(video_path)
            video_clips.append(video_clip)
        
        final_video = concatenate_videoclips(video_clips, method="compose")
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video_file:
            final_video.write_videofile(temp_video_file.name, codec="libx264", audio_codec="aac")
            temp_video_file.seek(0)
            final_video_data = temp_video_file.read()
        
        final_video_base64 = base64.b64encode(final_video_data).decode('utf-8')
        project.video = f"data:video/mp4;base64,{final_video_base64}"
        project.save()
        
        # Clean up temporary video clips and files
        for clip in video_clips:
            clip.close()
        for video_path in videos:
            os.unlink(video_path)
        
        return Response({
            "status": "success",
            "message": "Videos stitched together successfully and saved to the project.",
            "project_id": str(project.id),
            "final_video_base64": project.video
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "success": False
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def get_project_and_scenes(request):
    """
    Get details of a particular project and its scenes for the authenticated user.
    Expects: { "project_id": ... }
    """
    try:
        data = json.loads(request.body)
        project_id = data.get('project_id')
        if not project_id:
            return Response({
                "status": "error",
                "message": "project_id is required."
            }, status=status.HTTP_400_BAD_REQUEST)

        project = models.Project.objects.get(id=project_id, user=request.user)
        project_serializer = serializers.ProjectSerializer(project)
        return Response({
            "status": "success",
            "project": project_serializer.data,
        })
    except models.Project.DoesNotExist:
        return Response({
            "status": "error",
            "message": "Project not found."
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def listProjects(request):
    projects = models.Project.objects.filter(user=request.user)
    data = [
        {
            "project_id":project.id,
            "project_name": project.title,
            "project_type": project.project_type
        }
        for project in projects
    ]
    return Response(data)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])  
@permission_classes([IsAuthenticated]) 
def CreateProject(request):
    """Initialize or continue project workflow"""
    try:
        data = json.loads(request.body)
        concept = data.get('concept', '').strip()
        num_of_scenes = data.get('num_scenes', 5)
        creativity = data.get('creativity', 'balanced').lower()
        project_title = data.get('concept', 'Generated Project').strip()

        if not concept:
            return Response({
                "status": "error",
                "message": "Concept is required.",
                "error_code": "concept_required"
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            num_of_scenes = int(num_of_scenes)
            if num_of_scenes < 1 or num_of_scenes > 20:
                num_of_scenes = 5
        except (ValueError, TypeError):
            num_of_scenes = 5

        valid_creativity = ["factual", "creative", "balanced"]
        if creativity not in valid_creativity:
            creativity = "balanced"

        # Try to get existing project for this user and concept/title
        project, created = models.Project.objects.get_or_create(
            user=request.user,
            concept=concept,
            defaults={
                "num_scenes": num_of_scenes,
                "creativity_level": creativity,
                "title": project_title,
                "project_type": detect_project_type(concept)
            }
        )
        if not created:
            # Overwrite fields if project already exists
            project.num_scenes = num_of_scenes
            project.creativity_level = creativity
            project.title = project_title
            project.save()
            # Delete old scenes
            project.scenes.all().delete()

        init_state = {
            "concept": concept,
            "num_scenes": num_of_scenes,
            "creativity": creativity,
            "script": "",
            "scenes": [],
            "project_title": project_title,
            "project_type": detect_project_type(concept)
        }

        app = build_workflow()
        thread_id = f"user-{request.user.id}-{project.id}"  
        config = {"configurable": {"thread_id": thread_id}} 
        state_after_script = app.invoke(init_state, config=config, interrupt_before="decide_rewrite")
        if state_after_script is None:
            return Response({
                "status": "error",
                "message": "Workflow did not return any state. Please check your workflow logic.",
                "error_code": "workflow_no_state"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        # Create scenes with custom titles if provided
        for scene_data in state_after_script.get("scenes", []):
            scene_title = scene_data.get("title", f"Scene {scene_data.get('scene_number', 1)}")
            models.Scene.objects.create(
                project=project,
                scene_number=scene_data.get("scene_number", 1),
                script=enforce_character_placeholder(scene_data.get("script", "")),
                story_context=enforce_character_placeholder(scene_data.get("story", "")),
                title=scene_title
            )

        serializer = serializers.ProjectSerializer(project)
        
        
        return Response({
            "status": "success",
            "message": "Script generated successfully.",
            "data": {
                "project": serializer.data,
            },
            "next_step": "review_script",
            "available_actions": ['accept_script', 'edit_scene', 'review_scene']
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "error_code": "internal_error"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])  
@permission_classes([IsAuthenticated])
def ReviewScript(request):
    """
    View details of a specific scene (read-only).
    Expects: { "project_id": ..., "scene_number": ... }
    """
    try:
        data = json.loads(request.body)
        project_id = data.get('project_id')
        scene_number = data.get('scene_number')

        if not project_id or not scene_number:
            return Response({
                "status": "error",
                "message": "Project ID and scene number are required.",
                "error_code": "missing_fields"
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            project = models.Project.objects.get(id=project_id, user=request.user)
        except models.Project.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Project not found.",
                "error_code": "project_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        try:
            scene = models.Scene.objects.get(project=project, scene_number=scene_number)
        except models.Scene.DoesNotExist:
            return Response({
                "status": "error",
                "message": f"Scene {scene_number} not found.",
                "error_code": "scene_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        scene_serializer = serializers.SceneSerializer(scene)
        return Response({
            "status": "success",
            "message": f"Scene {scene_number} ({scene.title}) details.",
            "data": {
                "scene": scene_serializer.data,
                "scene_title": scene.title
            },
            "next_step": "review_script",
            "available_actions": ["accept_script", "edit_scene"]
        })

    except Exception as e:
        return Response({
            "status": "error",
            "message": "Internal server error.",
            "error_code": "internal_error"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def EditScene(request):
    try:
        import pprint
        data = json.loads(request.body)
        project_id = data.get('project_id')
        scene_number = int(data.get('scene_number'))
        edit_instructions = data.get('edit_instructions', '').strip()

        if not project_id or not scene_number or not edit_instructions:
            return Response({
                "status": "error",
                "message": "project_id, scene_number, and edit_instructions are required.",
                "error_code": "missing_fields"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Get project and scene
        try:
            project = models.Project.objects.get(id=project_id, user=request.user)
        except models.Project.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Project not found.",
                "error_code": "project_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        try:
            scene_to_edit = models.Scene.objects.get(project=project, scene_number=scene_number)
        except models.Scene.DoesNotExist:
            return Response({
                "status": "error",
                "message": f"Scene {scene_number} not found.",
                "error_code": "scene_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        # Prepare state for scene rewriting
        thread_id = f"user-{request.user.id}-{project.id}"
        checkpoint_wrapper = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        if checkpoint_wrapper is None:
            # Fallback: reconstruct state from DB
            existing_scenes = []
            for scene in project.scenes.all():
                existing_scenes.append({
                    'id': str(scene.id),
                    'scene_number': scene.scene_number,
                    'script': scene.script,
                    'story_context': scene.story_context,
                    'story': scene.story_context or scene.script,
                    'title': scene.title
                })
            checkpoint_state = {
                "concept": project.concept,
                "num_scenes": project.num_scenes,
                "creativity": project.creativity_level,
                "scenes": existing_scenes,
                "project_title": project.title,
                "project_type": project.project_type,
                "trigger_word": getattr(project, 'trigger_word', '')
            }
        else:
            checkpoint_state = checkpoint_wrapper.checkpoint

        # --- Always overwrite these fields for edit ---
        checkpoint_state["scene_to_edit"] = int(scene_number)
        checkpoint_state["needs_rewrite"] = True
        checkpoint_state["rewrite_instructions"] = edit_instructions
        checkpoint_state["rewrite_decision"] = "edit"

        # --- Unwrap channel_values if present ---
        if "channel_values" in checkpoint_state and "__root__" in checkpoint_state["channel_values"]:
            checkpoint_state = checkpoint_state["channel_values"]["__root__"]

        print("checkpoint_state before workflow invoke:")
        pprint.pprint(checkpoint_state)

        # --- Resume graph ---
        app = build_workflow(entry_point="rewrite_scene")
        config = {"configurable": {"thread_id": thread_id}}
        updated_state = app.invoke(checkpoint_state, config=config)

        print("DEBUG: updated_state after workflow:", updated_state)

        if isinstance(updated_state, dict) and updated_state.get("error"):
            return Response({
                "status": "error",
                "message": f"Workflow error: {updated_state['error']}",
                "error_code": "workflow_node_error"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if updated_state is None:
            return Response({
                "status": "error",
                "message": "Workflow did not return any state. Please check your workflow logic.",
                "error_code": "workflow_no_state"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Defensive: ensure updated_state is a dict
        if not isinstance(updated_state, dict):
            return Response({
                "status": "error",
                "message": "Workflow returned invalid state type.",
                "error_code": "invalid_workflow_state"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        updated_scenes = updated_state.get('scenes', [])
        scene_updated = False
        scene_to_edit = None

        for updated_scene in updated_scenes:
            scene_number_db = updated_scene.get('scene_number')
            try:
                db_scene = models.Scene.objects.get(project=project, scene_number=scene_number_db)
                new_story = updated_scene.get('story')
                new_script = updated_scene.get('script') or new_story
                new_context = updated_scene.get('story_context') or new_story or new_script
                scene_title = updated_scene.get('title', db_scene.title)

                db_scene.script = new_script or db_scene.script
                db_scene.story_context = new_context or db_scene.story_context
                db_scene.title = scene_title or db_scene.title
                db_scene.save()

                if scene_number_db == scene_number:
                    scene_to_edit = db_scene
                    scene_updated = True
            except models.Scene.DoesNotExist:
                continue

        if not scene_updated or scene_to_edit is None:
            return Response({
                "status": "error",
                "message": "Failed to update scene - scene not found in response.",
                "error_code": "scene_update_failed"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        base_title = project.title.split(' - Scene')[0]
        project.title = f"{base_title} - Scene {scene_number} Updated"
        project.save()

        project.refresh_from_db()
        scene_serializer = serializers.SceneSerializer(scene_to_edit)
        
        WorkflowCheckpoint.objects.filter(thread_id=thread_id).delete()
        
        return Response({
            "status": "success",
            "message": f"Scene {scene_number} ({scene_to_edit.title}) updated successfully.",
            "data": {
                "updated_scene": scene_serializer.data,
                "edit_instructions_used": edit_instructions
            },
            "next_step": "review_script",
            "available_actions": ["accept_script", "edit_scene"]
        })

    except Exception as e:
        import traceback
        print("Exception in EditScene:", traceback.format_exc())
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "trace": traceback.format_exc(),
            "error_code": "internal_error"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def EditAllScenes(request):
    """
    Edit all scenes in a project coherently while maintaining context.
    Expects: { "project_id": "...", "edit_instructions": "..." }
    """
    try:
        import pprint
        data = json.loads(request.body)
        project_id = data.get('project_id')
        edit_instructions = data.get('edit_instructions', '').strip()  

        if not project_id or not edit_instructions:
            return Response({
                "status": "error",
                "message": "project_id and edit_instructions are required.",
                "error_code": "missing_fields"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Get project
        try:
            project = models.Project.objects.get(id=project_id, user=request.user)
        except models.Project.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Project not found.",
                "error_code": "project_not_found"
            }, status=status.HTTP_404_NOT_FOUND)

        # Check if project has scenes
        if not project.scenes.exists():
            return Response({
                "status": "error",
                "message": "No scenes found in this project.",
                "error_code": "no_scenes_found"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Prepare state for all scenes rewriting
        thread_id = f"user-{request.user.id}-{project.id}"
        checkpoint_wrapper = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        
        if checkpoint_wrapper is None:
            # Fallback: reconstruct state from DB
            existing_scenes = []
            for scene in project.scenes.all().order_by('scene_number'):
                existing_scenes.append({
                    'scene_number': scene.scene_number,
                    'script': scene.script,
                    'story_context': scene.story_context,
                    'story': scene.story_context or scene.script,
                    'title': scene.title
                })
            checkpoint_state = {
                "concept": project.concept,
                "num_scenes": project.num_scenes,
                "creativity": project.creativity_level,
                "scenes": existing_scenes,
                "project_title": project.title,
                "project_type": project.project_type,
                "trigger_word": getattr(project, 'trigger_word', '')
            }
        else:
            checkpoint_state = checkpoint_wrapper.checkpoint

        # --- Set up for editing all scenes ---
        checkpoint_state["needs_rewrite"] = True
        checkpoint_state["rewrite_instructions"] = edit_instructions
        checkpoint_state["rewrite_decision"] = "edit"
        checkpoint_state["edit_all_scenes"] = True  
        
        # Remove specific scene editing fields if they exist
        checkpoint_state.pop("scene_to_edit", None)

        # --- Unwrap channel_values if present ---
        if "channel_values" in checkpoint_state and "__root__" in checkpoint_state["channel_values"]:
            checkpoint_state = checkpoint_state["channel_values"]["__root__"]

        print("checkpoint_state before workflow invoke (edit all scenes):")
        pprint.pprint(checkpoint_state)

        # --- Resume graph for all scenes ---
        app = build_workflow(entry_point="rewrite_scene")
        config = {"configurable": {"thread_id": thread_id}}
        updated_state = app.invoke(checkpoint_state, config=config)

        print("DEBUG: updated_state after workflow (all scenes):")
        pprint.pprint(updated_state)

        if isinstance(updated_state, dict) and updated_state.get("error"):
            return Response({
                "status": "error",
                "message": f"Workflow error: {updated_state['error']}",
                "error_code": "workflow_node_error"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if updated_state is None:
            return Response({
                "status": "error",
                "message": "Workflow did not return any state. Please check your workflow logic.",
                "error_code": "workflow_no_state"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Defensive: ensure updated_state is a dict
        if not isinstance(updated_state, dict):
            return Response({
                "status": "error",
                "message": "Workflow returned invalid state type.",
                "error_code": "invalid_workflow_state"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        updated_scenes = updated_state.get('scenes', [])
        print(f"DEBUG: Found {len(updated_scenes)} scenes in updated_state")
        
        # Check if scenes were actually modified by comparing content
        scenes_actually_changed = False
        scenes_updated_count = 0
        updated_scene_data = []

        # Update all scenes in the database
        for updated_scene in updated_scenes:
            scene_number_db = updated_scene.get('scene_number')
            try:
                db_scene = models.Scene.objects.get(project=project, scene_number=scene_number_db)
                new_story = updated_scene.get('story')
                new_script = updated_scene.get('script') or new_story
                new_context = updated_scene.get('story_context') or new_story or new_script
                scene_title = updated_scene.get('title', db_scene.title)

                # Check if content actually changed
                old_script = db_scene.script
                if new_script and new_script != old_script:
                    scenes_actually_changed = True
                    # print(f"DEBUG: Scene {scene_number_db} content changed")
                    # print(f"Old: {old_script[:100]}...")
                    # print(f"New: {new_script[:100]}...")
                    
                    # Apply character placeholder enforcement and update
                    db_scene.script = new_script
                    db_scene.story_context = new_context or new_script
                    db_scene.title = scene_title or db_scene.title
                    db_scene.save()
                    scenes_updated_count += 1
                else:
                    print(f"DEBUG: Scene {scene_number_db} content unchanged")
                
                # Serialize the scene for response (whether changed or not)
                scene_serializer = serializers.SceneSerializer(db_scene)
                updated_scene_data.append(scene_serializer.data)
                
            except models.Scene.DoesNotExist:
                print(f"DEBUG: Scene {scene_number_db} not found in database")
                continue

        # Check if any scenes were actually changed
        if not scenes_actually_changed:
            return Response({
                "status": "error", 
                "message": "Workflow completed but no scenes were actually modified. This might indicate an issue with the workflow logic or the edit instructions were not processed correctly.",
                "error_code": "no_actual_changes",
                "debug_info": {
                    "edit_instructions": edit_instructions,
                    "scenes_in_response": len(updated_scenes),
                    "workflow_state_keys": list(updated_state.keys()) if isinstance(updated_state, dict) else "not_dict",
                    "edit_all_scenes_flag": checkpoint_state.get("edit_all_scenes"),
                    "rewrite_decision": checkpoint_state.get("rewrite_decision")
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        if scenes_updated_count == 0:
            return Response({
                "status": "error",
                "message": "No scenes were actually updated in the database.",
                "error_code": "scenes_update_failed",
                "debug_info": {
                    "scenes_found_in_response": len(updated_scenes),
                    "edit_instructions": edit_instructions
                }
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Update project title to reflect the edit
        base_title = project.title.split(' - ')[0]  # Remove any existing suffixes
        project.title = f"{base_title} - All Scenes Updated"
        project.save()

        project.refresh_from_db()
        project_serializer = serializers.ProjectSerializer(project)
        
        WorkflowCheckpoint.objects.filter(thread_id=thread_id).delete()
        
        return Response({
            "status": "success",
            "message": f"Successfully updated {scenes_updated_count} scenes coherently.",
            "data": {
                "project": project_serializer.data,
                "scenes_updated_count": scenes_updated_count,
                "total_scenes": project.scenes.count(),
                "edit_instructions_used": edit_instructions
            },
            "next_step": "review_script",
            "available_actions": ["accept_script", "edit_scene", "edit_all_scenes"]
        })

    except json.JSONDecodeError:
        return Response({
            "status": "error",
            "message": "Invalid JSON format in request body.",
            "error_code": "invalid_json"
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        import traceback
        print("Exception in EditAllScenes:", traceback.format_exc())
        return Response({
            "status": "error",
            "message": f"Internal server error: {str(e)}",
            "trace": traceback.format_exc(),
            "error_code": "internal_error"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])  
@permission_classes([IsAuthenticated])
def GetProjectStatus(request, project_id):
    """Get current project status and next available actions"""
    try:
        project = models.Project.objects.get(id=project_id, user=request.user)
        serializer = serializers.ProjectSerializer(project)
        
        current_step = 'completed' if 'Completed' in project.title else 'review_script'
        
        # Define next actions based on current step
        next_actions = {
            'generating_script': ['wait'],
            'review_script': ['accept_script', 'edit_scene'],
            'edit_scene': ['provide_edit_instructions'],
            'completed': ['view_results']
        }
        
        return Response({
            "project": serializer.data,
            "current_step": current_step,
            "available_actions": next_actions.get(current_step, [])
        })
        
    except models.Project.DoesNotExist:
        return Response(
            {"error": "Project not found"}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {"error": f"Internal server error: {str(e)}"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        
        