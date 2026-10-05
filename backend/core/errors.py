
class ValidationError(Exception):
    pass

class BusinessError(Exception):
    pass


class NotFoundError(Exception):
    pass


class AuthorizationError(Exception):
    pass


class SchemaPendingError(Exception):
    pass
