"""Exercise metadata failures without starting MongoDB, Redis or HTTP servers."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from fastapi import HTTPException


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "flash_ticket_catalog_metadata", PROJECT_ROOT / "services/catalog/app/main.py"
)
catalog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = catalog
SPEC.loader.exec_module(catalog)


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def find(self, *arguments):
        return self

    def sort(self, *arguments):
        return self

    def limit(self, *arguments):
        return self

    def __iter__(self):
        return iter(self.documents)


class Mongo:
    def __init__(self, documents):
        self.collection = Cursor(documents)

    def __getitem__(self, name):
        return SimpleNamespace(events=self.collection)


class Cache:
    def get(self, key):
        return None

    def setex(self, *arguments):
        return True


def request_for(documents):
    state = SimpleNamespace(mongo=Mongo(documents), redis=Cache())
    return SimpleNamespace(app=SimpleNamespace(state=state))


class CatalogMetadataTests(unittest.TestCase):
    def test_valid_published_metadata_is_returned(self):
        records = catalog.load_metadata(request_for([{
            "_id": "11111111-1111-4111-8111-111111111111",
            "title": "Test Event",
            "banner_key": "banners/demo.svg",
        }]))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Test Event")

    def test_malformed_source_documents_return_a_controlled_unavailable_response(self):
        for document in (
            {"_id": "11111111-1111-4111-8111-111111111111"},
            None,
            {"_id": "invalid-id", "title": "Test Event", "banner_key": "banners/demo.svg"},
        ):
            with self.subTest(document=document):
                with self.assertRaises(HTTPException) as caught:
                    catalog.load_metadata(request_for([document]))
                self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
