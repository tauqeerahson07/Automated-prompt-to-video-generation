from django.db import models

# Create your models here.
from django.contrib.auth.models import User
import uuid
from django.db import models

class WorkflowCheckpoint(models.Model):
    thread_id = models.TextField()
    version = models.IntegerField(default=1)
    state_json = models.JSONField()

    class Meta:
        unique_together = ("thread_id", "version") 

class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    title = models.CharField(max_length=255)
    concept = models.TextField()
    num_scenes = models.IntegerField()
    creativity_level = models.CharField(
        max_length=20,
        choices=[
            ('factual', 'Factual'), 
            ('creative', 'Creative'),   
            ('balanced', 'Balanced'), 
        ],
        default='balanced'
    )
    project_type = models.CharField(
        max_length=20,
        choices=[
            ('story', 'Story'),
            ('commercial', 'Commercial'),
        ],
        default='story'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

class Scene(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='scenes')
    title = models.CharField(max_length=255)
    scene_number = models.IntegerField()
    script = models.TextField()
    story_context = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scene_number']
        unique_together = ['project', 'scene_number']

    def __str__(self):
        return f"{self.project.title} - Scene {self.scene_number}"