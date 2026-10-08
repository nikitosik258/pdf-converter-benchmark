from .base import BaseCloudAdapter
from .ocr_space_adapter import OCRSpaceAdapter
from .nutrient_adapter import NutrientDataExtractionAdapter
from .mindee_adapter import MindeeOCRAdapter
from .adobe_extract_adapter import AdobeExtractAdapter
from .llamaparse_adapter import LlamaParseAdapter
from .azure_document_intelligence_adapter import AzureDocumentIntelligenceAdapter

__all__ = [
    "BaseCloudAdapter",
    "OCRSpaceAdapter",
    "NutrientDataExtractionAdapter",
    "MindeeOCRAdapter",
    "AdobeExtractAdapter",
    "LlamaParseAdapter",
    "AzureDocumentIntelligenceAdapter",
]
