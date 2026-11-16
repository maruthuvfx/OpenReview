"""
Conform package for OpenReview.
Provides EDL, XML, AAF parsers and smart media relinking.
"""
from openreview.core.conform.edl_parser import EDLParser, EDLEvent
from openreview.core.conform.xml_parser import XMLParser
from openreview.core.conform.aaf_parser import AAFParser
from openreview.core.conform.relinker import MediaRelinker, RelinkReport

__all__ = [
    "EDLParser",
    "EDLEvent",
    "XMLParser",
    "AAFParser",
    "MediaRelinker",
    "RelinkReport",
]
