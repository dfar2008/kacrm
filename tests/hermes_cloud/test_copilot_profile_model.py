"""Copilot uses the same owned profile as the model picker and dispatch."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from hermes_cloud.app.config_store import InMemoryConfigStore
from hermes_cloud.app.copilot import routes


@pytest.mark.parametrize('path,payload', [
    ('/api/copilot/crm/send', {'module': 'leads', 'record_id': 36}),
    ('/api/copilot/scoped/send', {'scope': 'schema', 'context': {'module': 'leads'}}),
])
@pytest.mark.parametrize('explicit', [False, True])
def test_copilot_resolves_profile_model_and_dispatches_same_profile(path, payload, explicit):
    app = FastAPI()
    app.state.config = SimpleNamespace(internal_key='test')
    app.state.profile_store = MagicMock()
    profile = SimpleNamespace(id='admin', user_id='1', is_active=True)
    app.state.profile_store.ensure_default_profile.return_value = profile
    app.state.profile_store.get_profile.return_value = profile
    app.state.config_store = InMemoryConfigStore()
    app.state.config_store.set_user_model(user_id='1', profile_id='admin',
        model='test-model', api_key='test-key', base_url='https://example.invalid',
        providers_json='{"providers":{"test":{"models":["test-model"],"api_key":"test-key","base_url":"https://example.invalid"}}}')
    app.dependency_overrides[routes._require_user] = lambda: {'id': 1}
    @contextmanager
    def db_session():
        yield MagicMock()
    dispatch = AsyncMock(return_value=SimpleNamespace(status_code=200, reply_text='ok', tool_executions=[], usage={}, model='test-model', session_id='test', raw_body=b'{}'))
    with patch.object(routes, 'db_session', db_session), patch.object(routes, 'ModulesDb'), patch.object(routes, 'emit'), patch.object(routes, 'dispatch_to_hermes', dispatch):
        routes.register_routes(app)
        response = TestClient(app).post(path, json={**payload, 'message': 'test', 'model': 'test::test-model'}, headers={'X-Profile-Id': 'admin'} if explicit else {})
    assert response.status_code == 200, response.text
    assert response.json()['fallback'] is False
    assert dispatch.call_args.kwargs['timeout_sec'] == 240.0
    assert dispatch.call_args.kwargs['profile_id'] == 'admin'
    assert dispatch.call_args.kwargs['body_extras']['model'] == 'test-model'


def test_explicit_profile_must_be_active_and_owned():
    from starlette.requests import Request
    app = FastAPI()
    app.state.profile_store = MagicMock()
    for profile in [None, SimpleNamespace(user_id='2', is_active=True), SimpleNamespace(user_id='1', is_active=False)]:
        app.state.profile_store.get_profile.return_value = profile
        request = Request({'type': 'http', 'app': app, 'headers': [(b'x-profile-id', b'other')]})
        with pytest.raises(HTTPException) as exc:
            routes._copilot_profile_id(request, '1')
        assert exc.value.status_code == 403
