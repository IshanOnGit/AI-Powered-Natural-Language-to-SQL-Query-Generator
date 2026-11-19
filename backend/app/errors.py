class AppError(Exception):
    """Base for every error the app shows to the user.

    code    - short machine-readable name, e.g. "llm_timeout"
    message - friendly text that is safe to show (never contains secrets)
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
