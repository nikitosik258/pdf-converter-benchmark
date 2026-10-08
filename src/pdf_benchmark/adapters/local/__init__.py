from .docling_adapter import DoclingAdapter
from .pdfminer_adapter import PdfMinerAdapter
from .mineru_adapter import MinerUAdapter
from .pdfplumber_adapter import PdfPlumberAdapter
from .pymupdf_adapter import PyMuPDFAdapter

LOCAL_ADAPTERS = {
    "pymupdf": PyMuPDFAdapter,
    "pdfplumber": PdfPlumberAdapter,
    "docling": DoclingAdapter,
    "pdfminer": PdfMinerAdapter,
    "mineru": MinerUAdapter,
}


def create_local_adapter(name: str, config=None):
    try:
        cls = LOCAL_ADAPTERS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown local adapter {name!r}; expected one of {sorted(LOCAL_ADAPTERS)}") from exc
    return cls(config=config)


__all__ = [
    "PyMuPDFAdapter",
    "PdfPlumberAdapter",
    "DoclingAdapter",
    "MarkerAdapter",
    "MinerUAdapter",
    "LOCAL_ADAPTERS",
    "create_local_adapter",
]
