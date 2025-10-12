from rest_framework import serializers
from .models import Project, Scene, Character
import base64

class SceneSerializer(serializers.ModelSerializer):
    project_title = serializers.CharField(source='project.title', read_only=True)

    class Meta:
        model = Scene
        fields = ['id', 'scene_number', 'script', 'story_context', 'created_at', 'project_title', 'title']


class ProjectSerializer(serializers.ModelSerializer):
    scenes = SceneSerializer(many=True, read_only=True)
    
    class Meta:
        model = Project
        fields = ['title', 'concept', 'num_scenes', 'creativity_level', 
                'created_at', 'updated_at', 'scenes']

class ProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['title', 'concept', 'num_scenes', 'creativity_level']
        
class CharacterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Character
        fields = ['name', 'trigger_word', 'image']