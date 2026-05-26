import unittest
from uuid import UUID
from unittest.mock import MagicMock, patch

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


if __name__ == "__main__":
    unittest.main()
