from app.llm.base import BaseProvider, LLMRequest, LLMResponse
from app.llm.errors import LLMError


class FakeProvider(BaseProvider):
    """Canned replies for tests. No network, no model.

    `replies` is a list. Each item is either text to return or an LLMError
    to raise. Items are used in order; the last one repeats when used up.
    """

    name = "fake"

    def __init__(self, replies=("SELECT 1",), available: bool = True):
        super().__init__(sleep=lambda seconds: None)  # tests never really wait
        self._replies = list(replies) if not isinstance(replies, str) else [replies]
        self._available = available
        self.requests: list[LLMRequest] = []  # every request received

    def _generate_once(self, request: LLMRequest) -> LLMResponse:
        index = min(len(self.requests), len(self._replies) - 1)
        self.requests.append(request)
        reply = self._replies[index]
        if isinstance(reply, LLMError):
            raise reply
        return LLMResponse(text=reply, model="fake-model", provider=self.name)

    def is_available(self) -> bool:
        return self._available
