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
from .main import build_workflow
from dotenv import load_dotenv
from .models import WorkflowCheckpoint
import os

load_dotenv()

def enforce_character_placeholder(text):
    # Replace "the character's" or "character’s" with "{character}'s"
    text = re.sub(r"\b(the )?character[’']s\b", r"{character}'s", text, flags=re.IGNORECASE)
    # Replace "the character" or "character" with "{character}"
    text = re.sub(r"\b(the )?character\b", r"{character}", text, flags=re.IGNORECASE)
    return text

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

                db_scene.script = enforce_character_placeholder(new_script or db_scene.script)
                db_scene.story_context = enforce_character_placeholder(new_context or db_scene.story_context)
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
        serializer = serializers.ProjectSerializer(project)
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