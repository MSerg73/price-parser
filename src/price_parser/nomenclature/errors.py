class NomenclatureError(Exception):
    """Base error for nomenclature search."""


class CatalogValidationError(NomenclatureError):
    """Raised when the catalog is inconsistent or unsafe."""


class InventoryValidationError(NomenclatureError):
    """Raised when an inventory file contains invalid records."""


class SearchValidationError(NomenclatureError):
    """Raised when a search request cannot be validated."""
