from django.shortcuts import render
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
from .services.image_prompt_generation import ImagePromptGenerator
from .main import build_workflow
from dotenv import load_dotenv
from .models import WorkflowCheckpoint
import os

load_dotenv()
################# Helper Functions #################
def enforce_character_placeholder(text):
    # Replace "the character's" or "character’s" with "{character}'s"
    text = re.sub(r"\b(the )?character[’']s\b", r"{character}'s", text, flags=re.IGNORECASE)
    # Replace "the character" or "character" with "{character}"
    text = re.sub(r"\b(the )?character\b", r"{character}", text, flags=re.IGNORECASE)
    return text

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
        character, error_response = validate_character_selection(request)
        if character is None:
            return error_response
        
        # Use character's trigger_word if not provided in request
        trigger_word = character.trigger_word or data.get('trigger_word', '').strip()

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
        
        

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def generate_image_prompts(request):
    """
    API endpoint to generate image prompts from scenes data
    
    Expects: { "project_id": "..." }
    Retrieves scenes from database and generates image prompts
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
        
        
        trigger_word = project.trigger_word or data.get('trigger_word', '')
        
        # Prepare scenes data in the format expected by ImagePromptGenerator
        scenes_data = []
        for scene in scenes:
            scene_prompt = scene.story_context or scene.script
            scenes_data.append({
                "scene_number": scene.scene_number,
                "scene_title": scene.title,
                "final_prompt": scene_prompt,
                "trigger_word": trigger_word
            })
        
        # Format data as complete API response structure
        formatted_data = {
            "status": "success",
            "data": {
                "project_id": str(project.id),
                "project_title": project.title,
                "original_prompt": project.concept,
                "trigger_word": trigger_word,
                "total_scenes": len(scenes_data),
                "scenes": scenes_data
            }
        }
        
        # Generate image prompts using the formatted data
        generator = ImagePromptGenerator()  # Create instance
        result = generator.generate_image_prompt(formatted_data)
        
        if result.get("success", False):
            return Response(result, status=status.HTTP_200_OK)
        else:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
            
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
# Create your views here.
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

                db_scene.script = new_script or db_scene.script
                db_scene.story_context = new_context or db_scene.story_context
                db_scene.title = updated_scene.get('title', db_scene.title)
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
                    db_scene.title = updated_scene.get('title', db_scene.title)
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
        
        