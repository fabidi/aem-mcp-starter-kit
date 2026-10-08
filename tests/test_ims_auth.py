"""
Tests for Adobe IMS OAuth 2.0 Provider.
"""

import pytest
from unittest.mock import patch, MagicMock
from aem_mcp.auth.ims import AdobeImsAuthProvider


def test_ims_provider_unconfigured():
    provider = AdobeImsAuthProvider(client_id="", client_secret="")
    assert provider.is_configured() is False
    with pytest.raises(ValueError, match="not configured"):
        provider.get_bearer_token()


def test_ims_provider_configured():
    provider = AdobeImsAuthProvider(client_id="test_client", client_secret="test_secret")
    assert provider.is_configured() is True


@patch("urllib.request.urlopen")
def test_ims_provider_token_fetch_and_cache(mock_urlopen):
    # Mock Adobe IMS endpoint JSON response
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"access_token": "mock_token_abc123", "token_type": "bearer", "expires_in": 3600}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    provider = AdobeImsAuthProvider(client_id="test_client", client_secret="test_secret")
    
    # First call: fetches from IMS endpoint
    token = provider.get_bearer_token()
    assert token == "mock_token_abc123"
    assert mock_urlopen.call_count == 1

    # Second call: uses cache, does NOT call urlopen again
    cached_token = provider.get_bearer_token()
    assert cached_token == "mock_token_abc123"
    assert mock_urlopen.call_count == 1

    # Check headers
    headers = provider.get_auth_headers()
    assert headers["Authorization"] == "Bearer mock_token_abc123"
    assert headers["x-api-key"] == "test_client"
