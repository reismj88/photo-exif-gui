from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET


@dataclass
class XMPData:
    title: str = ""
    caption: str = ""
    keywords: list[str] = field(default_factory=list)
    copyright: str = ""
    rating: int = 0  # 0 = unrated, 1-5


def _register_namespaces() -> None:
    ET.register_namespace("x", "adobe:ns:meta/")
    ET.register_namespace("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
    ET.register_namespace("xmp", "http://ns.adobe.com/xap/1.0/")


def _lang_alt(parent: ET.Element, tag: str, value: str) -> None:
    el = ET.SubElement(parent, tag)
    alt = ET.SubElement(el, "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Alt")
    li = ET.SubElement(alt, "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li")
    li.set("{http://www.w3.org/XML/1998/namespace}lang", "x-default")
    li.text = value


def build_xmp_xml(data: XMPData) -> str:
    _register_namespaces()

    xmpmeta = ET.Element("{adobe:ns:meta/}xmpmeta")
    rdf = ET.SubElement(xmpmeta, "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF")
    desc = ET.SubElement(rdf, "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
    desc.set("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")

    if data.title:
        _lang_alt(desc, "{http://purl.org/dc/elements/1.1/}title", data.title)

    if data.caption:
        _lang_alt(desc, "{http://purl.org/dc/elements/1.1/}description", data.caption)

    if data.copyright:
        _lang_alt(desc, "{http://purl.org/dc/elements/1.1/}rights", data.copyright)

    if data.keywords:
        subj = ET.SubElement(desc, "{http://purl.org/dc/elements/1.1/}subject")
        bag = ET.SubElement(subj, "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Bag")
        for kw in data.keywords:
            li = ET.SubElement(bag, "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li")
            li.text = kw.strip()

    if data.rating > 0:
        rating_el = ET.SubElement(desc, "{http://ns.adobe.com/xap/1.0/}Rating")
        rating_el.text = str(data.rating)

    ET.indent(xmpmeta, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(xmpmeta, encoding="unicode")


def write_xmp(image_path: Path, data: XMPData) -> Path:
    xmp_path = image_path.with_suffix(".xmp")
    xmp_path.write_text(build_xmp_xml(data), encoding="utf-8")
    return xmp_path


def read_xmp(image_path: Path) -> Optional[XMPData]:
    xmp_path = image_path.with_suffix(".xmp")
    if not xmp_path.exists():
        return None
    try:
        tree = ET.parse(xmp_path)
        root = tree.getroot()

        DC = "http://purl.org/dc/elements/1.1/"
        XMP_NS = "http://ns.adobe.com/xap/1.0/"
        RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

        def get_lang_alt(tag: str) -> str:
            el = root.find(f".//{{{DC}}}{tag}/{{{RDF}}}Alt/{{{RDF}}}li")
            return el.text.strip() if el is not None and el.text else ""

        def get_keywords() -> list[str]:
            items = root.findall(f".//{{{DC}}}subject/{{{RDF}}}Bag/{{{RDF}}}li")
            return [li.text.strip() for li in items if li.text]

        def get_rating() -> int:
            el = root.find(f".//{{{XMP_NS}}}Rating")
            try:
                return max(0, min(5, int(el.text))) if el is not None and el.text else 0
            except (ValueError, TypeError):
                return 0

        return XMPData(
            title=get_lang_alt("title"),
            caption=get_lang_alt("description"),
            copyright=get_lang_alt("rights"),
            keywords=get_keywords(),
            rating=get_rating(),
        )
    except Exception:
        return None
