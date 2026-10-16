class RouteGISError(Exception):
    """Base error with a message safe to show to end users."""


class ConfigurationError(RouteGISError):
    """Raised when project or application configuration is invalid."""


class CadReadError(RouteGISError):
    """Raised when a CAD drawing cannot be read or converted."""


class RouteAssemblyError(RouteGISError):
    """Raised when selected CAD entities cannot form one route."""


class CrsError(RouteGISError):
    """Raised when a CRS is missing, unsuitable, or cannot be transformed."""


class ProviderError(RouteGISError):
    """Raised for recoverable external spatial-provider failures."""


class RetryableProviderError(ProviderError):
    """A server timeout/resource failure for which a smaller query may work."""


class ExportError(RouteGISError):
    """Raised when an output dataset cannot be written safely."""
