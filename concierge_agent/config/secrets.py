"""Secure runtime configuration and Google Cloud Secret Manager loader."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from google.api_core import exceptions as gcp_exceptions
from google.cloud import secretmanager
from pydantic import BaseModel, Field, SecretStr

logger = logging.getLogger(__name__)


class RuntimeSettings(BaseModel):
    """Validated runtime settings for the Executive Travel Concierge."""

    app_env: str = Field(default="local", description="Execution environment name.")
    project_id: str = Field(default="local-dev-project", description="Google Cloud project ID.")
    location: str = Field(default="us-central1", description="Default Google Cloud region.")
    use_vertex_ai: bool = Field(default=True, description="Route ADK calls via Vertex AI.")
    fast_worker_model: str = Field(default="gemini-2.5-flash", description="Fast specialist model.")
    reasoning_coordinator_model: str = Field(default="gemini-2.5-pro", description="Coordinator model.")
    session_db_url: str = Field(default="sqlite+aiosqlite:///./concierge_sessions.db")
    otel_service_name: str = Field(default="executive-travel-concierge")
    enable_cloud_trace: bool = Field(default=False)
    gemini_api_key: Optional[SecretStr] = Field(default=None)
    travel_provider_api_key: SecretStr = Field(default=SecretStr("sandbox-mode-unauthenticated"))


class SecretResolver:
    """Fetches and caches secrets from Google Cloud Secret Manager."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        app_env: Optional[str] = None,
        cache_ttl_seconds: int = 300,
        client: Optional[secretmanager.SecretManagerServiceClient] = None,
    ) -> None:
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self.app_env = (app_env or os.environ.get("APP_ENV", "local")).lower()
        self.cache_ttl_seconds = cache_ttl_seconds
        self._client = client
        self._cache: dict[str, tuple[SecretStr, float]] = {}

    @property
    def should_query_secret_manager(self) -> bool:
        """Returns True when running in cloud production or explicitly forced."""
        force_sm = os.environ.get("FORCE_SECRET_MANAGER", "false").lower() == "true"
        if force_sm:
            return bool(self.project_id)
        return bool(self.project_id) and self.app_env not in {"local", "test"}

    def resolve_secret(
        self,
        secret_id: str,
        env_fallback_var: Optional[str] = None,
        version: str = "latest",
        required: bool = False,
    ) -> Optional[SecretStr]:
        """Resolves a secret by ID from Secret Manager with safe local fallback.

        Args:
            secret_id: Identifier of the secret in Google Cloud Secret Manager.
            env_fallback_var: Environment variable name consulted in local/test mode.
            version: Secret version alias (defaults to 'latest').
            required: If True, raises RuntimeError when the secret cannot be resolved.

        Returns:
            Wrapped SecretStr value if found, or None when optional and absent.
        """
        now = time.monotonic()
        cached = self._cache.get(secret_id)
        if cached and (now - cached[1]) < self.cache_ttl_seconds:
            return cached[0]

        if self.should_query_secret_manager:
            if self._client is None:
                self._client = secretmanager.SecretManagerServiceClient()
            resource_name = f"projects/{self.project_id}/secrets/{secret_id}/versions/{version}"
            try:
                response = self._client.access_secret_version(request={"name": resource_name})
                secret_value = SecretStr(response.payload.data.decode("utf-8").strip())
                self._cache[secret_id] = (secret_value, now)
                logger.info("Resolved secret", extra={"event": "secret_resolved", "secret_id": secret_id})
                return secret_value
            except (gcp_exceptions.NotFound, gcp_exceptions.PermissionDenied, gcp_exceptions.GoogleAPICallError) as exc:
                logger.warning("Secret lookup failed", extra={"event": "secret_lookup_failed", "secret_id": secret_id})
                if self.app_env not in {"local", "test"} and required:
                    raise RuntimeError(f"Required secret '{secret_id}' unavailable in Secret Manager.") from exc

        lookup_var = env_fallback_var or secret_id.upper().replace("-", "_")
        env_value = os.environ.get(lookup_var, "").strip()
        if env_value:
            secret_value = SecretStr(env_value)
            self._cache[secret_id] = (secret_value, now)
            return secret_value

        if required:
            raise RuntimeError(f"Missing required secret '{secret_id}' or env var '{lookup_var}'.")
        return None


def load_runtime_settings(resolver: Optional[SecretResolver] = None) -> RuntimeSettings:
    """Builds validated RuntimeSettings using SecretResolver."""
    active_resolver = resolver or SecretResolver()
    gemini_key = active_resolver.resolve_secret(
        secret_id=os.environ.get("GEMINI_API_KEY_SECRET_ID", "concierge-gemini-api-key"),
        env_fallback_var="GEMINI_API_KEY",
    )
    provider_key = active_resolver.resolve_secret(
        secret_id=os.environ.get("TRAVEL_PROVIDER_API_KEY_SECRET_ID", "concierge-travel-provider-api-key"),
        env_fallback_var="TRAVEL_PROVIDER_API_KEY",
    ) or SecretStr("sandbox-mode-unauthenticated")

    return RuntimeSettings(
        app_env=os.environ.get("APP_ENV", "local"),
        project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", "local-dev-project"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        use_vertex_ai=os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true",
        session_db_url=os.environ.get("SESSION_DB_URL", "sqlite+aiosqlite:///./concierge_sessions.db"),
        otel_service_name=os.environ.get("OTEL_SERVICE_NAME", "executive-travel-concierge"),
        enable_cloud_trace=os.environ.get("ENABLE_CLOUD_TRACE", "false").lower() == "true",
        gemini_api_key=gemini_key,
        travel_provider_api_key=provider_key,
    )