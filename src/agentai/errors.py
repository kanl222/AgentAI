"""Application failures; only the transport decides how to present them."""


class ApplicationError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail
