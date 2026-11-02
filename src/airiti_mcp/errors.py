class AiritiError(Exception):
    """Base class. Message is shown to the model/user as-is, so keep it explicit."""


class AiritiNetworkError(AiritiError):
    """Could not reach the site, timed out, or got a non-2xx HTTP status."""


class AiritiParseError(AiritiError):
    """Got a response, but it did not have the structure we expect (site changed, or wrong page)."""
