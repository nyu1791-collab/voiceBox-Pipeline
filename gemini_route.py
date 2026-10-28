"""Choose the configured Vertex project or the explicitly supplied Gemini API key."""
import os
from urllib.parse import quote


def request_target(model, api_key=None):
    project = os.environ.get('VOICEBOX_VERTEX_PROJECT', '').strip()
    if project:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        credentials.refresh(Request())
        url = ('https://aiplatform.googleapis.com/v1/projects/' + quote(project, safe='') +
               '/locations/global/publishers/google/models/' + quote(model, safe='') + ':generateContent')
        return url, {'Authorization': 'Bearer ' + credentials.token}
    key = api_key or os.environ.get('GEMINI_API_KEY')
    if not key:
        raise RuntimeError('Configure VOICEBOX_VERTEX_PROJECT and Google credentials, or GEMINI_API_KEY')
    return ('https://generativelanguage.googleapis.com/v1beta/models/' +
            quote(model, safe='') + ':generateContent'), {'x-goog-api-key': key}
