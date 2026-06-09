from django.db import models

class CMSPage(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True, db_index=True)
    content = models.TextField(help_text="HTML or Markdown body content for the page.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cms_pages'
        verbose_name = 'CMS Page'
        verbose_name_plural = 'CMS Pages'

    def __str__(self):
        return f"{self.title} ({self.slug})"
