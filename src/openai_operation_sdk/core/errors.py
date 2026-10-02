class OpenAIOperationError(Exception):
    """Stable error code and uncertainty marker; no raw request secrets."""
    def __init__(self, code: str, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


TeamError = OpenAIOperationError
AccountError = OpenAIOperationError
