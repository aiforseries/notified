from __future__ import annotations

from datetime import date

from ..models import Item


class Source:
    """Base class for one regulator feed.

    Subclasses set the class attributes and implement fetch(). fetch() must:
      * return Items dated on/after `since` (items with unknown date may be included),
      * stop paginating once it passes `since` (be polite to govt servers),
      * raise an exception on total failure (the runner records it per-source;
        one broken site never stops the others).
    """
    id: str = ""            # unique snake_case id, e.g. "sebi_circulars"
    regulator: str = ""     # short label shown in UI/Excel, e.g. "SEBI"
    name: str = ""          # human name, e.g. "SEBI - Circulars"
    group: str = ""         # UI group: "ICAI & Audit" | "MCA" | "Direct Tax" | "GST & Customs" | "SEBI" | "RBI" | "Labour" | "Data Protection"
    homepage: str = ""      # listing page, for the user to check manually if the adapter breaks
    enabled_by_default: bool = True  # False = shown in UI but unticked (e.g. site restricts automated access)

    def fetch(self, since: date) -> list[Item]:
        raise NotImplementedError

    def resolve_pdf(self, item: Item) -> str:
        """Called only when the user opted to download PDFs and item.pdf_url is empty.
        Override to fetch the detail page and return the PDF link (or "")."""
        return ""

    def item(self, **kw) -> Item:
        return Item(source_id=self.id, regulator=self.regulator, **kw)
