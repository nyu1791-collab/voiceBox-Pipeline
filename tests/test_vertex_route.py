import io
import json
import os
from unittest.mock import Mock, patch


def test_vertex_takes_precedence_over_api_key():
    from gemini_route import request_target

    credentials = Mock(token='fixture-token')
    with patch.dict(os.environ, {'VOICEBOX_VERTEX_PROJECT': 'fixture-project', 'GEMINI_API_KEY': 'unused'}), \
         patch('google.auth.default', return_value=(credentials, None)):
        url, headers = request_target('gemini-3.5-transcribe')
    assert '/projects/fixture-project/locations/global/' in url
    assert url.endswith('/models/gemini-3.5-transcribe:generateContent')
    assert headers == {'Authorization': 'Bearer fixture-token'}
    credentials.refresh.assert_called_once()


def test_transcribe_and_dialogue_use_shared_vertex_route(tmp_path):
    from gemini_transcribe import transcribe
    from gemini_workflow import call_gemini

    audio = tmp_path / 'speech.mp3'
    audio.write_bytes(b'audio')
    responses = [
        {'candidates': [{'content': {'parts': [{'audioTranscription': {'words': [{'word': '音声'}]}}]}}]},
        {'candidates': [{'content': {'parts': [{'text': '{"ok": true}'}]}}]},
    ]
    target = ('https://example.test/vertex', {'Authorization': 'Bearer fixture-token'})
    with patch('gemini_transcribe.request_target', return_value=target), \
         patch('gemini_workflow.request_target', return_value=target), \
         patch('gemini_transcribe.urlopen', return_value=io.BytesIO(json.dumps(responses[0]).encode())) as audio_request, \
         patch('gemini_workflow.urlopen', return_value=io.BytesIO(json.dumps(responses[1]).encode())) as text_request:
        assert transcribe(audio)['words'][0]['word'] == '音声'
        assert call_gemini('gemini-3.8-flash', [], system='test')[0]['ok']
    for send in (audio_request, text_request):
        assert send.call_args.args[0].full_url == target[0]
    assert json.loads(audio_request.call_args.args[0].data)['contents'][0]['role'] == 'user'
