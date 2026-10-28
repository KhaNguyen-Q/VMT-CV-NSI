"""Delegated Microsoft Entra authentication with an OS-encrypted MSAL cache."""

from .paths import get_token_cache_path


GRAPH_SCOPES = ["https://graph.microsoft.com/Files.ReadWrite.All"]


class GraphAuthenticationError(RuntimeError):
    """Raised when delegated authentication cannot produce an access token."""


class GraphAuthenticator:
    def __init__(self, config):
        self._config = config

    def get_access_token(self):
        try:
            import msal
            from msal_extensions import (
                PersistedTokenCache,
                build_encrypted_persistence,
            )
        except ImportError as exc:
            raise GraphAuthenticationError(
                "Graph upload dependencies are missing; install requirements.txt."
            ) from exc

        cache_path = get_token_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            persistence = build_encrypted_persistence(str(cache_path))
        except RuntimeError as exc:
            raise GraphAuthenticationError(
                "An OS-backed encrypted token cache is unavailable on this device."
            ) from exc

        token_cache = PersistedTokenCache(persistence)
        application = msal.PublicClientApplication(
            client_id=self._config.client_id,
            authority=(
                "https://login.microsoftonline.com/"
                f"{self._config.tenant_id}"
            ),
            token_cache=token_cache,
        )

        for account in application.get_accounts():
            result = application.acquire_token_silent(GRAPH_SCOPES, account=account)
            if result and result.get("access_token"):
                return result["access_token"]

        result = application.acquire_token_interactive(
            scopes=GRAPH_SCOPES,
            redirect_uri="http://localhost",
        )
        if result and result.get("access_token"):
            return result["access_token"]
        details = result or {}
        error = details.get("error", "authentication_failed")
        description = details.get("error_description", "No access token was returned.")
        raise GraphAuthenticationError(f"{error}: {description}")
