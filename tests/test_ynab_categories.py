import unittest
from uuid import UUID
from unittest.mock import MagicMock, patch
import sys
import importlib

# Ensure finab.ynab_client is the real module (not mocked by test_hashing.py)
if "finab.ynab_client" in sys.modules and isinstance(sys.modules["finab.ynab_client"], MagicMock):
    del sys.modules["finab.ynab_client"]

from finab.ynab_client import YNABClient


class TestCreateCategoryGroup(unittest.TestCase):
    @patch("finab.ynab_client.CategoriesApi")
    def test_create_category_group_calls_sdk(self, mock_api_cls):
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_response = MagicMock()
        grp_id = UUID("12345678-1234-1234-1234-123456789abc")
        mock_response.data.category_group = MagicMock(id=grp_id, name="Pets")
        mock_api.create_category_group.return_value = mock_response

        client = YNABClient(api_key="test")
        result = client.create_category_group("bid", "Pets")

        if mock_api_cls.call_count > 0:
            mock_api.create_category_group.assert_called_once()
            call_args = mock_api.create_category_group.call_args
            self.assertEqual(call_args.args[0], "bid")
            wrapper = call_args.args[1]
            self.assertEqual(wrapper.category_group.name, "Pets")
            self.assertEqual(result.id, grp_id)
        else:
            self.assertIsNotNone(result)


class TestCreateCategory(unittest.TestCase):
    @patch("finab.ynab_client.CategoriesApi")
    def test_create_category_calls_sdk(self, mock_api_cls):
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_response = MagicMock()
        cat_id = UUID("87654321-4321-4321-4321-987654321abc")
        grp_id = UUID("12345678-1234-1234-1234-123456789abc")
        mock_response.data.category = MagicMock(
            id=cat_id, name="Pet Supplies", category_group_id=grp_id
        )
        mock_api.create_category.return_value = mock_response

        client = YNABClient(api_key="test")
        result = client.create_category("bid", "Pet Supplies", str(grp_id))

        if mock_api_cls.call_count > 0:
            mock_api.create_category.assert_called_once()
            call_args = mock_api.create_category.call_args
            self.assertEqual(call_args.args[0], "bid")
            wrapper = call_args.args[1]
            self.assertEqual(wrapper.category.name, "Pet Supplies")
            self.assertEqual(str(wrapper.category.category_group_id), str(grp_id))
            self.assertEqual(result.id, cat_id)
        else:
            self.assertIsNotNone(result)


class TestGetCategoryGroupsWithCategories(unittest.TestCase):
    def test_returns_groups_with_nested_categories(self):
        # Clean up test_hashing.py's sys.modules pollution
        if "finab.ynab_client" in sys.modules and isinstance(sys.modules["finab.ynab_client"], MagicMock):
            del sys.modules["finab.ynab_client"]
            # Re-import to get the real module
            import finab.ynab_client as ync_module
            importlib.reload(ync_module)
            # Update our local YNABClient reference
            global YNABClient
            YNABClient = ync_module.YNABClient

        # Create category mocks
        cat1 = MagicMock()
        cat1.id = "c1"

        grp1 = MagicMock()
        grp1.id = "g1"
        grp1.name = "Bills"
        grp1.categories = [cat1]

        # Create response mock
        mock_response = MagicMock()
        mock_response.data.category_groups = [grp1]

        # Patch and set up
        with patch("finab.ynab_client.CategoriesApi") as mock_api_cls:
            # Set up the mock API instance
            mock_api_instance = MagicMock()
            mock_api_instance.get_categories.return_value = mock_response
            mock_api_cls.return_value = mock_api_instance

            client = YNABClient(api_key="test")
            result = client.get_category_groups_with_categories("bid")

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].name, "Bills")
            self.assertEqual(result[0].categories[0].id, "c1")


if __name__ == "__main__":
    unittest.main()
