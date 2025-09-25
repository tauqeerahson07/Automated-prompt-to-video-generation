from django.urls import path, include
from . import views

urlpatterns = [
    path('get-all-characters/', views.getCharacters, name='get_all_characters'),
    # get scenes
    path('generate-scenes/', views.generateScenes, name='generate_scenes'),
    
    path('generate-image-prompts/', views.generate_image_prompts, name='generate_image_prompts'),
    # Project management endpoints
    path('list-projects/', views.listProjects, name='list_projects'),
    path('create-project/', views.CreateProject, name='create_project'),
    
    # Script review and editing endpoints
    path('review-script/', views.ReviewScript, name='review_script'),
    path('edit-scene/', views.EditScene, name='edit_scene'),
    
    # Project status endpoint
    path('project-status/<uuid:project_id>/', views.GetProjectStatus,name='project_status'),
    
    path('project/scenes/', views.get_project_and_scenes, name='get_project_and_scenes'),
    

]