from src.core.config import config


class ModelManager:
    def __init__(self, config):
        self.config = config

    def map_claude_model_to_openai(self, claude_model: str) -> str:
        """Map a Claude model tier (Haiku/Sonnet/Opus) to a configured upstream model.

        Models that don't match a known Claude tier name are passed through
        unchanged, so upstream model IDs can be requested directly.
        """
        model_lower = claude_model.lower()
        if "haiku" in model_lower:
            return self.config.small_model
        if "opus" in model_lower:
            return self.config.big_model
        if "sonnet" in model_lower:
            return self.config.middle_model
        return claude_model


model_manager = ModelManager(config)
