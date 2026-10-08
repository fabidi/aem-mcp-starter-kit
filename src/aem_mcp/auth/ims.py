"""
Adobe IMS OAuth 2.0 Authentication Provider for AEM as a Cloud Service (AEMaaCS).

Implements the official Server-to-Server OAuth 2.0 credential flow
recommended by Adobe Developer Console.
"""

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from aem_mcp.config import CONFIG

IMS_ENDPOINT = "https://ims-na1.adobelogin.com/ims/token/v3"
DEFAULT_SCOPE = "openid,AdobeID,read_organizations,additional_info.projectedProductContext"


class AdobeImsAuthProvider:
    """Manages Adobe IMS bearer tokens with automatic caching and renewal."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        ims_endpoint: str = IMS_ENDPOINT,
        scope: str = DEFAULT_SCOPE,
    ):
        self.client_id = client_id or CONFIG.ims_client_id
        self.client_secret = client_secret or CONFIG.ims_client_secret
        self.ims_endpoint = ims_endpoint
        self.scope = scope
        self._cached_token: str | None = None
        self._token_expiry: float = 0

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def get_bearer_token(self) -> str:
        """Return a valid cached access token or fetch a new one if expired."""
        now = time.time()
        # 5-minute safety buffer before expiration
        if self._cached_token and now < (self._token_expiry - 300):
            return self._cached_token

        if not self.is_configured():
            raise ValueError(
                "Adobe IMS credentials not configured. "
                "Set AEM_IMS_CLIENT_ID and AEM_IMS_CLIENT_SECRET environment variables."
            )

        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.scope,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.ims_endpoint,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "aem-mcp-starter-kit/0.1.0"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        token = body.get("access_token")
        if not token:
            raise RuntimeError(f"Adobe IMS response did not contain an access_token: {body}")

        expires_in = int(body.get("expires_in", 86400))
        self._cached_token = token
        self._token_expiry = now + expires_in

        return token

    def get_auth_headers(self) -> dict[str, str]:
        """Return HTTP headers ready to attach to AEM Cloud Service requests."""
        token = self.get_bearer_token()
        return {
            "Authorization": f"Bearer {token}",
            "x-api-key": self.client_id,
        }
