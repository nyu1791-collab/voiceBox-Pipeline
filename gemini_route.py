"""Select one explicit Gemini route; never silently fall back to an API key."""
import os
from urllib.parse import quote


def request_target(model, api_key=None):
    route = os.environ.get('VOICEBOX_GEMINI_ROUTE', 'vertex').strip().lower()
    if route not in ('vertex', 'api_key'):
        raise RuntimeError('VOICEBOX_GEMINI_ROUTE must be vertex or api_key')
    project = os.environ.get('VOICEBOX_VERTEX_PROJECT', '').strip()
    if route == 'vertex':
        if not project:
            raise RuntimeError('VOICEBOX_VERTEX_PROJECT is required for the vertex route')
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        credentials.refresh(Request())
        url = ('https://aiplatform.googleapis.com/v1/projects/' + quote(project, safe='') +
               '/locations/global/publishers/google/models/' + quote(model, safe='') + ':generateContent')
        print(f'Gemini route=vertex project={project} location=global model={model}', flush=True)
        return url, {'Authorization': 'Bearer ' + credentials.token}
    key = api_key or os.environ.get('GEMINI_API_KEY')
    if not key:
        raise RuntimeError('GEMINI_API_KEY is required for the explicitly selected api_key route')
    print(f'Gemini route=api_key model={model}', flush=True)
    return ('https://generativelanguage.googleapis.com/v1beta/models/' +
            quote(model, safe='') + ':generateContent'), {'x-goog-api-key': key}
