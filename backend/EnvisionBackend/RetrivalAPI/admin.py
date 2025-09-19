from django.contrib import admin
from .models import User, Project, Scene, Character
from django.utils.html import format_html
# Register your models here.
@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    list_display = ['name', 'trigger_word', 'image_preview', 'created_at']
    fields = ['name', 'trigger_word', 'image_file', 'image_preview']
    readonly_fields = ['image_preview']

    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-width: 25vw; max-height: 25vh;" />', obj.image)
        return "No image"
    image_preview.short_description = "Current Image"