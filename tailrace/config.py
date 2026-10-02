"""Settings come from the environment. Locally, ../../.env is read as a fallback."""
import os
from pathlib import Path


def _load_dotenv() -> None:
    here = Path(__file__).resolve().parent.parent
    for candidate in (here / ".env", here.parent.parent / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class Settings:
    @property
    def paypal_client_id(self): return env("PAYPAL_CLIENT_ID")
    @property
    def paypal_secret(self): return env("PAYPAL_SECRET")
    @property
    def paypal_api(self): return env("PAYPAL_API", "https://api-m.sandbox.paypal.com")
    @property
    def database_url(self): return env("DATABASE_URL", "postgresql://postgres:pg@localhost:5439/payrail")
    @property
    def workflow_slug(self): return env("WORKFLOW_SLUG", "tailrace-flow")
    @property
    def webhook_id(self): return env("PAYPAL_WEBHOOK_ID")
    @property
    def sender_email(self): return env("SENDER_EMAIL", "sb-mixsn53098231@business.example.com")
    @property
    def anthropic_key(self): return env("ANTHROPIC_API_KEY")
    @property
    def anthropic_model(self): return env("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    @property
    def bedrock_model(self): return env("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    @property
    def aws_region(self): return env("AWS_REGION", "us-east-1")
    @property
    def agent_provider(self):
        """anthropic | bedrock | policy. AGENT_PROVIDER forces one; otherwise first one that has credentials."""
        forced = env("AGENT_PROVIDER")
        if forced:
            return forced
        if self.anthropic_key:
            return "anthropic"
        if env("AWS_ACCESS_KEY_ID") or env("AWS_PROFILE") or env("AWS_BEARER_TOKEN_BEDROCK") or Path.home().joinpath(".aws/credentials").exists():
            return "bedrock"
        return "policy"
    @property
    def local_dev(self): return env("RENDER_USE_LOCAL_DEV", "").lower() == "true"
    @property
    def render_key(self): return env("RENDER_API_KEY")


settings = Settings()
