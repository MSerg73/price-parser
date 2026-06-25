from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import RawItem
from ..workbook import WorkbookData


class SupplierParser(ABC):
    supplier_name: str

    @abstractmethod
    def matches(self, workbook: WorkbookData) -> bool:
        raise NotImplementedError

    @abstractmethod
    def extract(self, workbook: WorkbookData) -> list[RawItem]:
        raise NotImplementedError
