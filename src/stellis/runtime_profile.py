"""Separate agent execution from model inference for each explicit profile."""
import os


def selected() -> str:
    value = os.environ.get("STELLIS_RUNTIME_PROFILE", "local").strip()
    if value not in {"local", "cloud", "direct"}:
        raise ValueError("STELLIS_RUNTIME_PROFILE must be local, cloud or direct")
    return value


def cloud() -> bool:
    return selected() == "cloud"


def direct() -> bool:
    """Application-owned inference; independent of cloud or local model hosting."""
    return agent_backend() == "direct"


def agent_backend() -> str:
    """Explicit execution choice, retaining existing installations' profile defaults."""
    profile = selected()
    value = os.environ.get("STELLIS_AGENT_BACKEND", {"direct":"direct", "cloud":"gateway", "local":"nemoclaw"}[profile]).strip()
    if value not in {"direct", "gateway", "nemoclaw"}:
        raise ValueError("STELLIS_AGENT_BACKEND must be direct, gateway or nemoclaw")
    return value


def deployment() -> str:
    value=os.environ.get('STELLIS_DEPLOYMENT','unspecified')
    if value not in {'cloud','self-hosted','unspecified'}:
        raise ValueError('STELLIS_DEPLOYMENT must be cloud or self-hosted')
    return value


def inference_provider() -> str:
    if not local_inference():
        return "Token Factory"
    provider = os.environ.get("STELLIS_LOCAL_INFERENCE_PROVIDER", "ollama")
    if provider not in {"ollama", "openai"}:
        raise ValueError("STELLIS_LOCAL_INFERENCE_PROVIDER must be ollama or openai")
    return "Ollama" if provider == "ollama" else "Local OpenAI-compatible server"


def remote_agents() -> bool:
    """Use HTTP: cloud calls managed OpenClaw; direct and demo call inference."""
    from stellis import storage
    return storage.in_demo() or agent_backend() != "nemoclaw"


def shared_provider_keys() -> bool:
    """The deployment supplies provider keys and models to every account, which configure none."""
    return os.environ.get("STELLIS_SHARED_PROVIDER_KEYS") == "1"


def operator() -> bool:
    """This account may change provider keys and model routes: every account unless keys are shared, then only the owner."""
    return not shared_provider_keys() or os.environ.get("STELLIS_ACCOUNT_ID", "1") == "1"


def local_inference() -> bool:
    # Unset preserves existing installations until an explicit profile is selected.
    selected()
    return "STELLIS_RUNTIME_PROFILE" in os.environ and selected() == "local"


def inference_url() -> str:
    if not local_inference():
        return "https://api.tokenfactory.nebius.com/v1"
    from urllib.parse import urlsplit
    url = os.environ.get("STELLIS_LOCAL_INFERENCE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Local inference must use a loopback endpoint on the application host")
    return url


def _default_text_model() -> str:
    if local_inference():
        from stellis import storage
        path = storage.base_dir() / "local-inference-model"
        if path.is_file():
            value = path.read_text().strip()
            if value and len(value) <= 200:
                return value
        return os.environ.get("STELLIS_LOCAL_INFERENCE_MODEL", "nemotron-3-nano:latest")
    return "nvidia/Nemotron-3_5-Lightning"


# Nemotron models offered first when choosing a Token Factory route, with what each suits.
RECOMMENDED = (
    ("nvidia/nemotron-3-super-120b-a12b", "Super 120B: best grounded answers"),
    ("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "Nano 30B: faster and lighter"),
    ("nvidia/Nemotron-3_5-Lightning", "Lightning: quick, easy exchanges"),
    ("nvidia/Nemotron-3-Ultra-550b-a55b", "Ultra 550B: largest"),
)


def model_id(role: str = "transcript") -> str:
    from stellis import model_routes
    if role not in model_routes.ROLES:
        raise ValueError("Unknown inference role")
    saved = model_routes.choices().get(role)
    if saved:
        return saved
    if role == "report" and model_routes.choices().get("agent"):
        return model_routes.choices()["agent"]
    if role == "naming" and not local_inference():
        return "nvidia/Nemotron-3_5-Lightning"
    if role in {"agent","report","care"} and not local_inference():
        # Super grounded research answers best; Ultra stays selectable. Care review stays here: on the
        # same records Nano 30B dropped a medicine and failed its own source checks (2026-10-29 trial).
        return "nvidia/nemotron-3-super-120b-a12b"
    # Summaries, answers from the person's own records and the answer check run on the lighter model;
    # only questions that search public sources need the largest one (owner, 2026-10-29).
    if role in {"summary", "answers", "review"} and not local_inference():
        return "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    if role == "research" and not local_inference():
        return "nvidia/nemotron-3-super-120b-a12b"
    if role == "categories" and not local_inference():
        return "zai-org/GLM-5.3-Flash"
    return _default_text_model()


def agent_execution() -> str:
    """Describe the agent execution path, including the isolated demo exception."""
    from stellis import storage
    if storage.in_demo():
        return "Direct Ollama (demo)" if local_inference() else "Direct Token Factory (demo)"
    if direct():
        return "Application inference with " + inference_provider()
    if agent_backend() == "gateway":
        return "Managed HTTP agent gateway"
    return "NemoClaw command in the configured sandbox" if os.environ.get("STELLIS_NEMOCLAW_SANDBOX") else "NemoClaw not configured"


def label() -> str:
    """Name the configured version separately from its model provider."""
    from stellis import storage
    if storage.in_demo():
        return agent_execution()
    return ("Local inference" if local_inference() else "Cloud inference") + " · " + agent_execution()
