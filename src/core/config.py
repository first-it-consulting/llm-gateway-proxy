import hmac
import os
import sys


class Config:
    """Runtime configuration, loaded from environment variables (see .env.example)."""

    def __init__(self):
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")

        # Shared secret clients must present to use this proxy. Independent of
        # OPENAI_API_KEY, which authenticates the proxy to the upstream.
        self.proxy_api_key = os.environ.get("PROXY_API_KEY")
        if not self.proxy_api_key:
            print("Warning: PROXY_API_KEY not set. Client API key validation is disabled.")

        self.openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.azure_api_version = os.environ.get("AZURE_API_VERSION")  # set only for Azure OpenAI

        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8082"))
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")

        self.request_timeout = int(os.environ.get("REQUEST_TIMEOUT", "90"))
        self.max_retries = int(os.environ.get("MAX_RETRIES", "2"))

        # Mutual TLS to the upstream. Either a combined PEM (cert+key in one
        # file) via CLIENT_CERT_PATH alone, or a separate cert/key pair via
        # both CLIENT_CERT_PATH and CLIENT_KEY_PATH.
        self.client_cert_path = os.environ.get("CLIENT_CERT_PATH")
        self.client_key_path = os.environ.get("CLIENT_KEY_PATH")
        self.ca_bundle_path = os.environ.get("CA_BUNDLE_PATH")
        self.ssl_verify = os.environ.get("SSL_VERIFY", "true").strip().lower() not in (
            "false",
            "0",
            "no",
        )

    def get_client_cert(self):
        """Return the httpx `cert` value for mTLS, or None if not configured."""
        if self.client_cert_path and self.client_key_path:
            return (self.client_cert_path, self.client_key_path)
        if self.client_cert_path:
            return self.client_cert_path
        return None

    def get_verify(self):
        """Return the httpx `verify` value: a custom CA bundle path, or a bool."""
        if self.ca_bundle_path:
            return self.ca_bundle_path
        return self.ssl_verify

    def validate_client_api_key(self, client_api_key):
        """Validate a client-supplied key against PROXY_API_KEY (constant-time)."""
        if not self.proxy_api_key:
            return True
        return hmac.compare_digest(client_api_key, self.proxy_api_key)

    def get_custom_headers(self):
        """Collect CUSTOM_HEADER_* environment variables into a headers dict."""
        custom_headers = {}
        for env_key, env_value in os.environ.items():
            if env_key.startswith("CUSTOM_HEADER_"):
                header_name = env_key[len("CUSTOM_HEADER_"):]
                if header_name:
                    custom_headers[header_name.replace("_", "-")] = env_value
        return custom_headers


try:
    config = Config()
except Exception as e:
    print(f"Configuration error: {e}")
    sys.exit(1)
