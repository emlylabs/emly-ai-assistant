"""File ingestion → chunk → embed → Qdrant upsert.

Loads a file, chunks it via langchain, and hands the chunks to
``RAGManager.upsert(bot_id, file_id, chunks)``. All Qdrant access is
delegated to ``agents.rag_manager`` — see that module for the tenancy
boundary contract.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Dict, Iterator, List, Union

from langchain_community.document_loaders import (
    BSHTMLLoader,
    CSVLoader,
    Docx2txtLoader,
    PyPDFLoader,
    TextLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from agents.rag_manager import get_rag_manager
from config import CHUNK_OVERLAP, CHUNK_SIZE

log = logging.getLogger(__name__)
log.setLevel("INFO")


class DataService:
    """File-to-vector pipeline."""

    def __init__(self) -> None:
        self.rag = get_rag_manager()
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )

    # ------------------------------------------------------------------
    # File extraction
    # ------------------------------------------------------------------
    def _extract_text_from_file(self, filepath: str, file_extension: str) -> List[Document]:
        ext = file_extension.lower()
        if ext == ".pdf":
            try:
                documents = PyPDFLoader(filepath).load()
            except Exception as e:
                log.warning("PyPDFLoader failed (%s); falling back to PyMuPDFLoader", e)
                from langchain_community.document_loaders import PyMuPDFLoader

                documents = PyMuPDFLoader(filepath).load()
        elif ext == ".docx":
            documents = Docx2txtLoader(filepath).load()
        elif ext in (".html", ".htm"):
            documents = CustomBSHTMLLoader(filepath).load()
        elif ext == ".txt":
            documents = TextLoader(filepath, autodetect_encoding=True).load()
        elif ext == ".csv":
            documents = CSVLoader(filepath).load()
        else:
            try:
                documents = TextLoader(filepath, autodetect_encoding=True).load()
            except UnicodeDecodeError:
                documents = TextLoader(filepath, encoding="latin-1").load()

        return self.text_splitter.split_documents(documents)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def process_file(
        self,
        filepath: str,
        bot_id: str,
        file_id: str | None = None,
        document_type: str | None = None,
        source_url: str | None = None,
    ) -> Dict[str, Any]:
        """Embed a file's chunks into the bot's vector space.

        ``file_id`` defaults to a fresh UUID if not provided. Phase 5 wires
        the persistent ``emly_files.id`` here so re-index / delete operate
        on a stable identifier. ``document_type`` and ``source_url`` are
        attached to every chunk's metadata so the chat surface can label
        citations (e.g. "from a product page" vs "from a support article").
        """
        _, file_extension = os.path.splitext(filepath)
        file_id = file_id or str(uuid.uuid4())

        try:
            text_chunks = self._extract_text_from_file(filepath, file_extension)
            extra_meta: Dict[str, Any] = {}
            if document_type:
                extra_meta["document_type"] = document_type
            if source_url:
                extra_meta["source_url"] = source_url
            payload_chunks = [
                {
                    "text": chunk.page_content,
                    "metadata": {
                        **chunk.metadata,
                        "source": filepath,
                        "filename": os.path.basename(filepath),
                        **extra_meta,
                    },
                }
                for chunk in text_chunks
            ]
            written = self.rag.upsert(bot_id=bot_id, file_id=file_id, chunks=payload_chunks)

            return {
                "filename": filepath,
                "file_id": file_id,
                "chunks_processed": written,
                "bot_id": bot_id,
            }
        except Exception as e:
            log.exception("Error processing file %s", filepath)
            raise ValueError(f"Error processing file: {str(e)}") from e


class CustomBSHTMLLoader(BSHTMLLoader):
    def lazy_load(self) -> Iterator[Document]:
        from bs4 import BeautifulSoup

        with open(self.file_path, "r", encoding=self.open_encoding) as f:
            try:
                soup = BeautifulSoup(f, **self.bs_kwargs)
            except UnicodeEncodeError:
                log.warning("UnicodeEncodeError using default parser; switching to html.parser")
                self.bs_kwargs["features"] = "html.parser"
                f.seek(0)
                soup = BeautifulSoup(f, **self.bs_kwargs)

        text = soup.get_text(self.get_text_separator)
        title = str(soup.title.string) if soup.title else ""
        first_image = soup.find("img")
        image_url = first_image["src"] if first_image else ""

        metadata: Dict[str, Union[str, None]] = {
            "source": str(self.file_path),
            "title": title,
            "og": json.dumps(self.get_og_object(soup)),
            "payload": json.dumps(self.get_payload_object(soup)),
            "image": "" if image_url is None else image_url,
        }

        yield Document(page_content=text, metadata=metadata)

    def get_og_object(self, soup) -> dict:
        og_metadata: Dict[str, str] = {}
        for meta in soup.find_all("meta", property=True):
            property_name = meta.get("property")
            content = meta.get("content", "")
            if property_name and property_name.startswith("og:"):
                og_metadata[property_name.replace("og:", "")] = content
        return og_metadata

    def get_payload_object(self, soup) -> dict:
        data_metadata: Dict[str, str] = {}
        for meta in soup.find_all("meta", property=True):
            property_name = meta.get("property")
            content = meta.get("content", "")
            if property_name and property_name.startswith("payload:"):
                data_metadata[property_name.replace("payload:", "")] = content
        return data_metadata
