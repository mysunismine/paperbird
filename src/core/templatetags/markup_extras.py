import markdown as md
from django import template
from django.template.defaultfilters import stringfilter
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter(name='markdown')
@stringfilter
def markdown(value):
    """
    Converts a Markdown string to HTML.
    """
    # Use extensions for more features, e.g., fenced code blocks
    return mark_safe(md.markdown(value, extensions=['fenced_code', 'tables']))
