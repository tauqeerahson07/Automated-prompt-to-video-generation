from django.urls import path, include
from . import views

urlpatterns = [
    path('get-all-characters/', views.getCharacters, name='get_all_characters'),
    path('select-character/', views.setCharacter, name='select_character'),
    # get scenes
    path('generate-scenes/', views.generateScenes, name='generate_scenes'),
    
    # path('generate-image-prompts/', views.generate_image_prompts, name='generate_image_prompts'),
    # Project management endpoints
    path('list-projects/', views.listProjects, name='list_projects'),
    path('create-project/', views.CreateProject, name='create_project'),
    
    # Script review and editing endpoints
    path('review-script/', views.ReviewScript, name='review_script'),
    path('edit-scene/', views.EditScene, name='edit_scene'),
    path('edit-all-scenes/', views.EditAllScenes, name='edit_all_scenes'),
    
    # generate images endpoint
    path('generate-images/', views.generate_images, name='generate_images'),
    
    # edit image(s) endpoint
    path('edit-image/', views.edit_image, name='edit_images'),
    path('edit-all-images/', views.edit_all_images, name='edit_all_images'),
    
    # Generate video endpoint
    path('generate-video/', views.CreateVideo_2, name='generate_video'),
    # Project status endpoint
    path('project-status/<uuid:project_id>/', views.GetProjectStatus,name='project_status'),
    
    path('project/scenes/', views.get_project_and_scenes, name='get_project_and_scenes'),
    

]